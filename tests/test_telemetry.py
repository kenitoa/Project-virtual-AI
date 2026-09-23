import asyncio
import logging
from time import monotonic

from test_audio import FakeBackend, make_wav, wait_event
from test_cancellation import item
from test_operator_controls import make_app

from virtual_ai.audio.player import WAVPlayer
from virtual_ai.config import AudioSettings
from virtual_ai.inputs.queue import InputQueue


def test_real_player_callback_timestamp_is_observed_not_estimated(tmp_path):
    async def run():
        backend = FakeBackend()
        player = WAVPlayer(AudioSettings(True), backend=backend)
        before = monotonic()
        task = asyncio.create_task(player.play(make_wav(tmp_path)))
        await wait_event(backend.started)
        backend.streams[-1].pump()
        assert await task
        assert before <= player.first_callback_at <= monotonic()
        await player.aclose()

    asyncio.run(run())


def test_trace_correlates_stages_without_logging_user_content(tmp_path, caplog):
    async def run():
        app = make_app(tmp_path)
        app.submit(item("private-input"))
        await app.process_next()
        await app.shutdown()

    with caplog.at_level(logging.INFO, logger="virtual_ai"):
        asyncio.run(run())
    records = [r.message for r in caplog.records if "stage=" in r.message]
    for stage in [
        "received",
        "selected",
        "llm_complete",
        "output_checked",
        "tts_complete",
        "first_playback_callback",
        "playback_finished",
        "cleanup_complete",
    ]:
        assert any("stage=" + stage + " " in r for r in records)
    assert len({r.split()[0] for r in records}) == 1
    assert "private-input" not in caplog.text
    assert "stage=first_playback_callback status=unobserved" in caplog.text


def test_drop_reasons_and_receipt_storage_are_bounded(tmp_path, caplog):
    async def run():
        app = make_app(tmp_path)
        app.queue = InputQueue(1, 1, clock=lambda: 0, on_drop=app._discard)
        app.submit(item("private-one"))
        assert not app.submit(item("private-one"))
        app.submit(item("private-two"))
        assert len(app._receipts) == 1
        app.queue.clock = lambda: 2
        assert len(app.queue) == 0
        assert not app._receipts
        await app.pause()
        assert not app.submit(item("private-three"))
        await app.shutdown()

    with caplog.at_level(logging.INFO, logger="virtual_ai"):
        asyncio.run(run())
    for reason in ["overflow", "expired", "duplicate", "input_locked"]:
        assert "status=" + reason in caplog.text
    assert "private-" not in caplog.text
