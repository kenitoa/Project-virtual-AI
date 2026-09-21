import asyncio
import threading
import wave
from pathlib import Path

import pytest
import yaml

from virtual_ai.audio import __main__ as audio_cli
from virtual_ai.audio.__main__ import interactive
from virtual_ai.audio.base import AudioError
from virtual_ai.audio.player import WAVPlayer
from virtual_ai.config import AudioSettings, Settings, load_config

ROOT = Path(__file__).resolve().parents[1]


def make_wav(tmp_path, width=2, channels=1):
    path = tmp_path / "test.wav"
    # Even frame count also permits RIFF padding rules with 8-bit mono.
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(32000)
        wav.writeframes(b"\x12" * width * channels * 100)
    return path


class FakeBackend:
    class CallbackStop(Exception):
        pass

    class CallbackAbort(Exception):
        pass

    def __init__(self, fail=None, gate=None):
        self.fail = fail
        self.gate = gate
        self.streams = []
        self.opening = threading.Event()
        self.started = threading.Event()
        self.checked = None

    def check_output_settings(self, **kwargs):
        self.checked = kwargs
        if self.fail == "device":
            raise RuntimeError("private device error")

    def RawOutputStream(self, **kwargs):
        self.opening.set()
        if self.gate:
            assert self.gate.wait(3)
        if self.fail == "open":
            raise RuntimeError("open failed")
        stream = FakeStream(self, kwargs)
        self.streams.append(stream)
        return stream


class FakeStream:
    def __init__(self, backend, kwargs):
        self.backend = backend
        self.kwargs = kwargs
        self.calls = []
        self.active = False
        self.stopped = True
        self.closed = False
        self.eof = False

    def start(self):
        self.calls.append("start")
        self.stopped = False
        if self.backend.fail == "start":
            raise RuntimeError("start failed")
        self.active = True
        self.backend.started.set()

    def pump(self, frames=128, status=False, drain=True):
        width = {"uint8": 1, "int16": 2, "int24": 3, "int32": 4}[self.kwargs["dtype"]]
        output = bytearray(frames * self.kwargs["channels"] * width)
        try:
            self.kwargs["callback"](output, frames, None, status)
        except self.backend.CallbackStop:
            self.eof = True
            if drain:
                self.finish()
        except self.backend.CallbackAbort:
            self.finish()
        return output

    def finish(self):
        self.active = False
        self.kwargs["finished_callback"]()

    def stop(self, *, ignore_errors):
        assert not ignore_errors
        self.calls.append("stop")
        self.active = False
        self.stopped = True

    def abort(self, *, ignore_errors):
        assert not ignore_errors
        self.calls.append("abort")
        self.active = False
        self.stopped = True
        if self.backend.fail == "abort":
            raise RuntimeError("abort failed")

    def close(self, *, ignore_errors):
        assert not ignore_errors
        self.calls.append("close")
        self.active = False
        self.closed = True
        if self.backend.fail == "close":
            raise RuntimeError("close failed")


async def wait_event(event):
    assert await asyncio.to_thread(event.wait, 3)


@pytest.mark.parametrize("width", [1, 2, 3, 4])
@pytest.mark.parametrize("channels", [1, 2])
def test_normal_drain_device_format_and_next_file(tmp_path, width, channels):
    async def run():
        backend = FakeBackend()
        player = WAVPlayer(AudioSettings(True, 7), backend=backend)
        for _ in range(2):
            backend.started.clear()
            task = asyncio.create_task(player.play(make_wav(tmp_path, width, channels)))
            await wait_event(backend.started)
            stream = backend.streams[-1]
            output = stream.pump(drain=False)
            assert output[: 100 * width * channels] == b"\x12" * 100 * width * channels
            silence = b"\x80" if width == 1 else b"\x00"
            assert output[100 * width * channels :] == silence * 28 * width * channels
            assert not task.done()  # EOF callback is not yet audible completion.
            stream.active = False  # PortAudio can update this before the callback.
            await asyncio.sleep(0.03)
            assert not task.done()
            stream.finish()
            assert await task is True
            assert stream.calls == ["start", "stop", "close"]
            assert backend.checked["device"] == 7
            assert backend.checked["samplerate"] == 32000
        await player.aclose()
        await player.aclose()
        with pytest.raises(AudioError):
            await player.play(tmp_path / "test.wav")

    asyncio.run(run())


