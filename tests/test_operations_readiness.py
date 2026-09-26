import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from test_dialogue import item, settings
from test_operator_controls import make_app
from test_rag import ALICE, BOB
from test_voice_pipeline import TTS

from virtual_ai.dialogue import Dialogue
from virtual_ai.operations import Observations
from virtual_ai.operator_panel import OperatorPanel
from virtual_ai.rag.coverage import coverage, missing_notice
from virtual_ai.schemas import Viewer
from virtual_ai.session_report import fingerprint, warmup, write_report
from virtual_ai.speech import pronunciation, validate_pronunciations


def test_bounded_observations_keep_failures_without_content():
    monitor = Observations(2)
    for identifier, status in (
        ("one", "completed"),
        ("two", "cancelled"),
        ("three", "failed"),
    ):
        for stage, value, at in (
            ("received", "ok", 0),
            ("selected", "ok", 1),
            ("first_playback_callback", "ok", 4),
            ("playback_finished", status, 5),
            ("cleanup_complete", "settled", 6),
        ):
            monitor.observe(identifier, stage, value, at)
    snapshot = monitor.snapshot()
    assert snapshot["recent_sample_count"] == 2
    assert snapshot["outcomes_since_start"] == {
        "audio:completed": 1,
        "audio:cancelled": 1,
        "audio:failed": 1,
    }
    assert (
        snapshot["all_observed_outcomes"]["input_to_first_callback_seconds"]["n"] == 2
    )
    assert "one" not in json.dumps(snapshot)
    monitor.observe("discard", "received", "ok", 0)
    monitor.observe("discard", "discard", "overflow", 0)
    assert not monitor.active
    monitor.observe("unknown", "received", "ok", 0)
    monitor.observe("unknown", "first_playback_callback", "unobserved", 0)
    monitor.observe("unknown", "cleanup_complete", "settled", 0)
    assert (
        monitor.snapshot()["all_observed_outcomes"]["input_to_first_callback_seconds"][
            "n"
        ]
        == 0
    )


def test_public_topics_only_completed_approved_knowledge_and_platform_scoped():
    now = [10.0]
    manager = Dialogue(settings(shared_context=True), {}, clock=lambda: now[0])
    a = Viewer("youtube", "one")
    b = Viewer("youtube", "two")
    turn = manager.prepare(item("참가 규칙?", a))
    manager.record(a, turn.query, turn, "검증된 답변")
    rows = [{"kind": "knowledge", "title": "퍼즐 참가 규칙"}]
    manager.delivered(a, "cancelled", used_rows=rows)
    assert manager.prepare(item("그럼 그거는?", b)).mode == "clarify"
    manager.delivered(a, "completed", used_rows=rows)
    assert "퍼즐" in manager.prepare(item("그럼 그거는?", b)).query
    assert (
        manager.prepare(item("그럼 그거는?", Viewer("chzzk", "two"))).mode == "clarify"
    )
    now[0] += 121
    assert manager.prepare(item("그럼 그거는?", b)).mode == "clarify"
    manager.delivered(
        a, "completed", used_rows=[{"kind": "memory", "title": "private"}]
    )
    assert not manager.public_topic("youtube")
    manager.delete(a)
    manager.delivered(a, "completed", used_rows=rows)
    assert a not in manager.sessions and not manager.public_topics


def test_pronunciation_cannot_bypass_configured_output_policy(tmp_path):
    from test_voice_pipeline import LLM

    async def run():
        app = make_app(tmp_path, llm=LLM(text="OBS 안내입니다."))
        app.settings = replace(
            app.settings,
            blocked_terms=("금지어",),
            tts=replace(app.settings.tts, pronunciations={"OBS": "금지어"}),
        )
        assert app.submit(item("설명해줘"))
        await app.process_next()
        assert not app.tts.calls and not app.player.calls
        assert app.last_delivery["playback"] == "failed"
        await app.shutdown()

    asyncio.run(run())


