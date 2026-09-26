"""Atomic operator writes, scoped reads and a persistent deletion ledger.

No network, auto-approval, assistant-derived facts, or restore operation.
"""

import hashlib
import json
import math
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from virtual_ai.memory.base import StoreError
from virtual_ai.memory.sqlite_store import _error, _text


def subject_key(scope, viewer):
    parts = [_text(scope, 128), _text(viewer.platform, 64), _text(viewer.user_id, 256)]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def split_document(text, limit=900):
    """Keep headings, paragraphs and rule/exception blocks together.

    Oversize blocks are rejected rather than silently dropping an exception.
    """
    blocks = [p.strip() for p in text.replace("\r\n", "\n").split("\n\n") if p.strip()]
    heading, current, result = "", "", []
    for block in blocks:
        if block.startswith("#") and "\n" not in block:
            if current:
                result.append(current)
                current = ""
            heading = block
            continue
        if len(block) + len(heading) + 1 > limit:
            raise StoreError("paragraph_too_large_split_manually")
        combined = (
            (current + "\n\n" + block).strip()
            if current
            else (heading + "\n" + block).strip()
        )
        if len(combined) > limit:
            result.append(current)
            current = (heading + "\n" + block).strip()
        else:
            current = combined
    if current:
        result.append(current)
    if not result:
        raise StoreError("empty_document")
    return result


