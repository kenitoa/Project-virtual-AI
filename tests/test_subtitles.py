import asyncio
from argparse import Namespace
from dataclasses import replace

import pytest
import yaml
from test_voice_pipeline import LLM, ROOT, TTS, Player, item

from virtual_ai import app as app_module
from virtual_ai import subtitles as subtitle_module
from virtual_ai.app import Application
from virtual_ai.config import AudioSettings, SubtitleSettings, TTSSettings, load_config
from virtual_ai.llm.base import LLMError
from virtual_ai.safety import FALLBACK
from virtual_ai.schemas import Response
from virtual_ai.subtitles import SubtitleError, SubtitleWriter


def writer(tmp_path, enabled=True):
    path = tmp_path / ".local/obs-subtitle.txt"
    return SubtitleWriter(SubtitleSettings(enabled, str(path))), path


def build(tmp_path, *, llm=None, tts=None, player=None, subtitles=None):
    settings, character = load_config(ROOT / "configs/app.example.yaml")
    target, path = writer(tmp_path)
    settings = replace(
        settings,
        subtitles=target.settings,
        tts=TTSSettings(enabled=True, ref_audio_path="ref.wav"),
        audio=AudioSettings(True),
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
        subtitles=subtitles or target,
        audio_directory=tmp_path / "audio",
    )
    return app, path, tts, player, output


def test_writer_only_final_utf8_fixed_path_and_no_temporary_residue(tmp_path):
    target, path = writer(tmp_path)
    target.write(
        Response(
            "../../other",
            "private raw",
            "검사된 답변입니다.\n두 번째 줄",
            "different speech",
        )
    )
    assert path.read_bytes() == "검사된 답변입니다.\n두 번째 줄".encode("utf-8")
    target.write(Response("id", "private", "짧은 답변", "speech"))
    assert path.read_text(encoding="utf-8") == "짧은 답변"
    assert list(path.parent.iterdir()) == [path]
    target.clear()
    assert path.read_bytes() == b""
    with pytest.raises(SubtitleError):
        target.write("console error")


def test_disabled_writer_creates_nothing(tmp_path):
    target, path = writer(tmp_path, False)
    target.clear()
    target.write(Response("id", "raw", "final", "speech"))
    assert not path.parent.exists()


def test_failed_atomic_replace_preserves_old_file_and_cleans_temp(
    tmp_path, monkeypatch
):
    target, path = writer(tmp_path)
    target.write(Response("id", "raw", "old", "speech"))

    def denied(*args):
        raise PermissionError("private path")

    monkeypatch.setattr(subtitle_module.os, "replace", denied)
    with pytest.raises(SubtitleError, match="update failed"):
        target.write(Response("next", "raw", "new", "speech"))
    assert path.read_text(encoding="utf-8") == "old"
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize(
    "options",
    [
        {"enabled": 1},
        {"path": None},
        {"path": "outside.txt"},
        {"path": ".local/token.json"},
        {"path": ""},
    ],
)
def test_invalid_subtitle_settings(options):
    with pytest.raises(ValueError):
        SubtitleSettings(**options)


def test_config_relative_path_and_unknown_keys(tmp_path):
    path = tmp_path / "app.yaml"
    data = {
        "character_path": str(ROOT / "configs/character.yaml"),
        "subtitles": {"enabled": True, "path": ".local/captions.txt"},
    }
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    settings, _ = load_config(path)
    assert settings.subtitles.path == str(tmp_path / ".local/captions.txt")
    assert not (tmp_path / ".local").exists()
    data["subtitles"]["unknown"] = True
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError, match="subtitles"):
        load_config(path)


@pytest.mark.parametrize("voice_error", [False, True])
def test_answer_only_not_console_error_and_stop_clear(tmp_path, voice_error):
    async def run():
        app, path, _, player, output = build(tmp_path, tts=TTS(fail=voice_error))
        assert path.read_bytes() == b""
        app.submit(item())
        response = await app.process_next()
        assert path.read_text(encoding="utf-8") == response.final
        if voice_error:
            assert len(output) == 2
            assert output[-1] not in path.read_text(encoding="utf-8")
        else:
            assert player.calls
        await app.stop()
        assert path.read_bytes() == b""
        await app.shutdown()

    asyncio.run(run())


