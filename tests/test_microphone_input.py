import asyncio

import pytest
from test_operator_controls import make_app

from virtual_ai.inputs.microphone import MicrophoneInput
from virtual_ai.stt.base import STTError


class Backend:
    def __init__(self, *, silent=False, overflow=False):
        self.silent, self.overflow = silent, overflow
        self.closed = False
        self.starts = 0

    def check_input_settings(self, **kwargs):
        assert kwargs["device"] == 1 and kwargs["samplerate"] == 16000

    def RawInputStream(self, **kwargs):
        backend = self

        class Stream:
            def start(self):
                backend.starts += 1
                kwargs["callback"](
                    (b"\0\0" if backend.silent else b"\x00\x10") * 4000,
                    4000,
                    None,
                    backend.overflow,
                )

            def stop(self):
                pass

            def abort(self):
                pass

            def close(self):
                backend.closed = True

        return Stream()


class Client:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    async def transcribe(self, path):
        assert path.exists()
        self.path = path
        self.calls += 1
        if self.fail:
            raise STTError("engine_failed")
        return "/quit"


async def recording(mic):
    async with asyncio.timeout(3):
        while mic.state != "recording" and not mic.task.done():
            await asyncio.sleep(0.01)


def test_half_duplex_and_recognized_commands_are_data(tmp_path):
    async def run():
        app = make_app(tmp_path)
        client, backend = Client(), Backend()
        mic = app.microphone = MicrophoneInput(app, client, 1, backend=backend)
        assert mic.start()
        assert app.runtime.input_locked and not app.resume()
        await recording(mic)
        assert app.runtime.paused and backend.starts == 1
        assert not mic.start()
        mic.release()
        await mic.task
        assert backend.closed and not client.path.exists()
        assert not app._closed and not app.runtime.paused
        item = app.queue.pop()
        assert item.text == "/quit" and item.viewer.platform == "microphone"
        await app.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize(
    "case,reason",
    [
        ("silence", "silence"),
        ("overflow", "input_overflow"),
        ("stt", "engine_failed"),
        ("cancel", "cancelled"),
    ],
)
def test_failure_and_cancel_release_device_and_remain_paused(tmp_path, case, reason):
    async def run():
        app = make_app(tmp_path)
        backend = Backend(silent=case == "silence", overflow=case == "overflow")
        client = Client(fail=case == "stt")
        mic = app.microphone = MicrophoneInput(app, client, 1, backend=backend)
        assert mic.start()
        await recording(mic)
        if case == "cancel":
            await app.stop()
        else:
            mic.release()
            await mic.task
        assert backend.closed and app.runtime.paused
        assert not app.runtime.microphone_active and not len(app.queue)
        assert mic.reason == reason
        await app.shutdown()

    asyncio.run(run())


def test_maximum_length_and_unconfirmed_output(tmp_path):
    async def run():
        app = make_app(tmp_path)
        backend, client = Backend(), Client()
        mic = app.microphone = MicrophoneInput(
            app, client, 1, backend=backend, max_seconds=0.2
        )
        assert mic.start()
        await mic.task
        assert client.calls == 1 and backend.closed
        await app.stop()
        assert app.resume()
        app.llm.cleanup_confirmed = False
        assert mic.start()
        await mic.task
        assert mic.reason == "output_cleanup_unconfirmed" and backend.starts == 1
        await app.shutdown()

    asyncio.run(run())


def test_output_stops_before_capture_and_stt_cancel_discards_late_text(tmp_path):
    from test_voice_pipeline import Player, item

    async def run():
        player = Player(hold=True)
        app = make_app(tmp_path, player=player)
        app.submit(item())
        response = asyncio.create_task(app.process_next())
        await player.started.wait()

        class CheckedBackend(Backend):
            def check_input_settings(self, **kwargs):
                assert not player.active and not app._lock.locked()
                super().check_input_settings(**kwargs)

        started = asyncio.Event()

        class SlowClient:
            async def transcribe(self, path):
                started.set()
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    return "late recognized text"

        mic = app.microphone = MicrophoneInput(
            app, SlowClient(), 1, backend=CheckedBackend()
        )
        assert mic.start()
        await recording(mic)
        mic.release()
        await started.wait()
        await app.panic()
        await response
        assert app.runtime.panic and app.runtime.paused and not len(app.queue)
        await app.shutdown()

    asyncio.run(run())