class RAGStore:
    def __init__(self, path, *, now=time.time):
        self.path = Path(path).resolve()
        self.ledger = self.path.with_name(self.path.name + ".revocations.sqlite3")
        self.now = now
        if self.path.exists() != self.ledger.exists():
            raise StoreError("deletion_ledger_missing_or_orphaned")
        new = not self.path.exists()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                if new:
                    for statement in (
                        Path(__file__)
                        .with_name("schema.sql")
                        .read_text(encoding="utf-8")
                        .split(";")
                    ):
                        if statement.strip():
                            db.execute(statement)
                    identity = str(uuid4())
                    db.execute("CREATE TABLE identity (id TEXT NOT NULL)")
                    db.execute("INSERT INTO identity VALUES (?)", (identity,))
                    db.execute("CREATE TABLE revocations.identity (id TEXT NOT NULL)")
                    db.execute(
                        "INSERT INTO revocations.identity VALUES (?)", (identity,)
                    )
                    db.execute(
                        "CREATE TABLE revocations.deleted (kind TEXT NOT NULL, id TEXT NOT NULL, PRIMARY KEY(kind,id))"
                    )
                self.verify(db)
                self.purge(db)
                db.commit()
        except OSError:
            raise StoreError("storage_io") from None

    @contextmanager
    def connection(self):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=0.15)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA max_page_count=16384")
            db.execute("ATTACH DATABASE ? AS revocations", (str(self.ledger),))
            db.execute("PRAGMA revocations.secure_delete=ON")
            db.execute("PRAGMA revocations.journal_mode=DELETE")
            db.execute("PRAGMA revocations.max_page_count=16384")
            deadline = time.monotonic() + 0.3
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            yield db
        except sqlite3.Error as exc:
            raise _error(exc) from None
        finally:
            if db is not None:
                db.close()

    def verify(self, db):
        if [tuple(r) for r in db.execute("SELECT version FROM meta")] != [(1,)]:
            raise StoreError("unsupported_schema")
        main = db.execute("SELECT id FROM identity").fetchall()
        ledger = db.execute("SELECT id FROM revocations.identity").fetchall()
        if len(main) != 1 or len(ledger) != 1 or main[0][0] != ledger[0][0]:
            raise StoreError("deletion_ledger_mismatch")

    def purge(self, db):
        before = db.total_changes
        db.execute(
            "DELETE FROM subjects WHERE id IN (SELECT id FROM revocations.deleted WHERE kind='subject')"
        )
        db.execute(
            "DELETE FROM documents WHERE id IN (SELECT id FROM revocations.deleted WHERE kind='document') OR expires<=?",
            (self.now(),),
        )
        db.execute(
            "DELETE FROM memories WHERE id IN (SELECT id FROM revocations.deleted WHERE kind='memory') OR expires<=?",
            (self.now(),),
        )
        db.execute("DELETE FROM delivery WHERE expires<=?", (self.now(),))
        if before != db.total_changes:
            db.execute("UPDATE meta SET revision=revision+1")

    @contextmanager
    def transaction(self, *, write=False):
        if not self.path.exists() or not self.ledger.exists():
            raise StoreError("deletion_ledger_missing_or_orphaned")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.verify(db)
            self.purge(db)
            yield db
            if write:
                db.execute("UPDATE meta SET revision=revision+1")
            db.commit()

    def expiry(self, days):
        if type(days) is not int or not 1 <= days <= 30:
            raise StoreError("invalid_retention")
        return self.now() + days * 86400

    def consent(self, scope, viewer, *, storage, retrieval, public):
        if any(type(v) is not bool for v in (storage, retrieval, public)):
            raise StoreError("invalid_input")
        subject = subject_key(scope, viewer)
        with self.transaction(write=True) as db:
            if db.execute(
                "SELECT 1 FROM revocations.deleted WHERE kind='subject' AND id=?",
                (subject,),
            ).fetchone():
                raise StoreError("deleted_subject")
            db.execute(
                "INSERT INTO subjects VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET storage=excluded.storage,retrieval=excluded.retrieval,public=excluded.public",
                (subject, storage, retrieval, public),
            )
        return subject

    def forget(self, scope, viewer):
        subject = subject_key(scope, viewer)
        with self.transaction(write=True) as db:
            db.execute(
                "INSERT OR IGNORE INTO revocations.deleted VALUES ('subject',?)",
                (subject,),
            )
            db.execute("DELETE FROM subjects WHERE id=?", (subject,))

    def ingest(
        self,
        scope,
        *,
        source,
        title,
        version,
        content,
        confirmed=False,
        days=30,
        fact_key="",
    ):
        if confirmed is not True:
            raise StoreError("approval_required")
        scope, source, title, version = (
            _text(scope, 128),
            _text(source, 256),
            _text(title, 200),
            _text(version, 80),
        )
        content = _text(content, 60000)
        if fact_key:
            _text(fact_key, 128)
        expires = self.expiry(days)
        chunks = split_document(content)
        digest = hashlib.sha256(content.encode()).hexdigest()
        with self.transaction(write=True) as db:
            old = db.execute(
                "SELECT id,digest,state FROM documents WHERE scope=? AND source=? AND version=?",
                (scope, source, version),
            ).fetchone()
            if old:
                if old["digest"] != digest:
                    raise StoreError("immutable_version")
                if old["state"] != "active":
                    raise StoreError("superseded_version_requires_new_version")
                return old["id"]
            db.execute(
                "UPDATE documents SET state='superseded' WHERE scope=? AND source=?",
                (scope, source),
            )
            document_id = str(uuid4())
            db.execute(
                "INSERT INTO documents VALUES (?,?,?,?,?,?,?,'active',?,?)",
                (
                    document_id,
                    scope,
                    source,
                    title,
                    version,
                    content,
                    digest,
                    self.now(),
                    expires,
                ),
            )
            db.executemany(
                "INSERT INTO chunks VALUES (?,?,?,?,?)",
                [
                    (str(uuid4()), document_id, i, c, fact_key)
                    for i, c in enumerate(chunks)
                ],
            )
        return document_id

    def delete(self, kind, identifier):
        if kind not in ("document", "memory"):
            raise StoreError("invalid_input")
        table = {"document": "documents", "memory": "memories"}[kind]
        with self.transaction(write=True) as db:
            db.execute(
                "INSERT OR IGNORE INTO revocations.deleted VALUES (?,?)",
                (kind, identifier),
            )
            db.execute(f"DELETE FROM {table} WHERE id=?", (identifier,))

    def candidate(
        self,
        scope,
        viewer,
        response_id,
        session_id,
        text,
        kind,
        *,
        days=30,
        valid=lambda: True,
        evidence_text=None,
    ):
        subject = subject_key(scope, viewer)
        text = _text(text, 1000)
        evidence_text = (
            _text(evidence_text, 1000) if evidence_text is not None else text
        )
        if kind not in ("preference", "name"):
            raise StoreError("invalid_input")
        expiry = self.expiry(days)
        with self.transaction() as db:
            consent = db.execute(
                "SELECT storage FROM subjects WHERE id=?", (subject,)
            ).fetchone()
            if not consent or not consent[0] or not valid():
                return None
            existing = db.execute(
                "SELECT id FROM memories WHERE subject=? AND (response_id=? OR (text=? AND state IN ('candidate','active')))",
                (subject, response_id, text),
            ).fetchone()
            if existing:
                return existing[0]
            identifier = str(uuid4())
            db.execute(
                "INSERT INTO memories VALUES (?,?,?,?,?,?,?,'candidate',?,?,NULL)",
                (
                    identifier,
                    subject,
                    _text(response_id, 128),
                    _text(session_id, 128),
                    kind,
                    text,
                    evidence_text,
                    self.now(),
                    expiry,
                ),
            )
            if not valid():
                raise StoreError("cancelled")
        return identifier

    def review(self, identifier, *, approve, supersedes=None):
        if type(approve) is not bool:
            raise StoreError("invalid_input")
        with self.transaction(write=True) as db:
            row = db.execute(
                "SELECT * FROM memories WHERE id=? AND state='candidate'", (identifier,)
            ).fetchone()
            if not row:
                raise StoreError("candidate_missing")
            if not approve:
                db.execute("DELETE FROM memories WHERE id=?", (identifier,))
                db.execute(
                    "INSERT OR IGNORE INTO revocations.deleted VALUES ('memory',?)",
                    (identifier,),
                )
                return
            allowed = db.execute(
                "SELECT storage FROM subjects WHERE id=?", (row["subject"],)
            ).fetchone()
            if not allowed or not allowed[0]:
                raise StoreError("consent_required")
            if supersedes:
                previous = db.execute(
                    "SELECT id FROM memories WHERE id=? AND subject=? AND kind=? AND state='active'",
                    (supersedes, row["subject"], row["kind"]),
                ).fetchone()
                if not previous:
                    raise StoreError("invalid_replacement")
                db.execute(
                    "UPDATE memories SET state='superseded' WHERE id=?", (supersedes,)
                )
            db.execute(
                "UPDATE memories SET state='active',supersedes=? WHERE id=?",
                (supersedes, identifier),
            )

    def cancel_capture(self, response_id):
        with self.transaction(write=True) as db:
            rows = db.execute(
                "SELECT id FROM memories WHERE response_id=?", (response_id,)
            ).fetchall()
            for row in rows:
                db.execute(
                    "INSERT OR IGNORE INTO revocations.deleted VALUES ('memory',?)",
                    (row[0],),
                )
            db.execute("DELETE FROM memories WHERE response_id=?", (response_id,))

    def delivered(self, scope, viewer, response_id, *, displayed, playback, days=30):
        if (
            playback
            not in (
                "not_started",
                "started",
                "completed",
                "failed",
                "cancelled",
                "unknown",
            )
            or type(displayed) is not bool
        ):
            raise StoreError("invalid_delivery")
        subject = subject_key(scope, viewer)
        with self.transaction() as db:
            consent = db.execute(
                "SELECT storage FROM subjects WHERE id=?", (subject,)
            ).fetchone()
            if consent and consent[0]:
                db.execute(
                    "INSERT INTO delivery VALUES (?,?,?,?,?) ON CONFLICT(response_id) DO UPDATE SET displayed=excluded.displayed,playback=excluded.playback",
                    (response_id, subject, displayed, playback, self.expiry(days)),
                )

    def snapshot(self, scope, viewer, *, knowledge=True, memory=False, public=True):
        subject = subject_key(scope, viewer)
        with self.transaction() as db:
            revision = db.execute("SELECT revision FROM meta").fetchone()[0]
            rows = []
            if knowledge:
                rows.extend(
                    dict(r)
                    for r in db.execute(
                        "SELECT c.id,c.text,c.fact_key,d.id AS source_id,d.title,d.version,d.source,d.expires,'knowledge' AS kind FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.scope=? AND d.state='active' ORDER BY c.id LIMIT 2001",
                        (scope,),
                    )
                )
            if memory:
                rows.extend(
                    dict(r)
                    for r in db.execute(
                        "SELECT m.id,m.text,m.kind AS fact_key,m.id AS source_id,'viewer memory' AS title,'1' AS version,'user_statement' AS source,m.expires,'memory' AS kind FROM memories m JOIN subjects s ON s.id=m.subject WHERE s.id=? AND s.storage=1 AND s.retrieval=1 AND (?=0 OR s.public=1) AND m.state='active' ORDER BY m.created DESC LIMIT 201",
                        (subject, public),
                    )
                )
            if len(rows) > 2000 or sum(r["kind"] == "memory" for r in rows) > 200:
                raise StoreError("retrieval_capacity")
            return revision, rows

    def revision(self):
        with self.transaction() as db:
            return db.execute("SELECT revision FROM meta").fetchone()[0]

    def inspect(self, scope, viewer=None):
        """Private explicit operator read, never a normal application log."""
        with self.transaction() as db:
            if viewer is None:
                return [
                    dict(r)
                    for r in db.execute(
                        "SELECT id,source,title,version,state,expires FROM documents WHERE scope=?",
                        (scope,),
                    )
                ]
            subject = subject_key(scope, viewer)
            return {
                "memories": [
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM memories WHERE subject=?", (subject,)
                    )
                ],
                "delivery": [
                    dict(r)
                    for r in db.execute(
                        "SELECT response_id,displayed,playback FROM delivery WHERE subject=?",
                        (subject,),
                    )
                ],
            }

    def save_vectors(self, revision, model, vectors):
        with self.transaction(write=True) as db:
            if db.execute("SELECT revision FROM meta").fetchone()[0] != revision:
                raise StoreError("source_changed_reindex")
            dimensions = set()
            for identifier, vector in vectors.items():
                validate_vector(vector)
                dimensions.add(len(vector))
                if len(dimensions) != 1:
                    raise StoreError("embedding_dimensions")
                db.execute(
                    "INSERT OR REPLACE INTO vectors VALUES (?,?,?)",
                    (identifier, _text(model, 128), json.dumps(vector)),
                )

    def vectors(self, model, identifiers):
        # IDs are already scoped by snapshot; no cross-subject vector collection.
        with self.transaction() as db:
            result = {}
            for identifier in identifiers:
                row = db.execute(
                    "SELECT embedding FROM vectors WHERE chunk_id=? AND model=?",
                    (identifier, model),
                ).fetchone()
                if row:
                    try:
                        result[identifier] = json.loads(row[0])
                        validate_vector(result[identifier])
                    except ValueError:
                        raise StoreError("invalid_embedding") from None
            return result


def validate_vector(vector):
    if (
        not isinstance(vector, list)
        or not 1 <= len(vector) <= 4096
        or any(
            type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1e6
            for v in vector
        )
        or not any(vector)
    ):
        raise StoreError("invalid_embedding")
