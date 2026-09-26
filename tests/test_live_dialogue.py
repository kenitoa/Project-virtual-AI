"""Live-path contracts with synthetic viewers and real HTTP adapter mocks."""

import asyncio
import json
from dataclasses import replace
from time import monotonic, time

import httpx
import pytest
from test_dialogue import SelectionLLM, item, settings
from test_koboldcpp import answer
from test_rag import ALICE, QuoteLLM, document, setup
from test_voice_pipeline import TTS, Player

from virtual_ai.app import Application
from virtual_ai.config import AudioSettings, Settings, TTSSettings
from virtual_ai.dialogue import Dialogue
from virtual_ai.llm.koboldcpp import KoboldCppClient
from virtual_ai.safety import prepare_response
from virtual_ai.schemas import ChatInput, Viewer


@pytest.mark.parametrize(
    "question,raw,forbidden",
    [
        ("잘 지냈어?", "잘 지냈어요?", "?"),
        ("속상하지 않아, 합격했어!", "합격을 기뻐해 주셔서 감사합니다.", "감사"),
    ],
)
def test_observed_real_model_role_and_echo_failures(question, raw, forbidden):
    cfg = settings()
    dialogue = Dialogue(cfg, {})
    turn = dialogue.prepare(item(question))
    final = dialogue.guard(ALICE, turn, prepare_response("fixture", raw, cfg))
    assert forbidden not in final


@pytest.mark.parametrize(
    "text,mode",
    [
        ("너는 참여 규칙 알려줘, 실패해서 속상해", "answer"),
        ("합격했어! 참여 조건은?", "answer"),
        ("너는 최신 뉴스 알려줘", "answer"),
        ("오늘 피곤해서 쉬러 왔어", "chat"),
        ("퇴근하고 왔어", "chat"),
        ("잘 지냈어?", "chat"),
        ("프랑스 수도", "answer"),
        ("속상하지 않아, 합격했어!", "celebrate"),
    ],
)
def test_mixed_intents_prioritize_facts_but_social_chat_reaches_llm(text, mode):
    turn = Dialogue(settings(), {}).prepare(item(text))
    assert turn.mode == mode


def test_mixed_emotion_question_still_uses_verified_evidence(tmp_path):
    async def run():
        store, pipeline = setup(tmp_path)
        document(
            store,
            "18세 이상만 참여할 수 있습니다. 점검 중에는 참여할 수 없습니다.",
            title="참여 규칙",
        )
        app = Application(
            settings(), {}, SelectionLLM(), output=lambda _: None, rag=pipeline
        )
        try:
            app.submit(item("너는 참여 규칙 알려줘, 실패해서 속상해"))
            response = await app.process_next(raise_errors=True)
            assert "18세 이상만" in response.final
            assert "참여할 수 없어요" in response.final
            assert pipeline.last["validation"] == "supported"
        finally:
            await app.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("confirmed", [True, False])
def test_public_deadline_aborts_own_key_and_only_reuses_confirmed_server(confirmed):
    async def run():
        calls, aborts, outputs = [], [], []

        async def handler(request):
            if request.url.path == "/api/extra/abort":
                aborts.append(json.loads(request.content)["genkey"])
                return httpx.Response(
                    200, json={"success": confirmed, "done": confirmed}
                )
            if request.url.path == "/api/extra/perf":
                return httpx.Response(200, json={"idle": 1, "queue": 0})
            calls.append(json.loads(request.content))
            if len(calls) == 1:
                await asyncio.Event().wait()
            return httpx.Response(200, json=answer("반가워요!"))

        cfg = replace(
            settings(generation_timeout_seconds=0.02), server_abort_enabled=True
        )
        llm = KoboldCppClient(cfg, httpx.MockTransport(handler))
        app = Application(cfg, {}, llm, output=outputs.append)
        try:
            app.submit(item("안녕"))
            assert await app.process_next() is None
            assert aborts == [calls[0]["genkey"]]
            assert not outputs and not app.history.messages(ALICE)
            assert llm.cleanup_confirmed is confirmed
            assert app.runtime.paused is not confirmed
            accepted = app.submit(item("하이", identifier="next"))
            assert accepted is confirmed
            response = await app.process_next()
            assert (response is not None) is confirmed
            assert len(calls) == (2 if confirmed else 1)
            if confirmed:
                assert calls[1]["genkey"] != calls[0]["genkey"]
        finally:
            await app.shutdown()

    asyncio.run(run())


