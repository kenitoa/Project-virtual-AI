import asyncio
import logging
from argparse import Namespace
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from virtual_ai import app as app_module
from virtual_ai.app import Application
from virtual_ai.audio.base import AudioError
from virtual_ai.config import AudioSettings, TTSSettings, load_config
from virtual_ai.safety import FALLBACK
from virtual_ai.schemas import ChatInput, Viewer
from virtual_ai.tts.base import TTSError

ROOT = Path(__file__).resolve().parents[1]


class LLM:
    def __init__(self, text="안녕하세요. **반갑습니다** `private_code`", events=None):
        self.text = text
        self.calls = []
        self.closed = False
        self.events = events if events is not None else []

    async def generate(self, messages):
        self.calls.append(messages)
        self.events.append("llm")
        return self.text

    async def aclose(self):
        self.closed = True


class TTS:
    def __init__(self, *, hold=False, late=False, fail=False, events=None):
        self.hold, self.late, self.fail = hold, late, fail
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = []
        self.closed = False
        self.events = events if events is not None else []

    async def synthesize(self, text, destination):
        self.calls.append((text, destination))
        self.events.append("tts")
        self.started.set()
        if self.hold and len(self.calls) == 1:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                if not self.late:
                    raise
                await self.release.wait()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fake audio")
        if self.fail and len(self.calls) == 1:
            raise TTSError("private server detail")
        return destination

    async def aclose(self):
        self.closed = True


class Player:
    def __init__(self, *, hold=False, fail=False, events=None):
        self.hold, self.fail = hold, fail
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = []
        self.stops = 0
        self.active = False
        self.closed = False
        self.events = events if events is not None else []

    async def play(self, path):
        assert path.exists()
        self.calls.append(path)
        self.events.append("play")
        self.active = True
        self.started.set()
        try:
            if self.hold and len(self.calls) == 1:
                await self.release.wait()
            if self.fail and len(self.calls) == 1:
                raise AudioError("private device detail")
            return True
        finally:
            self.active = False
            self.events.append("play_end")

    async def stop(self):
        self.stops += 1
        self.active = False
        self.release.set()

    async def aclose(self):
        await self.stop()
        self.closed = True


def item(text="hello", message_id="../../untrusted"):
    return ChatInput(Viewer("test", "viewer"), text, message_id)


def build(tmp_path, *, llm=None, tts=None, player=None, output=None, enabled=True):
    settings, character = load_config(ROOT / "configs/app.example.yaml")
    settings = replace(
        settings,
        tts=TTSSettings(enabled=enabled, ref_audio_path="ref.wav"),
        audio=AudioSettings(True),
    )
    llm = llm if llm is not None else LLM()
    tts = tts if tts is not None else TTS()
    player = player if player is not None else Player()
    output = output if output is not None else []
    app = Application(
        settings,
        character,
        llm,
        output.append,
        tts=tts,
        player=player,
        audio_directory=tmp_path / "audio",
    )
    return app, llm, tts, player, output


def test_speech_only_output_first_program_id_cleanup_and_privacy(tmp_path, caplog):
    async def run():
        events = []

        class Output(list):
            def append(self, value):
                events.append("display")
                super().append(value)

        output = Output()
        app, llm, tts, player, _ = build(
            tmp_path,
            llm=LLM(
                "<think>secret thought</think>안녕 **친구** `private_code`", events
            ),
            tts=TTS(events=events),
            player=Player(events=events),
            output=output,
        )
        app.submit(item())
        response = await app.process_next()
        assert output == [response.final]
        assert tts.calls[0][0] == response.speech == "안녕 친구"
        assert response.raw != response.final != response.speech
        path = tts.calls[0][1]
        assert path.parent == tmp_path / "audio"
        assert UUID(path.stem).version == 4
        assert path.name == f"{response.response_id}.wav"
        assert player.calls == [path]
        assert not path.exists()
        assert events == ["llm", "display", "tts", "play", "play_end"]
        assert app.history.messages(item().viewer)[-1]["content"] == response.final
        await app.shutdown()

    with caplog.at_level(logging.INFO):
        asyncio.run(run())
    assert "tts_seconds=" in caplog.text and "playback_seconds=" in caplog.text
    assert "secret thought" not in caplog.text and "private_code" not in caplog.text


