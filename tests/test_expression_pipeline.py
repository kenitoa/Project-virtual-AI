import asyncio
from argparse import Namespace
from dataclasses import replace

import pytest
import yaml
from test_voice_pipeline import LLM, ROOT, TTS, Player, item

from virtual_ai import app as app_module
from virtual_ai.app import Application
from virtual_ai.config import AudioSettings, TTSSettings, VTSSettings, load_config
from virtual_ai.integrations.vts.base import AvatarError
from virtual_ai.schemas import Response


class Avatar:
    def __init__(self, events=None, *, fail=None, hold=None, late=False):
        self.events = events if events is not None else []
        self.fail, self.hold, self.late = fail, hold, late
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.calls = []
        self.closed = False

    async def connect(self):
        await self.operation("connect")

    async def operation(self, name):
        self.events.append(name)
        self.calls.append(name)
        if name == self.fail:
            raise AvatarError("private avatar detail")
        if name == self.hold:
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                if not self.late:
                    raise
                await self.release.wait()

    async def set_expression(self, expression):
        await self.operation(expression)

    async def reset(self):
        await self.operation("reset")

    async def aclose(self):
        self.closed = True
        await self.operation("close")


def build(
    tmp_path,
    avatar,
    *,
    tts=None,
    player=None,
    llm=None,
    policy=None,
    timeout=0.1,
    enabled=True,
):
    settings, character = load_config(ROOT / "configs/app.example.yaml")
    settings = replace(
        settings,
        tts=TTSSettings(enabled=True, ref_audio_path="ref.wav"),
        audio=AudioSettings(True),
        vts=VTSSettings(enabled=enabled, request_timeout_seconds=timeout),
    )
    output = []
    tts, player = tts or TTS(), player or Player()
    app = Application(
        settings,
        character,
        llm or LLM(),
        output.append,
        tts=tts,
        player=player,
        avatar=avatar,
        expression_policy=policy,
        audio_directory=tmp_path / "audio",
    )
    return app, tts, player, output


def test_response_position_and_default_policy(tmp_path):
    assert Response("id", "raw", "final", "speech", True).blocked is True
    assert Response("id", "raw", "final", "speech", True).expression == "neutral"

    async def run():
        avatar = Avatar()
        app, _, _, _ = build(tmp_path, avatar)
        app.submit(item())
        result = await app.process_next()
        assert result.expression == "neutral"
        assert avatar.calls == ["neutral", "reset"]
        await app.shutdown()

    asyncio.run(run())


def test_order_validated_expression_and_cleanup(tmp_path):
    async def run():
        events = []
        avatar = Avatar(events)
        app, _, player, output = build(
            tmp_path,
            avatar,
            tts=TTS(events=events),
            player=Player(events=events),
            policy=lambda response: "happy",
        )
        app.output = lambda text: (output.append(text), events.append("text"))
        app.submit(item())
        response = await app.process_next()
        assert response.expression == "happy"
        assert events == ["text", "tts", "happy", "play", "play_end", "reset"]
        assert app.history.messages(item().viewer)
        assert not player.calls[0].exists()
        await app.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("candidate", ["raw-hotkey-id", {}, None, "happy"])
@pytest.mark.parametrize("blocked", [False, True])
def test_policy_allowlist_and_blocked_neutral(tmp_path, candidate, blocked):
    async def run():
        avatar = Avatar()
        app, _, _, _ = build(
            tmp_path,
            avatar,
            llm=LLM("" if blocked else "Hello"),
            policy=lambda response: candidate,
        )
        app.submit(item())
        result = await app.process_next()
        assert result.expression == (
            "happy" if not blocked and candidate == "happy" else "neutral"
        )
        assert avatar.calls[0] == result.expression
        await app.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["happy", "reset"])
def test_avatar_failure_preserves_text_voice_and_next_response(tmp_path, caplog, stage):
    async def run():
        avatar = Avatar(fail=stage)
        app, _, player, output = build(
            tmp_path, avatar, policy=lambda response: "happy"
        )
        for i in range(2):
            app.submit(item(message_id=str(i)))
            await app.process_next()
        assert len(output) == len(player.calls) == 2
        assert avatar.calls.count("happy") == 1
        assert "avatar_status=unknown" in caplog.text
        assert "private avatar detail" not in caplog.text
        assert not list((tmp_path / "audio").glob("*.wav"))
        await app.shutdown()

    asyncio.run(run())


def test_stop_during_late_tts_never_applies_expression(tmp_path):
    async def run():
        avatar = Avatar()
        app, tts, player, _ = build(
            tmp_path,
            avatar,
            tts=TTS(hold=True, late=True),
            policy=lambda response: "happy",
        )
        app.submit(item())
        response = asyncio.create_task(app.process_next())
        await tts.started.wait()
        await app.stop()
        await tts.cancelled.wait()
        tts.release.set()
        await response
        assert not avatar.calls and not player.calls
        await app.shutdown()

    asyncio.run(asyncio.wait_for(run(), 2))


