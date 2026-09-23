import asyncio
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from test_cancellation import ControlledLLM, item
from test_voice_pipeline import LLM, TTS, Player
from test_youtube_chat import event, page

from virtual_ai.app import Application
from virtual_ai.audio.base import AudioError
from virtual_ai.config import AudioSettings, TTSSettings, YouTubeSettings, load_config
from virtual_ai.integrations.youtube import YouTubeChat

ROOT = Path(__file__).resolve().parents[1]


def test_console_controls_and_model_commands_never_execute(
    tmp_path, monkeypatch, capsys
):
    from virtual_ai.inputs.console import console

    async def run():
        app = make_app(tmp_path, llm=LLM(text="/panic"))
        app.submit(item("hello"))
        await app.process_next()
        assert not app.runtime.panic
        commands = iter(
            ["/panic", "/resume", "/recover", "/resume", "/unmute", "/status", "/quit"]
        )
        monkeypatch.setattr("builtins.input", lambda _: next(commands))
        await console(app)
        assert not app.runtime.input_locked and not app.runtime.muted
        await app.shutdown()

    asyncio.run(run())
    output = capsys.readouterr().out
    assert "운영:" in output
    assert "복구 거절:" in output


def test_youtube_enabled_starts_paused(tmp_path):
    app = make_app(tmp_path)
    settings = replace(app.settings, youtube=YouTubeSettings(True, "chat"))
    live = Application(settings, app.character, LLM(), tts=TTS(), player=Player())
    assert live.runtime.paused
    assert not live.submit(item("startup"))


def test_delayed_chat_from_pause_is_dropped_after_resume_and_cursor_advances(
    tmp_path, monkeypatch
):
    async def run():
        monkeypatch.setenv("YOUTUBE_ACCESS_TOKEN", "test-token")
        app = make_app(tmp_path, live=True)
        old = event("during-pause", age=0)
        requests = []

        async def handler(request):
            requests.append(request.url.params.get("pageToken"))
            if len(requests) == 1:
                return httpx.Response(200, json=page([], "cursor1"))
            if len(requests) == 2:
                assert app.resume()
                return httpx.Response(200, json=page([old], "cursor2"))
            return httpx.Response(200, json=page([event("fresh")], ended=True))

        async def sleep(_):
            pass

        chat = YouTubeChat(
            YouTubeSettings(True, "chat"),
            max_age=30,
            transport=httpx.MockTransport(handler),
            sleep=sleep,
            accept_after=lambda: app.runtime.accept_after,
        )
        await chat.run(app.submit)
        assert requests == [None, "cursor1", "cursor2"]
        assert len(app.queue) == 1
        assert app.queue.pop().message_id == "fresh"
        await app.shutdown()

    asyncio.run(run())


def make_app(tmp_path, *, llm=None, tts=None, player=None, live=False):
    settings, character = load_config(ROOT / "configs/app.example.yaml")
    settings = replace(
        settings,
        tts=TTSSettings(
            enabled=True, ref_audio_path="reference.wav", prompt_text="test"
        ),
        audio=AudioSettings(enabled=True),
    )
    return Application(
        settings,
        character,
        llm or LLM(),
        output=lambda _: None,
        tts=tts or TTS(),
        player=player or Player(),
        audio_directory=tmp_path,
        live=live,
    )


@pytest.mark.parametrize("command", ["pause", "panic"])
def test_lock_precedes_delayed_device_cleanup(tmp_path, command):
    async def run():
        class DelayedPlayer(Player):
            async def stop(self):
                self.started.set()
                await self.release.wait()

        player = DelayedPlayer()
        app = make_app(tmp_path, player=player)
        operation = asyncio.create_task(getattr(app, command)())
        await player.started.wait()
        assert not app.submit(item("during cleanup"))
        assert not app.resume()
        assert not app.recover()
        assert not app.unmute()
        player.release.set()
        await operation
        if command == "panic":
            assert not app.resume()
            assert app.recover()
            assert app.runtime.paused and app.runtime.muted
        assert app.resume()
        assert len(app.queue) == 0
        await app.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["llm", "tts", "playback"])
