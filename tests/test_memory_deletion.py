import asyncio

from test_memory_store import store
from test_operator_controls import make_app

from virtual_ai.memory.base import StoreError
from virtual_ai.memory.retrieval import MemoryContext
from virtual_ai.memory.summarizer import summarize
from virtual_ai.schemas import Viewer


def test_long_deletion_ram_summary_and_restored_backup(tmp_path):
    async def run():
        db = store(tmp_path)
        db.allow("local")
        db.add_fact("local", "astronomy fact", "fixture", confirmed=True)
        db.add_turn("local", "astronomy maybe", "answer", session_id="s")
        summarize(db, "local", "s")
        backup = tmp_path / "old.sqlite3"
        db.backup(backup)
        app = make_app(tmp_path)
        app.memory = MemoryContext(db, session_id="s")
        viewer = Viewer("console", "local")
        app.history.add(viewer, "astronomy", "answer")
        assert await app.forget_long()
        assert app.runtime.paused and not app.history.messages(viewer)
        assert not await app.memory.retrieve(viewer, "astronomy")
        db.restore(backup)
        assert not db.retrieve("local", "s", ["astronomy"])
        assert all(not rows for rows in db.lookup("local").values())
        await app.shutdown()

    asyncio.run(run())


def test_failed_deletion_disables_memory_and_pauses(tmp_path):
    async def run():
        db = store(tmp_path)
        app = make_app(tmp_path)
        app.memory = MemoryContext(db)

        def fail(*args):
            raise StoreError("locked")

        db.forget = fail
        assert not await app.forget_long()
        assert app.memory.blocked and app.runtime.paused
        assert app._component_faults["memory"] == "locked"
        await app.shutdown()

    asyncio.run(run())