def test_audio_stop_precedes_reset_and_old_cleanup_blocks_new_avatar(tmp_path):
    async def run():
        events = []
        avatar = Avatar(events, hold="reset")
        player = Player(hold=True, events=events)
        app, _, _, _ = build(
            tmp_path, avatar, player=player, timeout=1, policy=lambda response: "happy"
        )
        app.submit(item())
        first = asyncio.create_task(app.process_next())
        await player.started.wait()
        original_stop = player.stop

        async def stop():
            events.append("audio_stop")
            await asyncio.sleep(0)
            await original_stop()

        player.stop = stop
        await app.stop()
        await avatar.started.wait()
        assert events.index("audio_stop") < events.index("reset")
        app.submit(item(message_id="second"))
        second = asyncio.create_task(app.process_next())
        await asyncio.sleep(0)
        assert avatar.calls == ["happy", "reset"]
        avatar.release.set()
        await first
        await second
        assert avatar.calls == ["happy", "reset", "happy", "reset"]
        await app.shutdown()

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("stage", ["happy", "reset"])
def test_timeout_quarantines_late_requests_without_blocking_voice(tmp_path, stage):
    async def run():
        avatar = Avatar(hold=stage, late=True)
        app, _, player, _ = build(
            tmp_path, avatar, timeout=0.02, policy=lambda response: "happy"
        )
        app.submit(item())
        await app.process_next()
        await avatar.cancelled.wait()
        previous = list(avatar.calls)
        app.submit(item(message_id="second"))
        await app.process_next()
        assert len(player.calls) == 2
        assert avatar.calls == previous
        avatar.release.set()
        await asyncio.sleep(0)
        await app.shutdown()

    asyncio.run(asyncio.wait_for(run(), 2))


def test_stop_during_avatar_wait_is_immediate_and_stale_audio_is_blocked(tmp_path):
    async def run():
        avatar = Avatar(hold="happy", late=True)
        app, _, player, _ = build(
            tmp_path, avatar, timeout=1, policy=lambda response: "happy"
        )
        app.submit(item())
        first = asyncio.create_task(app.process_next())
        await avatar.started.wait()
        await asyncio.wait_for(app.stop(), 0.1)
        assert player.stops == 1
        await avatar.cancelled.wait()
        await first
        assert not player.calls
        avatar.release.set()
        await asyncio.sleep(0)
        app.submit(item(message_id="second"))
        await app.process_next()
        assert avatar.calls == ["happy"]
        assert len(player.calls) == 1
        await app.shutdown()

    asyncio.run(asyncio.wait_for(run(), 2))


@pytest.mark.parametrize("failure", [None, "connect", "close"])
def test_cli_saved_auth_failure_is_optional_and_all_clients_close(
    tmp_path, monkeypatch, caplog, failure
):
    async def run():
        avatar, llm, tts, player = Avatar(fail=failure), LLM(), TTS(), Player()
        monkeypatch.setattr(app_module, "VTSClient", lambda settings: avatar)
        monkeypatch.setattr(app_module, "FakeLLM", lambda: llm)
        monkeypatch.setattr(app_module, "GPTSoVITSClient", lambda settings: tts)
        monkeypatch.setattr(app_module, "WAVPlayer", lambda settings: player)
        data = yaml.safe_load(
            (ROOT / "configs/app.example.yaml").read_text(encoding="utf-8")
        )
        data["character_path"] = str(ROOT / "configs/character.yaml")
        data["tts"].update(enabled=True, ref_audio_path="ref.wav")
        data["audio"]["enabled"] = data["vts"]["enabled"] = True
        config = tmp_path / "app.yaml"
        config.write_text(yaml.safe_dump(data), encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        await app_module.run_cli(
            Namespace(config=str(config), backend="mock", once="hello")
        )
        assert avatar.closed and llm.closed and tts.closed and player.closed
        assert len(player.calls) == 1
        assert avatar.calls[0] == "connect"
        if failure:
            assert "avatar_status=unknown" in caplog.text

    asyncio.run(run())


def test_disabled_avatar_and_tts_failure_never_apply(tmp_path):
    async def run():
        for enabled, tts in [(False, TTS()), (True, TTS(fail=True))]:
            avatar = Avatar()
            app, _, _, _ = build(tmp_path, avatar, enabled=enabled, tts=tts)
            app.submit(item())
            await app.process_next()
            assert not avatar.calls
            await app.shutdown()

    asyncio.run(run())


def test_repeated_stop_and_shutdown_wait_for_reset_without_detaching(tmp_path):
    async def run():
        avatar = Avatar(hold="reset")
        app, _, player, _ = build(
            tmp_path,
            avatar,
            player=Player(hold=True),
            timeout=1,
            policy=lambda response: "happy",
        )
        app.submit(item())
        response = asyncio.create_task(app.process_next())
        await player.started.wait()
        await app.stop()
        await avatar.started.wait()
        await app.stop()
        shutdown = asyncio.create_task(app.shutdown())
        await asyncio.sleep(0)
        assert not shutdown.done()
        assert not player.active
        assert not app.submit(item(message_id="new"))
        avatar.release.set()
        await shutdown
        await response
        await asyncio.sleep(0)
        assert avatar.calls == ["happy", "reset"]
        assert not app._avatar_pending
        assert not list((tmp_path / "audio").glob("*.wav"))
        await avatar.aclose()
        assert avatar.closed

    asyncio.run(asyncio.wait_for(run(), 3))
