import sqlite3

import pytest

from virtual_ai.memory.base import StoreError
from virtual_ai.memory.sqlite_store import SQLiteStore


def store(tmp_path, **kwargs):
    return SQLiteStore(tmp_path / "memory.sqlite3", enabled=True, **kwargs)


def test_disabled_never_creates_files_or_retains_new_information(tmp_path):
    obj = SQLiteStore(tmp_path / "missing" / "memory.sqlite3")
    assert not obj.allow("local")
    assert obj.add_fact("local", "fact", "evidence", confirmed=True) is None
    assert obj.add_turn("local", "hello", "answer") is None
    assert obj.lookup("local") == {}
    assert not obj.forget("local") and not obj.backup(tmp_path / "backup")
    assert list(tmp_path.iterdir()) == []


def test_restart_consent_and_separate_delivery_states(tmp_path):
    obj = store(tmp_path)
    with pytest.raises(StoreError, match="consent_required"):
        obj.add_turn("local", "question", "answer")
    obj.allow("local")
    turn = obj.add_turn("local", "question", "shown answer", displayed=True)
    obj.delivery("local", turn, displayed=True, playback="failed")
    obj.add_turn(
        "local", "next question", "played answer", displayed=True, playback="completed"
    )
    rows = store(tmp_path).lookup("local")["turns"]
    assert [(r["displayed"], r["playback"]) for r in rows] == [
        (1, "failed"),
        (1, "completed"),
    ]
    with pytest.raises(StoreError, match="invalid_delivery_transition"):
        obj.delivery("local", turn, displayed=True, playback="completed")


@pytest.mark.parametrize("source", ["youtube", "youtube_summary", "model", "unknown"])
def test_non_local_origins_are_rejected(tmp_path, source):
    obj = store(tmp_path)
    obj.allow("local")
    for action in (
        lambda: obj.add_turn("local", "q", "a", source=source),
        lambda: obj.add_fact(
            "local", "fact", "evidence", confirmed=True, source=source
        ),
        lambda: obj.add_summary(
            "local", "summary", ["id"], source=source, confirmed=True
        ),
    ):
        with pytest.raises(StoreError, match="policy_denied"):
            action()
    assert all(not rows for rows in obj.lookup("local").values())


def test_facts_require_confirmation_and_summaries_require_owned_evidence(tmp_path):
    obj = store(tmp_path)
    obj.allow("a")
    obj.allow("b")
    with pytest.raises(StoreError, match="policy_denied"):
        obj.add_fact("a", "blue", "operator")
    fact = obj.add_fact("a", "likes blue", "explicit local fixture", confirmed=True)
    turn = obj.add_turn("a", "q", "a")
    with pytest.raises(StoreError, match="evidence_missing"):
        obj.add_summary("b", "summary", [turn], confirmed=True)
    obj.add_summary("a", "summary", [turn], confirmed=True)
    assert obj.lookup("a")["memories"][0]["id"] == fact


def test_expiry_and_summary_cannot_extend_evidence_retention(tmp_path):
    now = [100]
    obj = store(tmp_path, now=lambda: now[0])
    obj.allow("a")
    turn = obj.add_turn("a", "q", "a", retention=5)
    obj.add_fact("a", "fact", "evidence", confirmed=True, retention=5)
    obj.add_summary("a", "summary", [turn], confirmed=True, retention=100)
    assert obj.lookup("a")["session_summaries"][0]["expires_at"] == 105
    now[0] = 105
    assert all(not rows for rows in obj.lookup("a").values())


def test_delete_and_old_backup_restore_do_not_resurrect_information(tmp_path):
    obj = store(tmp_path)
    obj.allow("a")
    obj.allow("b")
    turn = obj.add_turn("a", "q", "a")
    obj.add_fact("a", "fact", "evidence", confirmed=True)
    obj.add_summary("a", "summary", [turn], confirmed=True)
    obj.add_fact("b", "preserve", "evidence", confirmed=True)
    backup = tmp_path / "backup.sqlite3"
    assert obj.backup(backup)
    assert obj.forget("a")
    assert obj.restore(backup)
    assert all(not rows for rows in store(tmp_path).lookup("a").values())
    assert obj.lookup("b")["memories"][0]["fact"] == "preserve"
    with pytest.raises(StoreError, match="deleted_subject"):
        obj.allow("a")
    assert obj.forget("a")