def test_modes_operator_context_and_incomplete_delivery():
    manager = Dialogue(settings(fast_reactions=True, shared_context=True), {})
    assert manager.prepare(item("안녕")).direct
    assert manager.prepare(item("ㅋㅋ"), mode="focus").mode == "skip_low_priority"
    assert manager.prepare(item("규칙은?"), mode="quiet").mode == "skip_quiet"
    assert manager.prepare(
        item("그거 언제야?", BOB), public_topic="퍼즐 방송"
    ).query.startswith("퍼즐 방송")
    assert manager.prepare(item("응", BOB)).mode == "clarify"
    turn = manager.prepare(item("규칙은?"))
    manager.record(ALICE, turn.query, turn, "답변")
    manager.delivered(ALICE, "cancelled")
    assert manager.prepare(item("규칙은?")).mode != "skip_answered"
    assert json.loads(manager.context(ALICE, turn))["previous_delivery"] == "cancelled"


def test_pronunciation_preserves_numbers_and_does_not_cascade():
    mapping = {"OBS": "오비에스", "오비에스": "다른말"}
    validate_pronunciations(mapping)
    assert (
        pronunciation("OBS 가격은 -10원, 10%가 아니에요.", mapping)
        == "오비에스 가격은 -10원, 10%가 아니에요."
    )
    for invalid in ({"10": "십"}, {"OBS": "-10"}, {"OBS": ""}):
        with pytest.raises(ValueError):
            validate_pronunciations(invalid)


def test_explicit_question_coverage_reports_missing_not_semantic_proof():
    result = coverage(
        "참가비 얼마고 신청은 언제까지야?", [{"text": "참가비는 1,000원입니다."}]
    )
    assert result["covered"] == ["fee"]
    assert result["missing"] == ["deadline"]
    assert "마감" in missing_notice(result)
    assert result["semantic_relevance"] == "unverified"
    assert not coverage("가격은?", [{"text": "가격에 대해 안내합니다."}])["covered"]


def test_panel_real_http_security_and_controller_methods(tmp_path):
    async def run():
        app = make_app(tmp_path)
        panel = OperatorPanel(app)
        url = await panel.start()
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                page = await client.get(url)
                assert page.status_code == 200
                assert (
                    "frame-ancestors 'none'" in page.headers["content-security-policy"]
                )
                assert (await client.get(url + "/status")).status_code == 403
                headers = {"X-Operator-Token": panel.token}
                assert (
                    await client.get(url + "/status", headers=headers)
                ).status_code == 200
                assert (
                    await client.post(
                        url + "/command",
                        json={"action": "panic"},
                        headers=headers | {"Origin": "https://attacker.example"},
                    )
                ).status_code == 400
                assert not app.runtime.panic
                assert (
                    await client.get(url, headers={"Host": "attacker.example"})
                ).status_code == 400
                assert (
                    await client.post(
                        url + "/command", json={"action": "panic"}, headers=headers
                    )
                ).status_code == 200
                assert app.runtime.panic and app.runtime.muted and app.runtime.paused
                reply = await client.post(
                    url + "/command", json={"action": "resume"}, headers=headers
                )
                assert reply.json()["accepted"] is False
                assert (
                    await client.post(
                        url + "/command", json={"action": "recover"}, headers=headers
                    )
                ).json()["accepted"]
                assert app.runtime.paused and app.runtime.muted
                await client.post(
                    url + "/command",
                    json={"action": "say", "text": "/panic"},
                    headers=headers,
                )
                assert not app.runtime.panic
                assert (
                    await client.post(
                        url + "/command", json={"action": "shell"}, headers=headers
                    )
                ).status_code == 400
        finally:
            await panel.aclose()
            await app.shutdown()

    asyncio.run(run())