def test_old_public_question_is_dropped_before_model_but_console_is_not():
    async def run():
        llm = QuoteLLM()
        app = Application(settings(), {}, llm, output=lambda _: None)
        try:
            app.submit(ChatInput(ALICE, "퍼즐 설명", "old", published_at=time() - 21))
            assert await app.process_next() is None
            assert not llm.calls
            app.submit(
                ChatInput(
                    Viewer("console", "local"),
                    "안녕",
                    "local",
                    published_at=time() - 21,
                )
            )
            assert await app.process_next() is not None
        finally:
            await app.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize(
    "limits",
    [
        {"public_response_ttl_seconds": 0.02},
        {"generation_timeout_seconds": 0.02},
    ],
)
@pytest.mark.parametrize("frozen_clock", [False, True])
def test_even_adapter_suppressing_cancellation_cannot_display_expired_answer(
    limits, frozen_clock, monkeypatch
):
    if frozen_clock:
        now = monotonic()
        monkeypatch.setattr("virtual_ai.app.monotonic", lambda: now)

    class LateLLM(QuoteLLM):
        async def generate(self, messages):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return "반가워요!"

    async def run():
        outputs = []
        app = Application(
            settings(**limits),
            {},
            LateLLM(),
            output=outputs.append,
        )
        try:
            app.submit(item("안녕"))
            assert await app.process_next() is None
            assert not outputs and not app.history.messages(ALICE)
        finally:
            await app.shutdown()

    asyncio.run(run())


def test_expired_synthesis_never_starts_playback(tmp_path, monkeypatch):
    async def run():
        clock = [monotonic()]
        monkeypatch.setattr("virtual_ai.app.monotonic", lambda: clock[0])

        class DelayedTTS(TTS):
            async def synthesize(self, text, destination):
                await super().synthesize(text, destination)
                clock[0] += 21

        cfg = replace(
            settings(),
            tts=TTSSettings(enabled=True, ref_audio_path="fixture.wav"),
            audio=AudioSettings(enabled=True),
        )
        player = Player()
        app = Application(
            cfg,
            {},
            QuoteLLM(),
            output=lambda _: None,
            tts=DelayedTTS(),
            player=player,
            audio_directory=tmp_path,
        )
        try:
            app.submit(item("안녕"))
            assert await app.process_next() is not None
            assert not player.calls
            assert not list(tmp_path.glob("*.wav"))
        finally:
            await app.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize(
    "values",
    [
        {"temperature": True},
        {"temperature": float("nan")},
        {"temperature": 2.1},
        {"top_p": 0},
        {"top_p": float("inf")},
        {"top_p": "0.9"},
    ],
)
def test_sampling_config_is_validated(values):
    with pytest.raises(ValueError):
        Settings(**values)


def test_configured_sampling_reaches_actual_http_adapter():
    async def run():
        async def handler(request):
            payload = json.loads(request.content)
            assert payload["temperature"] == 0.65
            assert payload["top_p"] == 0.9
            return httpx.Response(200, json=answer())

        client = KoboldCppClient(
            Settings(temperature=0.65, top_p=0.9), httpx.MockTransport(handler)
        )
        try:
            assert (
                await client.generate([{"role": "user", "content": "hello"}])
                == "answer"
            )
        finally:
            await client.aclose()

    asyncio.run(run())