def test_panic_during_delayed_pipeline_blocks_late_output_and_requires_recovery(
    tmp_path, phase
):
    async def run():
        llm = ControlledLLM() if phase == "llm" else LLM()
        tts = TTS(hold=phase == "tts", late=phase == "tts")
        player = Player(hold=phase == "playback")
        app = make_app(tmp_path, llm=llm, tts=tts, player=player)
        app.submit(item("old"))
        task = asyncio.create_task(app.process_next())
        observed = {"llm": llm, "tts": tts, "playback": player}[phase]
        await asyncio.wait_for(observed.started.wait(), 2)
        assert app.status()["phase"] == phase
        await app.panic()
        assert not app.submit(item("discarded"))
        assert not app.resume()
        tts.release.set()
        await task
        assert not list(tmp_path.glob("*"))
        if phase != "playback":
            assert not player.calls
        assert app.recover()
        assert not app.submit(item("still paused"))
        assert app.resume()
        assert app.runtime.muted
        assert app.unmute()
        app.submit(item("fresh"))
        await app.process_next()
        assert player.calls
        await app.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["llm", "tts", "playback"])
def test_mute_keeps_text_and_worker_alive_without_late_audio(tmp_path, phase):
    async def run():
        llm = ControlledLLM() if phase == "llm" else LLM()
        tts = TTS(hold=phase == "tts", late=phase == "tts")
        player = Player(hold=phase == "playback")
        app = make_app(tmp_path, llm=llm, tts=tts, player=player)
        outputs = []
        app.output = outputs.append
        app.submit(item("old"))
        task = asyncio.create_task(app.process_next())
        await asyncio.wait_for(
            {"llm": llm, "tts": tts, "playback": player}[phase].started.wait(), 2
        )
        await app.mute()
        assert not app.unmute()  # Cleanup/generation is still in progress.
        if phase == "llm":
            llm.release.set()
        tts.release.set()
        await task
        assert outputs
        count = len(player.calls)
        app.submit(item("text while muted"))
        await app.process_next()
        assert len(player.calls) == count
        assert app.unmute()
        app.submit(item("audible again"))
        await app.process_next()
        assert len(player.calls) == count + 1
        assert not list(tmp_path.glob("*"))
        await app.shutdown()

    asyncio.run(run())


def test_live_pause_drop_and_viewer_commands_are_data(tmp_path):
    async def run():
        app = make_app(tmp_path, live=True)
        assert not app.submit(item("backlog"))
        assert app.resume()
        for command in [
            "/pause",
            "/resume",
            "/mute",
            "/unmute",
            "/status",
            "/panic",
            "/recover",
        ]:
            assert app.submit(item(command))
            await app.process_next()
            assert not app.runtime.input_locked and not app.runtime.muted
        await app.pause()
        for i in range(30):
            assert not app.submit(item(str(i)))
        assert len(app.queue) == 0
        await app.stop()
        await app.forget()
        assert app.runtime.paused
        assert app.resume()
        await app.shutdown()

    asyncio.run(run())


def test_cleanup_failure_and_llm_block_cannot_be_reset_by_operator(tmp_path):
    async def run():
        class BrokenPlayer(Player):
            async def stop(self):
                raise AudioError("device cleanup unknown")

        app = make_app(tmp_path, player=BrokenPlayer())
        await app.panic()
        assert app.runtime.cleanup_failed
        assert not app.recover() and not app.resume() and not app.unmute()
        assert not app.submit(item("unsafe"))
        app = make_app(tmp_path)
        app.llm.cleanup_confirmed = False
        await app.pause()
        assert not app.resume()
        assert app.status()["llm"] == "cleanup_unconfirmed"
        await app.shutdown()

    asyncio.run(run())
