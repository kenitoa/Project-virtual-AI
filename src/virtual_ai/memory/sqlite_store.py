"""Opt-in local SQLite store. No automatic collection, inference, or audio hooks."""

import hashlib
import json
import math
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from virtual_ai.memory.base import StoreError
from virtual_ai.safety import contains_personal_data, contains_secret

MIGRATION = Path(__file__).with_name("migrations") / "001_initial.sql"
TABLES = ("turns", "memories", "session_summaries")


def _error(exc):
    code = getattr(exc, "sqlite_errorcode", 0) & 255
    reason = {
        sqlite3.SQLITE_BUSY: "locked",
        sqlite3.SQLITE_LOCKED: "locked",
        sqlite3.SQLITE_FULL: "capacity",
        sqlite3.SQLITE_CORRUPT: "corrupt",
        sqlite3.SQLITE_NOTADB: "corrupt",
        sqlite3.SQLITE_INTERRUPT: "timeout",
    }.get(code, "storage_io")
    return StoreError(reason)


def _text(value, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise StoreError("invalid_input")
    if contains_secret(value) or contains_personal_data(value):
        raise StoreError("sensitive_input")
    return value


def _subject(user_id):
    return hashlib.sha256(("local:" + _text(user_id, 256)).encode()).hexdigest()


class SQLiteStore:
    def __init__(
        self,
        path=".local/memory.sqlite3",
        *,
        enabled=False,
        timeout=0.2,
        max_pages=65536,
        now=time.time,
    ):
        if (
            type(enabled) is not bool
            or not 0 < timeout <= 2
            or type(max_pages) is not int
            or not 32 <= max_pages <= 65536
        ):
            raise StoreError("invalid_configuration")
        self.path = Path(path).resolve()
        self.ledger = self.path.with_name(self.path.name + ".deletions.sqlite3")
        self.enabled, self.timeout, self.max_pages, self.now = (
            enabled,
            timeout,
            max_pages,
            now,
        )
        if not enabled:
            return
        if self.path.exists() != self.ledger.exists():
            raise StoreError("deletion_ledger_missing_or_orphaned")
        new = not self.path.exists()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not new:
                self._backup_before_migration()
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                self._migrate(db)
                if new:
                    identity = str(uuid4())
                    db.execute("INSERT INTO store_meta VALUES (?)", (identity,))
                    db.execute("CREATE TABLE deletions.store_meta (id TEXT NOT NULL)")
                    db.execute(
                        "INSERT INTO deletions.store_meta VALUES (?)", (identity,)
                    )
                    db.execute(
                        "CREATE TABLE deletions.tombstones (subject TEXT PRIMARY KEY, deleted_at REAL NOT NULL)"
                    )
                self._verify(db)
                self._purge(db)
                db.commit()
        except OSError:
            raise StoreError("storage_io") from None

    def _backup_before_migration(self):
        """Back up schema 1 before opening the migration transaction."""
        source = None
        try:
            source = sqlite3.connect(
                self.path.as_uri() + "?mode=ro", uri=True, timeout=self.timeout
            )
            versions = [
                r[0]
                for r in source.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ]
            if versions != [1]:
                return
            target = self.path.with_name(
                self.path.name + ".pre-migration-" + str(uuid4()) + ".sqlite3"
            )
            with target.open("xb"):
                pass
            destination = sqlite3.connect(target)
            try:
                self._copy(source, destination)
            finally:
                destination.close()
        except sqlite3.Error as exc:
            raise _error(exc) from None
        finally:
            if source is not None:
                source.close()

    @contextmanager
    def _connection(self):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=self.timeout)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA secure_delete=ON")
            # DELETE journals support atomic transactions across the attached ledger.
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute(f"PRAGMA max_page_count={self.max_pages:d}")
            db.execute("ATTACH DATABASE ? AS deletions", (str(self.ledger),))
            db.execute("PRAGMA deletions.journal_mode=DELETE")
            db.execute("PRAGMA deletions.secure_delete=ON")
            db.execute(f"PRAGMA deletions.max_page_count={self.max_pages:d}")
            deadline = time.monotonic() + 2
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            yield db
        except sqlite3.Error as exc:
            raise _error(exc) from None
        finally:
            if db is not None:
                db.close()

    def _migrate(self, db):
        paths = [MIGRATION, Path(__file__).with_name("migrations") / "002_sessions.sql"]
        scripts = [p.read_text(encoding="utf-8") for p in paths]
        expected = [
            (i + 1, hashlib.sha256(s.replace("\r\n", "\n").encode()).hexdigest())
            for i, s in enumerate(scripts)
        ]
        db.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL)"
        )
        rows = [
            tuple(r)
            for r in db.execute(
                "SELECT version, checksum FROM schema_migrations ORDER BY version"
            )
        ]
        if rows != expected[: len(rows)] or len(rows) > len(expected):
            raise StoreError("unsupported_schema")
        for index in range(len(rows), len(scripts)):
            for statement in scripts[index].split(";"):
                if statement.strip():
                    db.execute(statement)
            db.execute("INSERT INTO schema_migrations VALUES (?,?)", expected[index])

    def _verify(self, db):
        for schema in ("main", "deletions"):
            if db.execute(f"PRAGMA {schema}.quick_check").fetchone()[0] != "ok":
                raise StoreError("corrupt")
        main = db.execute("SELECT id FROM store_meta").fetchall()
        ledger = db.execute("SELECT id FROM deletions.store_meta").fetchall()
        if len(main) != 1 or len(ledger) != 1 or main[0][0] != ledger[0][0]:
            raise StoreError("deletion_ledger_mismatch")

    def _purge(self, db):
        db.execute(
            "DELETE FROM viewers WHERE id IN (SELECT subject FROM deletions.tombstones)"
        )
        for table in TABLES:
            db.execute(f"DELETE FROM {table} WHERE expires_at<=?", (self.now(),))

    @contextmanager
    def _transaction(self):
        # Never recreate a missing ledger for an existing store.
        if not self.path.exists() or not self.ledger.exists():
            raise StoreError("deletion_ledger_missing_or_orphaned")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._verify(db)
            self._purge(db)
            yield db
            db.commit()

    def _allowed(self, db, subject):
        if (
            db.execute(
                "SELECT 1 FROM viewers WHERE id=? AND allowed=1", (subject,)
            ).fetchone()
            is None
        ):
            raise StoreError("consent_required")

    def _expiry(self, seconds):
        if (
            type(seconds) not in (float, int)
            or not math.isfinite(seconds)
            or not 0 < seconds <= 30 * 86400
        ):
            raise StoreError("invalid_retention")
        return self.now() + seconds

    def allow(self, user_id):
        if not self.enabled:
            return False
        subject = _subject(user_id)
        with self._transaction() as db:
            if db.execute(
                "SELECT 1 FROM deletions.tombstones WHERE subject=?", (subject,)
            ).fetchone():
                raise StoreError("deleted_subject")
            db.execute(
                "INSERT INTO viewers VALUES (?, 'local', 1) ON CONFLICT(id) DO UPDATE SET allowed=1",
                (subject,),
            )
        return True

    def forget(self, user_id):
        if not self.enabled:
            return False
        subject = _subject(user_id)
        with self._transaction() as db:
            db.execute(
                "INSERT INTO deletions.tombstones VALUES (?,?) ON CONFLICT(subject) DO NOTHING",
                (subject, self.now()),
            )
            db.execute("DELETE FROM viewers WHERE id=?", (subject,))
        return True

    def add_turn(
        self,
        user_id,
        input_text,
        final,
        *,
        source="local_operator",
        displayed=False,
        playback="not_started",
        retention=86400,
        session_id="",
    ):
        if not self.enabled:
            return None
        if (
            source != "local_operator"
            or type(displayed) is not bool
            or playback
            not in ("not_started", "completed", "failed", "cancelled", "unknown")
        ):
            raise StoreError("policy_denied")
        subject, turn_id = _subject(user_id), str(uuid4())
        values = (
            turn_id,
            subject,
            _text(input_text),
            _text(final),
            int(displayed),
            playback,
            self.now(),
            self._expiry(retention),
            _text(session_id, 128) if session_id else "",
        )
        with self._transaction() as db:
            self._allowed(db, subject)
            db.execute("INSERT INTO turns VALUES (?,?,?,?,?,?,?,?,?)", values)
        return turn_id

    def delivery(self, user_id, turn_id, *, displayed, playback):
        if not self.enabled:
            return False
        if type(displayed) is not bool or playback not in (
            "completed",
            "failed",
            "cancelled",
            "unknown",
        ):
            raise StoreError("invalid_delivery")
        with self._transaction() as db:
            subject = _subject(user_id)
            self._allowed(db, subject)
            row = db.execute(
                "SELECT displayed, playback FROM turns WHERE id=? AND viewer_id=?",
                (turn_id, subject),
            ).fetchone()
            if row is None or row[1] != "not_started" or (row[0] and not displayed):
                raise StoreError("invalid_delivery_transition")
            db.execute(
                "UPDATE turns SET displayed=?, playback=? WHERE id=? AND viewer_id=?",
                (int(displayed), playback, turn_id, subject),
            )
        return True

    def add_fact(
        self,
        user_id,
        fact,
        evidence,
        *,
        source="local_operator",
        confirmed=False,
        retention=86400,
    ):
        if not self.enabled:
            return None
        if source != "local_operator" or confirmed is not True:
            raise StoreError("policy_denied")
        subject, fact_id = _subject(user_id), str(uuid4())
        values = (
            fact_id,
            subject,
            _text(fact),
            source,
            _text(evidence),
            self.now(),
            self._expiry(retention),
        )
        with self._transaction() as db:
            self._allowed(db, subject)
            db.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?)", values)
        return fact_id

    def add_summary(
        self,
        user_id,
        summary,
        turn_ids,
        *,
        source="local_operator",
        confirmed=False,
        retention=86400,
        session_id="",
        replace_existing=False,
    ):
        if not self.enabled:
            return None
        if (
            source != "local_operator"
            or confirmed is not True
            or not isinstance(turn_ids, list)
            or not 1 <= len(turn_ids) <= 20
        ):
            raise StoreError("policy_denied")
        subject, summary_id = _subject(user_id), str(uuid4())
        summary = _text(summary)
        session_id = _text(session_id, 128) if session_id else ""
        expiry = self._expiry(retention)
        with self._transaction() as db:
            self._allowed(db, subject)
            for turn_id in turn_ids:
                row = db.execute(
                    "SELECT expires_at FROM turns WHERE id=? AND viewer_id=? AND session_id=?",
                    (_text(turn_id, 256), subject, session_id),
                ).fetchone()
                if row is None:
                    raise StoreError("evidence_missing")
                expiry = min(expiry, row[0])
            if replace_existing:
                db.execute(
                    "DELETE FROM session_summaries WHERE viewer_id=? AND session_id=?",
                    (subject, session_id),
                )
            db.execute(
                "INSERT INTO session_summaries VALUES (?,?,?,?,?,?,?)",
                (
                    summary_id,
                    subject,
                    summary,
                    json.dumps(turn_ids),
                    self.now(),
                    expiry,
                    session_id,
                ),
            )
        return summary_id

    def retrieve(self, user_id, session_id, keywords):
        if not self.enabled or not keywords:
            return []
        keywords = [_text(k, 80).lower() for k in keywords[:5]]
        subject = _subject(user_id)
        result = []
        with self._transaction() as db:
            for table, field, scope in (
                ("memories", "fact", "user"),
                ("session_summaries", "summary", "session"),
            ):
                condition = " OR ".join(
                    f"instr(lower({field}), ?) > 0" for _ in keywords
                )
                params = [subject, *keywords]
                clause = ""
                if scope == "session":
                    if not session_id:
                        continue
                    clause = " AND session_id=?"
                    params.append(_text(session_id, 128))
                rows = db.execute(
                    f"SELECT id, {field}, evidence FROM {table} WHERE viewer_id=? AND viewer_id IN (SELECT id FROM viewers WHERE allowed=1) AND ({condition}){clause} ORDER BY created_at DESC LIMIT 3",
                    params,
                )
                result.extend(
                    {"scope": scope, "id": r[0], "text": r[1], "evidence": r[2]}
                    for r in rows
                )
        return result

    def summary_inputs(self, user_id, session_id):
        if not self.enabled:
            return []
        with self._transaction() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT id, input FROM turns WHERE viewer_id=? AND session_id=? ORDER BY created_at DESC LIMIT 5",
                    (_subject(user_id), _text(session_id, 128)),
                )
            ]

    def lookup(self, user_id):
        if not self.enabled:
            return {}
        subject = _subject(user_id)
        with self._transaction() as db:
            return {
                table: [
                    dict(r)
                    for r in db.execute(
                        f"SELECT * FROM {table} WHERE viewer_id=? ORDER BY created_at LIMIT 100",
                        (subject,),
                    )
                ]
                for table in TABLES
            }

    def purge_expired(self):
        if self.enabled:
            with self._transaction():
                pass

    def backup(self, path):
        if not self.enabled:
            return False
        self.purge_expired()
        target = Path(path).resolve()
        if target.exists() or target in (self.path, self.ledger):
            raise StoreError("backup_target_exists")
        try:
            with target.open("xb"):
                pass
            with self._connection() as source:
                destination = sqlite3.connect(target, timeout=self.timeout)
                try:
                    self._copy(source, destination)
                finally:
                    destination.close()
        except OSError:
            raise StoreError("storage_io") from None
        return True

    def _copy(self, source, destination):
        deadline = time.monotonic() + 2

        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise StoreError("backup_timeout")

        try:
            source.backup(destination, pages=64, progress=progress, sleep=0.01)
        except sqlite3.Error as exc:
            raise _error(exc) from None

    def restore(self, path):
        """Explicit offline maintenance; the current deletion ledger is never replaced."""
        if not self.enabled:
            return False
        self.purge_expired()
        try:
            source = sqlite3.connect(
                Path(path).resolve().as_uri() + "?mode=ro",
                uri=True,
                timeout=self.timeout,
            )
            try:
                with self._connection() as destination:
                    expected = destination.execute(
                        "SELECT id FROM store_meta"
                    ).fetchone()[0]
                    if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise StoreError("corrupt")
                    if (
                        source.execute("SELECT id FROM store_meta").fetchone()[0]
                        != expected
                    ):
                        raise StoreError("backup_identity_mismatch")
                    # Refuse unknown migration versions before replacing any data.
                    current = [
                        tuple(r)
                        for r in destination.execute("SELECT * FROM schema_migrations")
                    ]
                    if (
                        source.execute("SELECT * FROM schema_migrations").fetchall()
                        != current
                    ):
                        raise StoreError("unsupported_schema")
                    self._copy(source, destination)
            finally:
                source.close()
        except sqlite3.Error as exc:
            raise _error(exc) from None
        self.purge_expired()
        return True