@pytest.mark.parametrize("action", ["stop", "cancel", "close", "draining"])
def test_interrupt_aborts_hardware_and_releases_device(tmp_path, action):
    async def run():
        backend = FakeBackend()
        player = WAVPlayer(AudioSettings(True), backend=backend)
        task = asyncio.create_task(player.play(make_wav(tmp_path)))
        await wait_event(backend.started)
        stream = backend.streams[0]
        with pytest.raises(AudioError, match="이미"):
            await player.play(tmp_path / "test.wav")
        if action == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            if action == "draining":
                stream.pump(drain=False)
            await (player.aclose() if action == "close" else player.stop())
            assert await task is False
        assert stream.calls == ["start", "abort", "close"]
        assert stream.closed and not stream.active
        if action != "close":
            backend.started.clear()
            task = asyncio.create_task(player.play(tmp_path / "test.wav"))
            await wait_event(backend.started)
            backend.streams[-1].pump()
            assert await task
        await player.aclose()

    asyncio.run(run())


def test_repeated_cancellation_during_open_never_starts_audio(tmp_path):
    async def run():
        gate = threading.Event()
        backend = FakeBackend(gate=gate)
        player = WAVPlayer(AudioSettings(True), backend=backend)
        task = asyncio.create_task(player.play(make_wav(tmp_path)))
        await wait_event(backend.opening)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert backend.streams[0].calls == ["close"]
        await player.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure", ["device", "open", "start", "abort", "close", "callback", "disconnect"]
)
def test_device_and_callback_errors_always_release(tmp_path, failure):
    async def run():
        backend = FakeBackend(fail=failure)
        player = WAVPlayer(AudioSettings(True, "selected output"), backend=backend)
        task = asyncio.create_task(player.play(make_wav(tmp_path)))
        if failure not in ("device", "open", "start"):
            await wait_event(backend.started)
            stream = backend.streams[0]
            if failure == "abort":
                with pytest.raises(AudioError):
                    await player.stop()
            elif failure == "disconnect":
                stream.finish()
            else:
                stream.pump(status=failure == "callback")
        with pytest.raises(AudioError):
            await task
        assert all(s.closed for s in backend.streams)
        assert "private" not in str(task.exception())
        await player.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "kind", ["missing", "directory", "garbage", "truncated", "float", "empty"]
)
def test_invalid_files_do_not_open_device(tmp_path, kind):
    path = make_wav(tmp_path)
    if kind == "missing":
        path = tmp_path / "missing.wav"
    elif kind == "directory":
        path = tmp_path
    elif kind == "garbage":
        path.write_bytes(b"not a WAV")
    elif kind == "truncated":
        path.write_bytes(path.read_bytes()[:-2])
    elif kind == "float":
        data = bytearray(path.read_bytes())
        data[20:22] = (3).to_bytes(2, "little")
        path.write_bytes(data)
    elif kind == "empty":
        with wave.open(str(path), "wb") as wav:
            wav.setparams((1, 2, 32000, 0, "NONE", "not compressed"))

    async def run():
        backend = FakeBackend()
        player = WAVPlayer(AudioSettings(True), backend=backend)
        with pytest.raises(AudioError):
            await player.play(path)
        assert not backend.streams
        await player.aclose()

    asyncio.run(run())


def test_disabled_never_opens_backend(tmp_path):
    async def run():
        backend = FakeBackend()
        player = WAVPlayer(AudioSettings(), backend=backend)
        await player.stop()
        with pytest.raises(AudioError):
            await player.play(make_wav(tmp_path))
        assert not backend.streams
        await player.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("device", [-1, True, 1.5, "", "  ", [], {}])