def test_warmup_no_playback_and_private_report(tmp_path):
    async def run():
        app = make_app(tmp_path)
        app.audio_directory = tmp_path / "audio"
        app.warmup_status = await warmup(app)
        assert app.warmup_status["status"] == "completed"
        assert not app.player.calls and not list(app.audio_directory.glob("*.wav"))
        identity = fingerprint(app)
        await app.shutdown()
        report = tmp_path / "report.json"
        write_report(report, app, identity)
        data = json.loads(report.read_text(encoding="utf-8"))
        assert not data["acceptance"]["release_approved"]
        assert "source_sha256" in data["identity"]
        with pytest.raises(FileExistsError):
            write_report(report, app, identity)
        failed = make_app(tmp_path, tts=TTS(fail=True))
        failed.audio_directory = tmp_path / "failed"
        assert (await warmup(failed))["status"] == "failed"
        assert not failed._voice_enabled
        await failed.shutdown()

    asyncio.run(run())


def test_memory_panel_is_scoped_and_requires_confirmed_permissions(tmp_path):
    from test_rag import allow, memory, setup

    async def run():
        app = make_app(tmp_path)
        store, app.rag = setup(tmp_path)
        allow(store, ALICE)
        allow(store, BOB)
        memory(store, ALICE)
        memory(store, BOB, text="나는 공포 게임을 좋아해.")
        panel = OperatorPanel(app)
        target = {"platform": "youtube", "user": "alice"}
        private = await panel.dispatch({"action": "memory_inspect", **target})
        assert "퍼즐" in str(private) and "공포" not in str(private)
        assert "퍼즐" not in json.dumps(panel.status(), ensure_ascii=False)
        with pytest.raises(ValueError):
            await panel.dispatch({"action": "memory_forget", **target})
        with pytest.raises(ValueError):
            await panel.dispatch(
                {
                    "action": "memory_consent",
                    **target,
                    "identity_verified": True,
                    "storage": True,
                    "retrieval": True,
                    "public": True,
                }
            )
        await panel.dispatch(
            {"action": "memory_forget", **target, "identity_verified": True}
        )
        assert not store.inspect("local", ALICE)["memories"]
        assert store.inspect("local", BOB)["memories"]
        assert app.runtime.paused
        await app.shutdown()

    asyncio.run(run())


def test_partial_rag_answer_retains_conditions_and_names_missing_question(tmp_path):
    from test_rag import document, setup

    from virtual_ai.config import Settings
    from virtual_ai.rag.retrieval import Plan

    async def run():
        store, pipeline = setup(tmp_path)
        pipeline.settings = replace(pipeline.settings, question_coverage=True)
        document(store, "참가비는 1,000원입니다. 단, 사전 신청자만 참가할 수 있습니다.")
        evidence = await pipeline.prepare(
            ALICE,
            "참가비 얼마고 신청은 언제까지야?",
            route=Plan("knowledge", "참가비 얼마고 신청은 언제까지야?"),
        )
        text, status = pipeline.validate(
            '{"source_ids":["s1"]}', evidence, Settings(), selection_only=True
        )
        assert status == "supported"
        assert (
            "사전 신청자만" in text and "마감" in text and "확인하지 못했어요" in text
        )
        assert pipeline.last["coverage"]["missing"] == ["deadline"]

    asyncio.run(run())


def test_acceptance_missing_evidence_never_passes():
    from virtual_ai.readiness import assess

    result = assess({"schema": 1})
    assert result["first_audio_target_met"] is None
    assert not result["duration_met"] and not result["cleanup_confirmed"]
    assert not result["release_approved"]
    assert result["non_completion_rate"] is None


def test_question_target_expiry_and_interruption():
    now = [0.0]
    manager = Dialogue(settings(), {}, clock=lambda: now[0])
    turn = manager.prepare(item("요즘 퍼즐 게임을 해."))
    manager.record(ALICE, turn.query, turn, "퍼즐 게임 좋아하세요?")
    manager.delivered(ALICE, "completed")
    assert manager.prepare(item("응")).mode == "acknowledgement"
    assert manager.prepare(item("응", BOB)).mode == "clarify"
    now[0] = 61
    assert manager.prepare(item("응")).mode == "clarify"
    manager.record(ALICE, turn.query, turn, "퍼즐 게임 좋아하세요?")
    manager.delivered(ALICE, "cancelled")
    assert manager.prepare(item("응")).mode == "clarify"
