import asyncio
import json
import math
from dataclasses import replace

import pytest
from test_audio import FakeBackend, make_wav, wait_event
from test_expression_pipeline import Avatar, build
from test_voice_pipeline import Player, item
from test_vts_client import API_NAME, FakeSocket, make_client

from virtual_ai.audio import levels as levels_module
from virtual_ai.audio.base import AudioError
from virtual_ai.audio.levels import PlaybackLevels, pcm_rms
from virtual_ai.audio.player import WAVPlayer
from virtual_ai.config import AudioSettings, VTSSettings
from virtual_ai.integrations.vts.base import AvatarError
from virtual_ai.lipsync import MouthSync


@pytest.mark.parametrize("width", [1, 2, 3, 4])
def test_pcm_silence_full_scale_and_stereo_rms(width):
    silence = b"\x80" if width == 1 else b"\x00" * width
    assert pcm_rms(silence * 128, width) == 0
    assert pcm_rms(b"", width) == 0
    scale = 2 ** (width * 8 - 1)
    if width == 1:
        samples = bytes([128 + scale // 2, 128 - scale // 2])
        full = b"\x00"
    else:
        samples = (scale // 2).to_bytes(width, "little", signed=True) + (
            -scale // 2
        ).to_bytes(width, "little", signed=True)
        full = (-scale).to_bytes(width, "little", signed=True)
    assert pcm_rms(samples * 10000, width) == pytest.approx(0.5)
    assert pcm_rms(full * 128, width) == 1


def test_single_latest_value_expires_and_ignores_late_callbacks(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(levels_module.time, "monotonic", lambda: now[0])
    levels = PlaybackLevels()
    assert levels.latest() is None
    for i in range(1000):
        levels.publish(i / 1000, 0.02)
    assert levels.latest() == 0.999
    now[0] += 1
    assert levels.latest() == 0
    levels.close()
    levels.publish(1, 10)
    assert levels.latest() is None


@pytest.mark.parametrize("stop", [False, True])
def test_pcm_levels_follow_real_player_callbacks_not_file_duration(tmp_path, stop):
    async def run():
        levels = PlaybackLevels()
        backend = FakeBackend()
        player = WAVPlayer(AudioSettings(True), backend=backend)
        task = asyncio.create_task(player.play(make_wav(tmp_path), levels=levels))
        await wait_event(backend.started)
        assert levels.latest() is None
        stream = backend.streams[0]
        stream.pump(frames=40)
        assert 0 < levels.latest() < 1
        if stop:
            await player.stop()
            assert await task is False
        else:
            stream.pump()
            assert await task is True
        assert levels.latest() is None
        levels.publish(1, 10)
        assert levels.latest() is None
        assert stream.closed

    asyncio.run(run())


def test_metering_failure_cannot_abort_audio(tmp_path):
    class BrokenMeter:
        def publish(self, *args):
            raise RuntimeError("meter failed")

        def close(self):
            pass

    async def run():
        backend = FakeBackend()
        player = WAVPlayer(AudioSettings(True), backend=backend)
        task = asyncio.create_task(
            player.play(make_wav(tmp_path), levels=BrokenMeter())
        )
        await wait_event(backend.started)
        backend.streams[0].pump()
        assert await task is True

    asyncio.run(run())


def settings(**kwargs):
    return VTSSettings(
        lipsync_enabled=True,
        expected_model_id="model-1",
        mouth_parameter="MouthOpen",
        **kwargs,
    )


def test_sender_latest_only_rate_limit_smoothing_and_silence():
    async def run():
        calls = []
        sent = asyncio.Event()

        async def send(value, *, valid):
            if valid():
                calls.append((asyncio.get_running_loop().time(), value))
                sent.set()

        session = MouthSync(
            settings(lipsync_gain=2, lipsync_smoothing=0.5), send, lambda: True
        )
        await asyncio.sleep(0)
        assert not calls  # No synthetic timeline before the first callback.
        for i in range(1000):
            session.levels.publish(0.5, 1)
        await asyncio.wait_for(sent.wait(), 1)
        assert calls[0][1] == 0.5
        sent.clear()
        session.levels.publish(0, 1)
        await asyncio.wait_for(sent.wait(), 1)
        assert calls[-1][1] == 0
        assert calls[-1][0] - calls[0][0] >= 1 / 25 - 0.005
        session.stop()
        await session.task
        count = len(calls)
        session.levels.publish(1, 1)
        await asyncio.sleep(0.05)
        assert len(calls) == count

    asyncio.run(run())


class MouthAvatar(Avatar):
    def __init__(self, *, mouth_fail=False):
        super().__init__()
        self.mouth = []
        self.mouth_sent = asyncio.Event()
        self.mouth_fail = mouth_fail

    async def set_mouth_open(self, value, *, valid=None):
        if valid is not None and not valid():
            return
        self.mouth.append(value)
        self.mouth_sent.set()
        if self.mouth_fail:
            raise AvatarError("private mouth error")


class MeterPlayer(Player):
    def __init__(self, avatar, *, fail=False):
        super().__init__()
        self.avatar = avatar
        self.fail = fail
        self.levels = None
        self.level = 0.25
        self.finish = asyncio.Event()

    async def play(self, path, *, levels=None):
        self.calls.append(path)
        self.levels = levels
        self.active = True
        self.started.set()
        try:
            levels.publish(self.level, 2)
            await self.avatar.mouth_sent.wait()
            if self.fail:
                raise AudioError("playback error")
            await self.finish.wait()
            return True
        finally:
            self.active = False

    async def stop(self):
        await super().stop()
        self.finish.set()


def pipeline(tmp_path, *, fail=False, mouth_fail=False):
    avatar = MouthAvatar(mouth_fail=mouth_fail)
    player = MeterPlayer(avatar, fail=fail)
    app, _, _, output = build(tmp_path, avatar, player=player)
    app.settings = replace(
        app.settings,
        vts=replace(
            app.settings.vts,
            lipsync_enabled=True,
            mouth_parameter="MouthOpen",
            expected_model_id="model-1",
        ),
    )
    return app, avatar, player, output


@pytest.mark.parametrize("ending", ["normal", "stop", "shutdown", "error"])
def test_pipeline_zero_after_playback_end_stop_shutdown_or_failure(tmp_path, ending):
    async def run():
        app, avatar, player, output = pipeline(tmp_path, fail=ending == "error")
        app.submit(item())
        response = asyncio.create_task(app.process_next())
        await avatar.mouth_sent.wait()
        if ending == "normal":
            player.finish.set()
        elif ending == "stop":
            await app.stop()
        elif ending == "shutdown":
            await app.shutdown()
        await response
        assert output
        assert avatar.mouth[0] > 0
        assert avatar.mouth[-1] == 0
        assert not player.active
        count = len(avatar.mouth)
        player.levels.publish(1, 100)
        await asyncio.sleep(0.05)
        assert len(avatar.mouth) == count
        assert not list((tmp_path / "audio").glob("*.wav"))
        await app.shutdown()

    asyncio.run(asyncio.wait_for(run(), 3))


def test_vts_failure_keeps_audio_running_and_reports_unknown(tmp_path, caplog):
    async def run():
        app, avatar, player, output = pipeline(tmp_path, mouth_fail=True)
        app.submit(item())
        task = asyncio.create_task(app.process_next())
        await avatar.mouth_sent.wait()
        await asyncio.sleep(0.05)
        assert player.active and output
        player.finish.set()
        await task
        assert "avatar_status=unknown" in caplog.text
        assert "private mouth error" not in caplog.text
        assert not app._avatar_available
        await app.shutdown()

    asyncio.run(asyncio.wait_for(run(), 3))


def test_previous_playback_callback_cannot_move_next_silent_mouth(tmp_path):
    async def run():
        app, avatar, player, _ = pipeline(tmp_path)
        app.submit(item())
        first = asyncio.create_task(app.process_next())
        await avatar.mouth_sent.wait()
        old_levels = player.levels
        player.finish.set()
        await first
        avatar.mouth_sent.clear()
        player.finish.clear()
        player.level = 0
        before = len(avatar.mouth)
        app.submit(item(message_id="second"))
        second = asyncio.create_task(app.process_next())
        await avatar.mouth_sent.wait()
        old_levels.publish(1, 100)
        await asyncio.sleep(0.06)
        player.finish.set()
        await second
        assert all(value == 0 for value in avatar.mouth[before:])
        await app.shutdown()

    asyncio.run(asyncio.wait_for(run(), 3))


class MouthSocket(FakeSocket):
    def __init__(self):
        super().__init__()
        self.parameter = {"name": "MouthOpen", "min": 0, "max": 1}

    async def recv(self):
        request = self.calls[-1]
        kind = request["messageType"]
        if kind not in ("InputParameterListRequest", "InjectParameterDataRequest"):
            return await super().recv()
        data = (
            {}
            if kind == "InjectParameterDataRequest"
            else {
                "modelLoaded": self.loaded,
                "modelID": self.model,
                "defaultParameters": [self.parameter],
                "customParameters": [],
            }
        )
        return json.dumps(
            {
                "apiName": API_NAME,
                "apiVersion": "1.0",
                "requestID": request["requestID"],
                "messageType": kind.replace("Request", "Response"),
                "data": data,
            }
        )


def injections(socket):
    return [
        r["data"]
        for r in socket.calls
        if r["messageType"] == "InjectParameterDataRequest"
    ]


def test_vts_input_validation_injection_and_close_zero(tmp_path):
    async def run():
        socket = MouthSocket()
        client, _ = make_client(
            tmp_path, socket, lipsync_enabled=True, mouth_parameter="MouthOpen"
        )
        await client.connect(authenticate=True)
        await client.set_mouth_open(0.5)
        await client.set_mouth_open(1)
        assert (
            len(
                [
                    r
                    for r in socket.calls
                    if r["messageType"] == "InputParameterListRequest"
                ]
            )
            == 1
        )
        await client.aclose()
        assert [d["parameterValues"][0]["value"] for d in injections(socket)] == [
            0.5,
            1.0,
            0.0,
        ]
        assert all(
            d["mode"] == "set" and d["parameterValues"][0]["id"] == "MouthOpen"
            for d in injections(socket)
        )
        assert all("faceFound" not in d for d in injections(socket))

    asyncio.run(run())


def test_stop_during_model_lookup_drops_unsent_mouth_value(tmp_path):
    async def run():
        socket = MouthSocket()
        client, _ = make_client(
            tmp_path, socket, lipsync_enabled=True, mouth_parameter="MouthOpen"
        )
        await client.connect(authenticate=True)
        original = socket.recv
        waiting, release = asyncio.Event(), asyncio.Event()
        current = [True]

        async def delayed():
            if socket.calls[-1]["messageType"] == "CurrentModelRequest":
                waiting.set()
                await release.wait()
            return await original()

        socket.recv = delayed
        task = asyncio.create_task(client.set_mouth_open(1, valid=lambda: current[0]))
        await waiting.wait()
        current[0] = False
        release.set()
        await task
        assert not injections(socket)
        await client.aclose()

    asyncio.run(asyncio.wait_for(run(), 3))


def test_hung_vts_update_does_not_delay_audio_stop(tmp_path, caplog):
    async def run():
        app, avatar, player, _ = pipeline(tmp_path)
        started, release = asyncio.Event(), asyncio.Event()

        async def hung(value, *, valid=None):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()

        avatar.set_mouth_open = hung
        app.submit(item())
        response = asyncio.create_task(app.process_next())
        await started.wait()
        await asyncio.wait_for(app.stop(), 0.05)
        assert not player.active
        await response
        assert "avatar_status=unknown" in caplog.text
        assert not app._avatar_available
        release.set()
        await asyncio.sleep(0)
        await app.shutdown()

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("value", [-1, 2, float("nan"), True, "MouthOpen"])
def test_invalid_mouth_values_do_not_send(tmp_path, value):
    async def run():
        socket = MouthSocket()
        client, _ = make_client(
            tmp_path, socket, lipsync_enabled=True, mouth_parameter="MouthOpen"
        )
        await client.connect(authenticate=True)
        with pytest.raises(AvatarError):
            await client.set_mouth_open(value)
        assert not injections(socket)
        await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["missing", "range", "model", "disabled"])
def test_parameter_and_model_must_match_before_injection(tmp_path, mode):
    async def run():
        socket = MouthSocket()
        client, _ = make_client(
            tmp_path,
            socket,
            lipsync_enabled=mode != "disabled",
            mouth_parameter="MouthOpen",
        )
        await client.connect(authenticate=True)
        if mode == "missing":
            socket.parameter["name"] = "Other"
        if mode == "range":
            socket.parameter["max"] = 0.5
        if mode == "model":
            socket.model = "other"
        with pytest.raises(AvatarError):
            await client.set_mouth_open(0.5)
        assert not injections(socket)
        await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"lipsync_enabled": 1},
        {"lipsync_enabled": True},
        {"mouth_parameter": 5},
        {"lipsync_hz": 31},
        {"lipsync_hz": 0},
        {"lipsync_gain": math.inf},
        {"lipsync_smoothing": 0},
        {"lipsync_smoothing": 1.1},
    ],
)
def test_invalid_lipsync_config(kwargs):
    with pytest.raises(ValueError):
        VTSSettings(**kwargs)
