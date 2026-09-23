import asyncio

from test_chat_selection import chat

from virtual_ai.inputs.queue import InputQueue
from virtual_ai.inputs.selection import SelectionPolicy


def test_fifty_thousand_inputs_keep_storage_bounded_and_preserve_waiters():
    now = [0]
    q = InputQueue(20, 30, clock=lambda: now[0], policy=SelectionPolicy())
    for i in range(20):
        assert q.put(chat(f"waiting-{i}", str(i)))
    for i in range(50_000):
        assert not q.put(chat(f"flood-{i}", f"m-{i}"))
    assert len(q) == 20
    assert len(q._deadlines) == 20
    assert len(q._seen) <= 40 and len(q._content_seen) <= 40
    assert len(q.dropped) == 1 and q.dropped["overflow"] == 50_000
    assert [q.pop().viewer.user_id for _ in range(20)] == [
        f"waiting-{i}" for i in range(20)
    ]
    now[0] = 30
    assert len(q) == len(q._seen) == len(q._content_seen) == 0
    assert not q._deadlines


def test_spammer_cannot_displace_slow_waiter_during_continuous_arrivals():
    q = InputQueue(6, 30, policy=SelectionPolicy())
    q.put(chat("spam", "first"))
    q.put(chat("spam", "second"))
    q.put(chat("patient", "waiting"))
    selected = []
    for i in range(10):
        for j in range(100):
            q.put(chat("spam", f"spam-{i}-{j}"))
        selected.append(q.pop().viewer.user_id)
    assert selected[:2] == ["spam", "patient"]
    assert len(q) <= 2 and len(q._seen) <= 12 and len(q._content_seen) <= 12


def test_payload_size_is_bounded_even_for_direct_queue_callers():
    q = InputQueue(20, 30, policy=SelectionPolicy())
    for item in [
        chat("a", "1", "x" * 4001),
        chat("a" * 257, "1"),
        chat("a", "1" * 257),
        chat("a", "1", platform="p" * 33),
    ]:
        assert not q.put(item)
    assert len(q) == len(q._seen) == len(q._content_seen) == 0
    assert q.dropped["invalid_input"] == 4


def test_app_flood_receipts_and_expiry_remain_bounded(tmp_path):
    from test_operator_controls import make_app

    async def run():
        app = make_app(tmp_path)
        now = [0]
        app.queue.clock = lambda: now[0]
        for i in range(5000):
            app.submit(chat(f"viewer-{i}", f"message-{i}"))
        assert len(app._receipts) == len(app.queue) == app.settings.queue_size
        assert app.status()["input_drops"]["overflow"] == 5000 - app.settings.queue_size
        now[0] = app.settings.queue_ttl_seconds
        assert await app.process_next() is None
        assert not app._receipts and len(app.queue) == 0
        await app.shutdown()

    asyncio.run(run())