@pytest.mark.parametrize("case", ["tts_disabled", "audio_disabled", "empty_speech"])
def test_disabled_voice_and_code_only_fallback(tmp_path, case):
    async def run():
        app, llm, tts, player, output = build(
            tmp_path,
            enabled=case != "tts_disabled",
            llm=LLM("`only code`" if case == "empty_speech" else "hello"),
        )
        if case == "audio_disabled":
            app.settings = replace(app.settings, audio=AudioSettings(False))
            app = Application(
                app.settings, app.character, llm, output.append, tts=tts, player=player
            )
        app.submit(item())
        result = await app.process_next()
        assert output == [result.final]
        if case == "empty_speech":
            assert result.blocked and "only code" not in result.speech
            assert tts.calls[0][0] == result.speech
            assert len(player.calls) == 1
            assert not list((tmp_path / "audio").glob("*.wav"))
        else:
            assert not tts.calls and not player.calls
            assert not (tmp_path / "audio").exists()
        await app.shutdown()

    asyncio.run(run())


def test_blocked_raw_never_reaches_tts(tmp_path):
    async def run():
        app, _, tts, _, output = build(tmp_path, llm=LLM("password=private_value"))
        app.submit(item())
        result = await app.process_next()
        assert result.blocked and output == [FALLBACK]
        assert tts.calls[0][0] == FALLBACK
        assert app.history.messages(item().viewer) == []
        await app.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["tts", "player"])
def test_voice_failure_preserves_text_history_and_next_input(tmp_path, stage, caplog):
    async def run():
        app, llm, tts, player, output = build(
            tmp_path,
            tts=TTS(fail=stage == "tts"),
            player=Player(fail=stage == "player"),
        )
        for n in range(2):
            app.submit(item(str(n), str(n)))
            response = await app.process_next(raise_errors=True)
            assert response.final in output
        assert len(llm.calls) == len(tts.calls) == 2
        assert len(player.calls) == (1 if stage == "tts" else 2)
        assert len(app.history.messages(item().viewer)) == 4
        assert not list((tmp_path / "audio").glob("*.wav"))
        assert "private server detail" not in " ".join(output)
        assert "private device detail" not in " ".join(output)
        await app.shutdown()

    with caplog.at_level(logging.WARNING):
        asyncio.run(run())
    assert "voice_status=failed" in caplog.text and "private" not in caplog.text


@pytest.mark.parametrize("late", [False, True])
def test_stop_during_synthesis_rejects_late_wav_then_recovers(tmp_path, late):
    async def run():
        app, llm, tts, player, output = build(tmp_path, tts=TTS(hold=True, late=late))
        app.submit(item("first", "first"))
        task = asyncio.create_task(app.process_next())
        await tts.started.wait()
        assert output
        app.submit(item("queued", "queued"))
        await app.stop()
        assert not len(app.queue)
        await tts.cancelled.wait()
        app.submit(item("next", "next"))
        tts.release.set()
        assert await task is not None
        assert not player.calls
        assert not list((tmp_path / "audio").glob("*.wav"))
        await app.process_next()
        assert len(llm.calls) == 2 and len(player.calls) == 1
        assert not list((tmp_path / "audio").glob("*.wav"))
        await app.shutdown()

    asyncio.run(asyncio.wait_for(run(), 3))


def test_stop_during_playback_drops_queue_and_stops_device(tmp_path):
    async def run():
        app, llm, tts, player, output = build(tmp_path, player=Player(hold=True))
        app.submit(item())
        task = asyncio.create_task(app.process_next())
        await player.started.wait()
        assert player.active
        app.submit(item("queued", "queued"))
        await app.stop()
        assert not player.active and player.stops == 1 and not len(app.queue)
        assert await task is not None
        assert output
        assert not player.calls[0].exists()
        app.submit(item("next", "next"))
        await app.process_next()
        assert len(llm.calls) == len(tts.calls) == len(player.calls) == 2
        await app.shutdown()

    asyncio.run(asyncio.wait_for(run(), 3))


