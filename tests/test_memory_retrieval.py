import asyncio
import threading

import pytest
from test_memory_store import store
from test_operator_controls import make_app

from virtual_ai.memory.base import StoreError
from virtual_ai.memory.retrieval import MemoryContext
from virtual_ai.memory.summarizer import summarize
from virtual_ai.prompting import build_messages
from virtual_ai.schemas import ChatInput, Viewer


def test_ownership_session_expiry_and_bounds(tmp_path):
    now = [100]
    db = store(tmp_path, now=lambda: now[0])
    for user in ("alice", "bob"):
        db.allow(user)
        db.add_fact(
            user, user + " likes astronomy", "fixture", confirmed=True, retention=10
        )
        turn = db.add_turn(
            user,
            "astronomy perhaps",
            "invented assistant assertion",
            session_id="s1",
            retention=10,
        )
        db.add_summary(
            user, "astronomy uncertain", [turn], confirmed=True, session_id="s1"
        )
    rows = db.retrieve("alice", "s2", ["astronomy"])
    assert len(rows) == 1 and rows[0]["text"].startswith("alice")
    assert len(db.retrieve("alice", "s1", ["astronomy"])) == 2
    assert not db.retrieve("alice", "s1", ["unrelated"])

    async def run():
        ctx = MemoryContext(db, "alice", "s1")
        assert not await ctx.retrieve(Viewer("youtube", "alice"), "astronomy")
        text = await ctx.retrieve(Viewer("console", "local"), "astronomy")
        assert "bob" not in text and len(text) <= 1000

    asyncio.run(run())
    now[0] = 111
    assert not db.retrieve("alice", "s1", ["astronomy"])


def test_extractive_summary_and_failure_preserve_previous(tmp_path, monkeypatch):
    db = store(tmp_path)
    db.allow("local")
    db.add_turn("local", "Perhaps astronomy", "FABRICATED", session_id="s1")
    db.add_turn("local", "OTHER SESSION", "answer", session_id="s2")
    summarize(db, "local", "s1")
    previous = db.lookup("local")["session_summaries"]
    assert "Perhaps astronomy" in previous[0]["summary"]
    assert "FABRICATED" not in previous[0]["summary"]
    assert "OTHER SESSION" not in previous[0]["summary"]
    monkeypatch.setattr(
        db, "_expiry", lambda _: (_ for _ in ()).throw(StoreError("capacity"))
    )
    with pytest.raises(StoreError):
        summarize(db, "local", "s1")
    assert db.lookup("local")["session_summaries"] == previous


def test_prompt_context_is_user_data_and_bounded():
    messages = build_messages(
        {}, [], "question", system_prompt="policy", memory_context="reference"
    )
    assert messages[0]["content"] == "policy\n{}"
    assert "reference" in messages[-1]["content"]
    assert (
        build_messages(
            {}, [], "question", system_prompt="policy", memory_context="x" * 1001
        )[-1]["content"]
        == "question"
    )


def test_app_retrieval_failure_and_stop_during_lookup(tmp_path):
    async def run():
        app = make_app(tmp_path)
        db = store(tmp_path)
        db.allow("local")
        db.add_fact("local", "astronomy local fact", "fixture", confirmed=True)
        app.memory = MemoryContext(db)
        seen = []

        async def generate(messages):
            seen.append(messages)
            return "safe reply"

        app.llm.generate = generate
        app.submit(ChatInput(Viewer("console", "local"), "astronomy", "one"))
        await app.process_next()
        assert "astronomy local fact" in seen[0][-1]["content"]
        started, release = threading.Event(), threading.Event()

        def slow(*args):
            started.set()
            release.wait(2)
            return []

        db.retrieve = slow
        app.submit(ChatInput(Viewer("console", "local"), "new question", "two"))
        work = asyncio.create_task(app.process_next())
        await asyncio.to_thread(started.wait, 2)
        await app.stop()
        release.set()
        await work
        assert len(seen) == 1

        def broken(*args):
            raise StoreError("locked")

        db.retrieve = broken
        app.submit(ChatInput(Viewer("console", "local"), "another question", "three"))
        await app.process_next()
        assert len(seen) == 2 and app._component_faults["memory"] == "locked"
        await app.shutdown()

    asyncio.run(run())


def test_idle_summary_does_not_compete_with_response(tmp_path):
    async def run():
        db = store(tmp_path)
        db.allow("local")
        db.add_turn("local", "astronomy maybe", "answer", session_id="s")
        app = make_app(tmp_path)
        app.memory = MemoryContext(db, session_id="s")
        await app._lock.acquire()
        assert not await app.summarize_memory()
        app._lock.release()
        assert await app.summarize_memory()
        assert len(db.lookup("local")["session_summaries"]) == 1
        assert await app.summarize_memory()
        assert len(db.lookup("local")["session_summaries"]) == 1
        await app.shutdown()

    asyncio.run(run())


def test_prompt_budget_drops_memory_before_question():
    from types import SimpleNamespace

    settings = SimpleNamespace(context_tokens=400, max_output_tokens=40)
    messages = build_messages(
        {},
        [],
        "question",
        system_prompt="policy",
        settings=settings,
        memory_context="x" * 200,
    )
    assert messages[-1]["content"] == "question"