def test_blocked_answer_uses_inspected_fallback_not_raw(tmp_path):
    async def run():
        app, path, _, _, _ = build(tmp_path, llm=LLM("<think>private thought</think>"))
        app.submit(item())
        response = await app.process_next()
        assert response.blocked
        assert path.read_text(encoding="utf-8") == FALLBACK
        assert "private" not in path.read_text(encoding="utf-8")
        await app.shutdown()

    asyncio.run(run())


def test_late_llm_after_stop_never_repopulates_subtitle(tmp_path):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        class LateLLM(LLM):
            async def generate(self, messages):
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                return "late answer"

        app, path, _, _, _ = build(tmp_path, llm=LateLLM())
        app.submit(item())
        task = asyncio.create_task(app.process_next())
        await started.wait()
        await app.stop()
        release.set()
        await task
        assert path.read_bytes() == b""
        await app.shutdown()

    asyncio.run(asyncio.wait_for(run(), 3))


def test_subtitle_failure_preserves_voice_and_hides_private_detail(tmp_path, caplog):
    class Broken:
        def clear(self):
            raise SubtitleError("private file location")

        def write(self, response):
            raise SubtitleError("private file location")

    async def run():
        app, _, _, player, output = build(tmp_path, subtitles=Broken())
        app.submit(item())
        response = await app.process_next()
        assert output == [response.final]
        assert player.calls
        await app.stop()
        await app.shutdown()
        assert "subtitle_status=update_failed state=unknown" in caplog.text
        assert "private file location" not in caplog.text

    asyncio.run(run())


def test_audio_abort_precedes_subtitle_clear_and_old_stop_keeps_new_answer(tmp_path):
    async def run():
        player = Player()
        app, path, _, _, _ = build(tmp_path, player=player)
        app.submit(item())
        first = await app.process_next()
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_stop():
            entered.set()
            await release.wait()

        player.stop = slow_stop
        stop = asyncio.create_task(app.stop())
        await entered.wait()
        assert path.read_text(encoding="utf-8") == first.final
        app.llm.text = "새 답변입니다."
        app.submit(item(text="next request", message_id="next"))
        second = await app.process_next()
        release.set()
        await stop
        assert path.read_text(encoding="utf-8") == second.final
        await app.shutdown()
        assert path.read_bytes() == b""

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("llm_error", [False, True])
def test_cli_clears_stale_and_final_subtitles_even_on_error(
    tmp_path, monkeypatch, llm_error
):
    class Client(LLM):
        async def generate(self, messages):
            if llm_error:
                raise LLMError("private error")
            return await super().generate(messages)

    async def run():
        _, path = writer(tmp_path)
        path.parent.mkdir()
        path.write_text("stale caption", encoding="utf-8")
        data = {
            "character_path": str(ROOT / "configs/character.yaml"),
            "subtitles": {"enabled": True, "path": ".local/obs-subtitle.txt"},
        }
        config = tmp_path / "app.yaml"
        config.write_text(yaml.safe_dump(data), encoding="utf-8")
        llm = Client()
        monkeypatch.setattr(app_module, "FakeLLM", lambda: llm)
        args = Namespace(config=str(config), backend="mock", once="hello")
        if llm_error:
            with pytest.raises(LLMError):
                await app_module.run_cli(args)
        else:
            await app_module.run_cli(args)
        assert llm.closed
        assert path.read_bytes() == b""

    asyncio.run(run())


def test_disabled_cli_does_not_construct_writer(monkeypatch):
    def unexpected(*args):
        pytest.fail("disabled subtitles created a writer")

    monkeypatch.setattr(app_module, "SubtitleWriter", unexpected)
    asyncio.run(
        app_module.run_cli(
            Namespace(
                config=str(ROOT / "configs/app.example.yaml"),
                backend="mock",
                once="hello",
            )
        )
    )