def test_one_response_runs_through_playback_before_next_llm(tmp_path):
    async def run():
        app, llm, tts, player, _ = build(tmp_path, player=Player(hold=True))
        app.submit(item("first", "1"))
        app.submit(item("second", "2"))
        first = asyncio.create_task(app.process_next())
        second = asyncio.create_task(app.process_next())
        await player.started.wait()
        assert len(llm.calls) == len(tts.calls) == len(player.calls) == 1
        assert not second.done()
        player.release.set()
        await asyncio.gather(first, second)
        assert len(llm.calls) == 2
        await app.shutdown()

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("stage", ["tts", "player"])
def test_worker_cancel_propagates_and_cleans_audio(tmp_path, stage):
    async def run():
        app, _, tts, player, output = build(
            tmp_path,
            tts=TTS(hold=stage == "tts"),
            player=Player(hold=stage == "player"),
        )
        app.submit(item())
        worker = asyncio.create_task(app.run())
        await (tts.started if stage == "tts" else player.started).wait()
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
        assert output and not player.active
        assert not list((tmp_path / "audio").glob("*.wav"))
        await app.shutdown()
        assert not app.submit(item("after", "after"))

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("stage", ["tts", "player"])
def test_console_quit_cleans_all_clients_and_tasks(monkeypatch, tmp_path, stage):
    async def run():
        llm, tts, player = (
            LLM(),
            TTS(hold=stage == "tts"),
            Player(hold=stage == "player"),
        )
        monkeypatch.setattr(app_module, "FakeLLM", lambda: llm)
        monkeypatch.setattr(app_module, "GPTSoVITSClient", lambda settings: tts)
        monkeypatch.setattr(app_module, "WAVPlayer", lambda settings: player)
        data = yaml.safe_load(
            (ROOT / "configs/app.example.yaml").read_text(encoding="utf-8")
        )
        data["character_path"] = str(ROOT / "configs/character.yaml")
        data["tts"].update(enabled=True, ref_audio_path="ref.wav")
        data["audio"]["enabled"] = True
        config = tmp_path / "app.yaml"
        config.write_text(yaml.safe_dump(data), encoding="utf-8")
        commands = iter(["hello", "/quit"])

        async def get_input(*args):
            command = next(commands)
            if command == "/quit":
                await (tts.started if stage == "tts" else player.started).wait()
            return command

        monkeypatch.setattr(asyncio, "to_thread", get_input)
        monkeypatch.chdir(tmp_path)
        before = asyncio.all_tasks()
        await app_module.run_cli(
            Namespace(config=str(config), backend="mock", once=None)
        )
        assert llm.closed and tts.closed and player.closed and not player.active
        assert asyncio.all_tasks() == before
        assert not list((tmp_path / "generated_audio").glob("*.wav"))

    asyncio.run(asyncio.wait_for(run(), 3))


def test_shutdown_waits_for_late_synthesis_cleanup(tmp_path):
    async def run():
        app, _, tts, player, _ = build(tmp_path, tts=TTS(hold=True, late=True))
        app.submit(item())
        response = asyncio.create_task(app.process_next())
        await tts.started.wait()
        shutdown = asyncio.create_task(app.shutdown())
        await tts.cancelled.wait()
        assert not shutdown.done()
        assert not app.submit(item("new", "new"))
        tts.release.set()
        await shutdown
        await response
        assert not player.calls
        assert not list((tmp_path / "audio").glob("*.wav"))
        assert app._voice is None

    asyncio.run(asyncio.wait_for(run(), 3))


def test_tts_returned_path_cannot_select_or_delete_other_files(tmp_path):
    unrelated = tmp_path / "keep.wav"
    unrelated.write_bytes(b"keep")

    class OtherPathTTS(TTS):
        async def synthesize(self, text, destination):
            await super().synthesize(text, destination)
            return unrelated

    async def run():
        app, _, tts, player, _ = build(tmp_path, tts=OtherPathTTS())
        app.submit(item())
        await app.process_next()
        assert player.calls == [tts.calls[0][1]]
        assert unrelated.read_bytes() == b"keep"
        await app.shutdown()

    asyncio.run(run())


def test_disabled_cli_does_not_create_voice_clients(monkeypatch):
    def unexpected(*args):
        pytest.fail("Text-only CLI constructed a voice client")

    monkeypatch.setattr(app_module, "GPTSoVITSClient", unexpected)
    monkeypatch.setattr(app_module, "WAVPlayer", unexpected)
    asyncio.run(
        app_module.run_cli(
            Namespace(
                config=str(ROOT / "configs/app.example.yaml"),
                backend="mock",
                once="hello",
            )
        )
    )


def test_cli_closes_other_clients_when_player_close_fails(monkeypatch, tmp_path):
    async def run():
        llm, tts, player = LLM(), TTS(), Player()

        async def failing_close():
            player.closed = True
            raise AudioError("private close error")

        player.aclose = failing_close
        monkeypatch.setattr(app_module, "FakeLLM", lambda: llm)
        monkeypatch.setattr(app_module, "GPTSoVITSClient", lambda settings: tts)
        monkeypatch.setattr(app_module, "WAVPlayer", lambda settings: player)
        data = yaml.safe_load(
            (ROOT / "configs/app.example.yaml").read_text(encoding="utf-8")
        )
        data["character_path"] = str(ROOT / "configs/character.yaml")
        data["tts"].update(enabled=True, ref_audio_path="ref.wav")
        data["audio"]["enabled"] = True
        config = tmp_path / "app.yaml"
        config.write_text(yaml.safe_dump(data), encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        with pytest.raises(AudioError):
            await app_module.run_cli(
                Namespace(config=str(config), backend="mock", once="hello")
            )
        assert llm.closed and tts.closed and player.closed
        assert not list((tmp_path / "generated_audio").glob("*.wav"))

    asyncio.run(run())