def test_missing_deletion_ledger_fails_closed(tmp_path):
    obj = store(tmp_path)
    obj.ledger.unlink()
    with pytest.raises(StoreError, match="ledger_missing"):
        store(tmp_path)
    with pytest.raises(StoreError, match="ledger_missing"):
        obj.lookup("a")


def test_lock_failure_is_bounded_and_sanitized(tmp_path):
    obj = store(tmp_path, timeout=0.01)
    with sqlite3.connect(obj.path) as blocker:
        blocker.execute("BEGIN EXCLUSIVE")
        with pytest.raises(StoreError) as exc:
            obj.allow("private-name")
        assert exc.value.reason == "locked"
        assert "private-name" not in str(exc.value)
        blocker.rollback()
    assert obj.allow("local")


def test_real_sqlite_capacity_failure_rolls_back(tmp_path):
    obj = store(tmp_path, max_pages=32)
    obj.allow("a")
    written = 0
    with pytest.raises(StoreError) as exc:
        for _ in range(100):
            obj.add_turn("a", "x" * 4000, "y" * 4000)
            written += 1
    assert exc.value.reason == "capacity"
    assert len(obj.lookup("a")["turns"]) == written


def test_corrupt_database_is_not_replaced(tmp_path):
    obj = store(tmp_path)
    obj.path.write_bytes(b"corrupt-private-content")
    with pytest.raises(StoreError) as exc:
        store(tmp_path)
    assert exc.value.reason == "corrupt"
    assert obj.path.read_bytes() == b"corrupt-private-content"


def test_parameter_binding_and_sensitive_text_gate(tmp_path):
    obj = store(tmp_path)
    user = "x'); DROP TABLE turns; --"
    obj.allow(user)
    obj.add_fact(user, "quote ' fact", "local evidence", confirmed=True)
    assert len(obj.lookup(user)["memories"]) == 1
    for text in ("password=private", "010-1234-5678"):
        with pytest.raises(StoreError, match="sensitive_input"):
            obj.add_turn(user, text, "answer")


def test_backup_does_not_overwrite_and_unrelated_restore_rejected(tmp_path):
    obj = store(tmp_path)
    existing = tmp_path / "existing"
    existing.write_text("keep")
    with pytest.raises(StoreError, match="backup_target_exists"):
        obj.backup(existing)
    assert existing.read_text() == "keep"
    other = SQLiteStore(tmp_path / "other.sqlite3", enabled=True)
    with pytest.raises(StoreError, match="identity_mismatch"):
        obj.restore(other.path)


def test_readonly_backup_timeout_is_bounded(tmp_path, monkeypatch):
    obj = store(tmp_path)

    class Stalled:
        def backup(self, destination, **kwargs):
            kwargs["progress"](5, 1, 1)

    times = iter([0, 3])
    monkeypatch.setattr(
        "virtual_ai.memory.sqlite_store.time.monotonic", lambda: next(times)
    )
    with pytest.raises(StoreError, match="backup_timeout"):
        obj._copy(Stalled(), object())


def test_storage_file_error_is_sanitized(tmp_path):
    parent = tmp_path / "private-path"
    parent.write_text("existing file")
    with pytest.raises(StoreError) as exc:
        SQLiteStore(parent / "db.sqlite3", enabled=True)
    assert exc.value.reason == "storage_io"
    assert "private-path" not in str(exc.value)


def test_disabled_store_does_not_append_to_existing_database(tmp_path):
    obj = store(tmp_path)
    obj.allow("local")
    obj.add_fact("local", "approved", "fixture", confirmed=True)
    disabled = SQLiteStore(obj.path)
    assert disabled.add_fact("local", "not approved", "fixture", confirmed=True) is None
    assert [r["fact"] for r in obj.lookup("local")["memories"]] == ["approved"]


def test_expired_backup_records_are_removed_after_restore(tmp_path):
    now = [0]
    obj = store(tmp_path, now=lambda: now[0])
    obj.allow("local")
    obj.add_fact("local", "temporary", "fixture", confirmed=True, retention=1)
    path = tmp_path / "backup.sqlite3"
    obj.backup(path)
    now[0] = 2
    obj.restore(path)
    assert obj.lookup("local")["memories"] == []