def test_invalid_device_config(device):
    with pytest.raises(ValueError):
        AudioSettings(output_device=device)


def test_config_loading_and_legacy_defaults(tmp_path):
    assert Settings().audio == AudioSettings()
    with pytest.raises(ValueError):
        AudioSettings(enabled="true")
    with pytest.raises(ValueError):
        Settings(audio={})
    data = yaml.safe_load(
        (ROOT / "configs/app.example.yaml").read_text(encoding="utf-8")
    )
    data["character_path"] = str(ROOT / "configs/character.yaml")
    path = tmp_path / "app.yaml"
    for value in (
        {"enabled": True, "output_device": "Speakers"},
        {},
        {"extra": 1},
        None,
    ):
        data["audio"] = value
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        if value is None or "extra" in value:
            with pytest.raises(ValueError):
                load_config(path)
        else:
            settings, _ = load_config(path)
            assert settings.audio == AudioSettings(**value)
    del data["audio"]
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert load_config(path)[0].audio == AudioSettings()


def test_console_stop_next_file_and_quit_while_playing(tmp_path):
    async def run():
        backend = FakeBackend()
        player = WAVPlayer(AudioSettings(True), backend=backend)
        commands = asyncio.Queue()
        path = make_wav(tmp_path)
        console = asyncio.create_task(interactive(player, path, commands))
        await wait_event(backend.started)
        commands.put_nowait("/stop")
        for _ in range(200):
            if backend.streams[0].closed:
                break
            await asyncio.sleep(0.01)
        assert backend.streams[0].closed
        backend.started.clear()
        commands.put_nowait(f"/play {path}")
        await wait_event(backend.started)
        commands.put_nowait("/quit")
        await asyncio.wait_for(console, 3)
        assert len(backend.streams) == 2
        assert all(s.calls == ["start", "abort", "close"] for s in backend.streams)

    asyncio.run(run())


@pytest.mark.parametrize("device,expected", [("4", 4), ("Speakers", "Speakers")])
def test_cli_once_device_override_and_cleanup(
    monkeypatch, capsys, tmp_path, device, expected
):
    created = []

    class Player:
        def __init__(self, settings):
            self.settings = settings
            self.closed = False
            created.append(self)

        async def play(self, path):
            assert path == tmp_path / "voice.wav"
            return True

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(audio_cli, "WAVPlayer", Player)
    monkeypatch.setattr(
        "sys.argv",
        [
            "audio",
            str(tmp_path / "voice.wav"),
            "--config",
            str(ROOT / "configs/app.example.yaml"),
            "--device",
            device,
        ],
    )
    audio_cli.main()
    assert created[0].settings.output_device == expected
    assert created[0].closed
    assert "재생 완료" in capsys.readouterr().out


def test_cli_error_exit_and_cleanup(monkeypatch, tmp_path):
    created = []

    class Player:
        def __init__(self, settings):
            self.closed = False
            created.append(self)

        async def play(self, path):
            raise AudioError("재생 실패")

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(audio_cli, "WAVPlayer", Player)
    monkeypatch.setattr(
        "sys.argv",
        [
            "audio",
            str(tmp_path / "voice.wav"),
            "--config",
            str(ROOT / "configs/app.example.yaml"),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        audio_cli.main()
    assert exc.value.code == 1
    assert created[0].closed


def test_oversized_wav_rejected_before_open(monkeypatch, tmp_path):
    path = make_wav(tmp_path)
    monkeypatch.setattr("virtual_ai.audio.player.MAX_WAV_BYTES", 32)

    async def run():
        backend = FakeBackend()
        player = WAVPlayer(AudioSettings(True), backend=backend)
        with pytest.raises(AudioError):
            await player.play(path)
        assert not backend.streams

    asyncio.run(run())
