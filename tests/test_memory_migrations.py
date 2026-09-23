import sqlite3

import pytest
from test_memory_store import store

from virtual_ai.memory import sqlite_store as module
from virtual_ai.memory.base import StoreError


def test_initial_migration_and_reopen_are_idempotent(tmp_path):
    obj = store(tmp_path)
    obj.allow("local")
    obj.add_fact("local", "fact", "local fixture", confirmed=True)
    reopened = store(tmp_path)
    assert reopened.lookup("local")["memories"][0]["fact"] == "fact"
    with sqlite3.connect(obj.path) as db:
        assert db.execute("SELECT version FROM schema_migrations").fetchall() == [
            (1,),
            (2,),
        ]


@pytest.mark.parametrize("change", ["version", "checksum"])
def test_unknown_or_changed_schema_fails_without_reset(tmp_path, change):
    obj = store(tmp_path)
    with sqlite3.connect(obj.path) as db:
        db.execute(
            "UPDATE schema_migrations SET version=99 WHERE version=1"
            if change == "version"
            else "UPDATE schema_migrations SET checksum='changed'"
        )
    with pytest.raises(StoreError, match="unsupported_schema"):
        store(tmp_path)


def test_failed_migration_rolls_back_all_schema_statements(tmp_path, monkeypatch):
    migration = tmp_path / "broken.sql"
    migration.write_text("CREATE TABLE partial (id INTEGER); INVALID SQL;")
    monkeypatch.setattr(module, "MIGRATION", migration)
    with pytest.raises(StoreError):
        store(tmp_path)
    with sqlite3.connect(tmp_path / "memory.sqlite3") as db:
        assert (
            db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            == []
        )


@pytest.mark.parametrize("backup_fails", [False, True])
def test_upgrade_v1_keeps_facts(tmp_path, monkeypatch, backup_fails):
    original = module.SQLiteStore._migrate

    def old_schema(self, db):
        import hashlib

        sql = module.MIGRATION.read_text(encoding="utf-8")
        db.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL)"
        )
        for statement in sql.split(";"):
            if statement.strip():
                db.execute(statement)
        db.execute(
            "INSERT INTO schema_migrations VALUES (1, ?)",
            (hashlib.sha256(sql.replace("\r\n", "\n").encode()).hexdigest(),),
        )

    monkeypatch.setattr(module.SQLiteStore, "_migrate", old_schema)
    obj = store(tmp_path)
    obj.allow("local")
    obj.add_fact("local", "astronomy fact", "fixture", confirmed=True)
    monkeypatch.setattr(module.SQLiteStore, "_migrate", original)
    if backup_fails:

        def fail_backup(*args):
            raise StoreError("capacity")

        monkeypatch.setattr(module.SQLiteStore, "_copy", fail_backup)
        with pytest.raises(StoreError, match="capacity"):
            store(tmp_path)
        with sqlite3.connect(obj.path) as db:
            assert db.execute("SELECT version FROM schema_migrations").fetchall() == [
                (1,)
            ]
            assert (
                db.execute("SELECT fact FROM memories").fetchone()[0]
                == "astronomy fact"
            )
        return
    upgraded = store(tmp_path)
    backups = list(tmp_path.glob("*.pre-migration-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("SELECT version FROM schema_migrations").fetchall() == [(1,)]
        assert db.execute("SELECT fact FROM memories").fetchone()[0] == "astronomy fact"
    assert (
        upgraded.retrieve("local", "session", ["astronomy"])[0]["text"]
        == "astronomy fact"
    )
