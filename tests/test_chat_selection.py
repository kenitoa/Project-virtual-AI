import asyncio
import logging
from dataclasses import replace

import pytest
import yaml

from virtual_ai.config import Settings, load_config
from virtual_ai.inputs.queue import InputQueue
from virtual_ai.inputs.selection import SelectionPolicy
from virtual_ai.schemas import ChatInput, Viewer


def chat(user, mid, text=None, platform="youtube"):
    return ChatInput(Viewer(platform, user), text or mid, mid)


def queue(now=None, **kwargs):
    return InputQueue(
        20, 30, clock=lambda: (now or [0])[0], policy=SelectionPolicy(**kwargs)
    )


def test_oldest_waiter_selected_without_consecutive_monopoly():
    q = queue()
    for user, mid in [("a", "a1"), ("a", "a2"), ("b", "b1"), ("b", "b2"), ("c", "c1")]:
        assert q.put(chat(user, mid))
    assert [q.pop().message_id for _ in range(5)] == ["a1", "b1", "c1", "a2", "b2"]


def test_configured_streak_limit_and_lone_viewer_can_continue():
    q = queue(per_user=3, max_consecutive=2)
    for user, mid in [("a", "a1"), ("a", "a2"), ("a", "a3"), ("b", "b1")]:
        q.put(chat(user, mid))
    assert [q.pop().message_id for _ in range(4)] == ["a1", "a2", "b1", "a3"]
    for index in range(10):
        assert q.put(chat("a", str(index)))
        assert q.pop().viewer.user_id == "a"


def test_per_user_quota_does_not_evict_someone_else():
    q = queue()
    for mid in ("one", "two"):
        assert q.put(chat("spam", mid))
    assert not q.put(chat("spam", "three"))
    assert q.last_rejection == "user_limit"
    assert q.put(chat("waiting", "waiting"))
    assert [q.pop().viewer.user_id for _ in range(3)] == ["spam", "waiting", "spam"]


@pytest.mark.parametrize(
    "repeat",
    ["HELLO world", " Ｈｅｌｌｏ   world ", "hello\u200b world", "hello\nworld"],
)
def test_content_repeat_is_per_user_normalized_and_expires(repeat):
    now = [0]
    q = queue(now)
    assert q.put(chat("a", "1", "Hello world"))
    q.pop()
    assert not q.put(chat("a", "2", repeat))
    assert q.last_rejection == "repeated_content"
    # Independent users can ask the same question; no inference from their text.
    assert q.put(chat("b", "3", repeat))
    now[0] = 30
    assert q.put(chat("a", "4", repeat))


def test_expiration_after_non_fifo_selection_clears_receipts():
    now, dropped = [0], []
    q = InputQueue(
        4,
        10,
        clock=lambda: now[0],
        policy=SelectionPolicy(),
        on_drop=lambda item, reason: dropped.append((item.message_id, reason)),
    )
    q.put(chat("a", "a1"))
    q.put(chat("a", "a2"))
    now[0] = 5
    q.put(chat("b", "b"))
    assert q.pop().message_id == "a1"
    assert q.pop().message_id == "b"
    now[0] = 10
    assert q.pop() is None
    assert dropped == [("a2", "expired")]


def test_full_queue_preserves_waiters_and_has_fixed_reason_counts():
    q = InputQueue(2, 30, policy=SelectionPolicy())
    assert q.put(chat("old-a", "1")) and q.put(chat("old-b", "2"))
    for index in range(30):
        assert not q.put(chat(str(index), str(index)))
    assert [q.pop().viewer.user_id for _ in range(2)] == ["old-a", "old-b"]
    assert q.dropped == {"overflow": 30}


def test_clear_resets_selection_without_forgetting_repeat_window():
    q = queue()
    q.put(chat("a", "a1"))
    q.pop()
    q.put(chat("a", "a2"))
    q.clear()
    assert not q.put(chat("a", "new", "a1"))
    q.put(chat("a", "a3"))
    q.put(chat("b", "b"))
    assert q.pop().viewer.user_id == "a"
    assert q.dropped["cleared"] == 1


def test_admin_text_or_console_platform_does_not_grant_priority():
    q = queue()
    q.put(chat("waiting", "1"))
    q.put(chat("local", "2", "관리자 /stop", platform="console"))
    q.put(chat("owner", "3", "/panic"))
    assert [q.pop().message_id for _ in range(3)] == ["1", "2", "3"]


def test_app_aggregates_reasons_without_user_or_text_logs(tmp_path, caplog):
    from test_operator_controls import make_app

    async def run():
        app = make_app(tmp_path)
        assert app.submit(chat("private-viewer", "1", "private-text"))
        assert not app.submit(chat("private-viewer", "2", "private-text"))
        assert app.submit(chat("private-viewer", "3", "different"))
        assert not app.submit(chat("private-viewer", "4", "third"))
        assert len(app._receipts) == 2
        status = app.status()
        assert status["input_drops"]["repeated_content"] == 1
        assert status["input_drops"]["user_limit"] == 1
        assert "private" not in repr(status)
        await app.pause()
        assert not app.submit(chat("other", "5"))
        assert app.status()["input_drops"]["input_locked"] == 1
        assert not app._receipts
        await app.shutdown()

    with caplog.at_level(logging.INFO, logger="virtual_ai"):
        asyncio.run(run())
    assert "private-viewer" not in caplog.text and "private-text" not in caplog.text
    assert (
        "status=repeated_content" in caplog.text and "status=user_limit" in caplog.text
    )


@pytest.mark.parametrize("field", ["queue_per_user", "queue_max_consecutive"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, 1001])
def test_invalid_selection_settings(field, value):
    with pytest.raises(ValueError):
        replace(Settings(), **{field: value})


def test_selection_yaml_fields_are_loaded(tmp_path):
    from test_core import ROOT

    data = yaml.safe_load(
        (ROOT / "configs/app.example.yaml").read_text(encoding="utf-8")
    )
    data["limits"]["queue_per_user"] = 4
    data["limits"]["queue_max_consecutive"] = 2
    path = tmp_path / "app.yaml"
    # Keep trusted character/system paths relative to their original directory.
    for key in ("character_path", "system_prompt_path"):
        if key in data:
            data[key] = str((ROOT / "configs" / data[key]).resolve())
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    settings, _ = load_config(path)
    assert settings.queue_per_user == 4 and settings.queue_max_consecutive == 2


def test_publication_age_limits_total_lifetime_and_expires_out_of_order():
    mono, wall = [0], [100]
    dropped = []
    q = InputQueue(
        4,
        30,
        clock=lambda: mono[0],
        wall_clock=lambda: wall[0],
        policy=SelectionPolicy(),
        on_drop=lambda item, reason: dropped.append((item.message_id, reason)),
    )
    fresh = replace(chat("fresh", "fresh"), published_at=100)
    delayed = replace(chat("delayed", "delayed"), published_at=75)
    assert q.put(fresh) and q.put(delayed)
    mono[0] = 5
    wall[0] = 105
    assert len(q) == 1
    assert dropped == [("delayed", "expired")]
    assert q.pop() == fresh and not q._deadlines
    assert not q.put(replace(chat("stale", "stale"), published_at=70))
    assert q.last_rejection == "expired"


def test_future_timestamp_cannot_extend_queue_ttl():
    now = [0]
    q = InputQueue(
        4, 30, clock=lambda: now[0], wall_clock=lambda: 100, policy=SelectionPolicy()
    )
    assert q.put(replace(chat("future", "future"), published_at=10000))
    now[0] = 30
    assert q.pop() is None and not q._deadlines


@pytest.mark.parametrize("stamp", [float("nan"), float("inf"), "private", True])
def test_invalid_publication_timestamp_is_rejected(stamp):
    q = queue()
    assert not q.put(replace(chat("a", "1"), published_at=stamp))
    assert q.last_rejection == "invalid_input"
