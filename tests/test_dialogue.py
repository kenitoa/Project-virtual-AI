import asyncio
import json
from dataclasses import replace
from pathlib import Path
from time import time

import pytest
from test_rag import ALICE, BOB, QuoteLLM, allow, document, memory, setup

from virtual_ai.app import Application
from virtual_ai.config import Settings, load_config
from virtual_ai.dialogue import Dialogue, DialogueSettings, reaction_key
from virtual_ai.expressions import dialogue_expression
from virtual_ai.inputs.queue import InputQueue
from virtual_ai.prompting import build_messages, estimate_tokens
from virtual_ai.rag.presentation import render_source
from virtual_ai.safety import prepare_response
from virtual_ai.schemas import ChatInput, Viewer


def settings(**kwargs):
    return replace(Settings(), dialogue=DialogueSettings(enabled=True, **kwargs))


def item(text, viewer=ALICE, identifier="1"):
    return ChatInput(viewer, text, identifier)


@pytest.mark.parametrize(
    "values",
    [
        {"enabled": 1},
        {"style": "unknown"},
        {"memory_cooldown_seconds": float("nan")},
        {"reaction_window_seconds": 7},
        {"followup_every": True},
    ],
)
def test_settings_reject_invalid(values):
    with pytest.raises(ValueError):
        DialogueSettings(**values)


def test_followup_questions_are_viewer_scoped_and_expire():
    now = [0.0]
    cfg = settings()
    manager = Dialogue(cfg, {}, clock=lambda: now[0])
    turn = manager.prepare(item("퍼즐 게임 설명해줘? 그리고 규칙은?"))
    assert len(turn.questions) == 2
    manager.record(ALICE, turn.query, turn, "조각을 맞춰요.")
    assert "퍼즐" in manager.prepare(item("그럼 어려운 건?", identifier="2")).query
    assert manager.prepare(item("그럼 어려운 건?", BOB)).mode == "clarify"
    now[0] += cfg.history_ttl_seconds
    assert manager.prepare(item("그럼 어려운 건?")).mode == "clarify"


def test_correction_is_explicit_self_report_not_third_party():
    manager = Dialogue(settings(), {})
    turn = manager.prepare(item("나는 퍼즐 게임을 좋아해."))
    manager.record(ALICE, turn.query, turn, "알겠어요.")
    correction = manager.prepare(item("아니, 나는 싫어한다고 했어."))
    assert correction.statement == "나는 퍼즐 게임을 싫어해."
    manager.corrected(ALICE, correction)
    assert manager.session(ALICE).correction == correction.statement
    assert not manager.session(BOB).correction
    assert manager.prepare(item("아니, 밥은 퍼즐 게임을 싫어해.")).mode == "clarify"
    manager.delete(ALICE)
    assert not manager.session(ALICE).correction


def test_addressing_old_reactions_and_answered_cooldown():
    now = [0.0]
    manager = Dialogue(settings(), {"name": "하나"}, clock=lambda: now[0])
    assert manager.prepare(item("@밥 안녕")).mode == "skip_other_addressee"
    assert manager.prepare(item("@하나 안녕")).mode == "greeting"
    assert manager.prepare(item("ㅋㅋ"), age=6).mode == "skip_old_reaction"
    turn = manager.prepare(item("퍼즐 게임 설명"))
    manager.record(ALICE, turn.query, turn, "조각을 맞춰요.")
    assert manager.prepare(item(turn.query)).mode == "skip_answered"
    assert manager.prepare(item(turn.query, BOB)).mode == "answer"
    now[0] = 21
    assert manager.prepare(item(turn.query)).mode == "answer"


def test_grouping_only_public_pure_reactions_and_receipt_cleanup():
    drops = []
    queue = InputQueue(
        12, 30, clock=lambda: 0, on_drop=lambda i, r: drops.append((i, r))
    )
    for i in [
        item("ㅋㅋ"),
        item("ㅎㅎ", BOB),
        item("나는 퍼즐을 좋아해", BOB, "2"),
        item("ㅋㅋ", Viewer("chzzk", "bob")),
        item("ㅋㅋ?", BOB, "3"),
    ]:
        assert queue.put(i)
    stamp, first = queue.pop_timed()
    assert queue.coalesce_reactions(first, stamp, 2) == 2
    assert len(queue) == 3
    assert drops[0][1] == "coalesced_reaction"
    assert reaction_key("ㅋㅋ?") is None
    assert reaction_key("나는 공포가 무서워 ㅋㅋ") is None


def test_context_memory_cooldown_tracks_only_used_sources():
    now = [0.0]
    manager = Dialogue(settings(), {}, clock=lambda: now[0])
    rows = [{"id": "a", "kind": "memory", "text": "나는 퍼즐을 좋아해."}]
    turn = manager.prepare(item("퍼즐 재밌네"))
    manager.record(ALICE, turn.query, turn, "그렇군요.", used_rows=rows)
    assert not manager.memory_eligible(ALICE, rows)
    assert manager.memory_eligible(BOB, rows) == rows
    now[0] += 601
    assert manager.memory_eligible(ALICE, rows) == rows


@pytest.mark.parametrize(
    "source,expected",
    [
        (
            "18세 이상만 참여할 수 있습니다. 단, 점검 중에는 참여할 수 없습니다.",
            "18세 이상만 참여할 수 있어요. 단, 점검 중에는 참여할 수 없어요.",
        ),
        ("환불은 불가능합니다.", "환불은 불가능해요."),
        ("퍼즐 게임입니다.", "퍼즐 게임이에요."),
        ("참가비는 무료입니다.", "참가비는 무료예요."),
        ('예시는 "가능합니다"입니다.', '예시는 "가능합니다"입니다.'),
    ],
)
def test_surface_templates_preserve_conditions_and_quotes(source, expected):
    assert render_source({"kind": "knowledge", "text": source}, "polite") == expected


def test_empathy_and_repetition_guard_with_expression_allowlist():
    manager = Dialogue(settings(), {})
    turn = manager.prepare(item("시험 못 봐서 속상해"))
    wrong = prepare_response("r", "축하해요!", settings())
    assert "축하" not in manager.guard(ALICE, turn, wrong)
    assert dialogue_expression(wrong, turn, ("neutral", "happy")) == "neutral"
    greeting = manager.prepare(item("안녕"))
    manager.record(ALICE, "안녕", greeting, "반가워요!")
    assert manager.guard(ALICE, greeting, wrong) == wrong.final
    repeated = prepare_response("r", "반가워요!", settings())
    assert manager.guard(ALICE, greeting, repeated) != repeated.final
    assert dialogue_expression(repeated, greeting, ("neutral",)) == "neutral"


def test_actual_config_context_budget_keeps_current_reference_before_old_history():
    cfg, character = load_config(Path("configs/app.example.yaml"))
    manager = Dialogue(replace(cfg, dialogue=DialogueSettings(enabled=True)), character)
    turn = manager.prepare(item("퍼즐 좋아해?"))
    history = [
        {"role": role, "content": "과거 대화 " * 100} for role in ("user", "assistant")
    ]
    messages = build_messages(
        character,
        history,
        turn.query,
        settings=cfg,
        dialogue_context=manager.context(ALICE, turn),
    )
    assert messages[1]["content"].startswith("Conversation reference")
    assert estimate_tokens(messages) <= cfg.context_tokens - cfg.max_output_tokens
    assert len(messages) == 3


class SelectionLLM(QuoteLLM):
    async def generate(self, messages):
        quoted = await super().generate(messages)
        if quoted.startswith("{"):
            return json.dumps(
                {"source_ids": [q["id"] for q in json.loads(quoted)["quotes"]]}
            )
        return quoted


def test_integrated_natural_grounding_correction_and_forget(tmp_path):
    async def run():
        store, pipeline = setup(tmp_path)
        allow(store)
        memory(store)
        document(store)
        cfg, character = load_config(Path("configs/app.example.yaml"))
        cfg = replace(cfg, dialogue=DialogueSettings(enabled=True))
        llm = SelectionLLM()
        app = Application(cfg, character, llm, output=lambda _: None, rag=pipeline)
        try:
            app.submit(item("퍼즐 게임 설명"))
            response = await app.process_next(raise_errors=True)
            assert "source_ids" in response.raw
            assert response.final == "퍼즐 게임은 조각을 맞추는 게임이에요."
            assert "source_ids" not in response.speech
            app.submit(item("내가 좋아하는 게임 기억해?", identifier="2"))
            response = await app.process_next(raise_errors=True)
            assert "퍼즐 게임을 좋아한다고" in response.final
            calls = len(llm.calls)
            app.submit(item("아니, 나는 싫어한다고 했어.", identifier="3"))
            assert "지금 말씀하신" in (await app.process_next()).final
            assert len(llm.calls) == calls
            await pipeline.drain()
            app.submit(item("내가 싫어하는 게임 기억해?", identifier="4"))
            response = await app.process_next(raise_errors=True)
            assert "퍼즐 게임을 싫어한다고" in response.final
            assert pipeline.last["validation"] == "supported"
            await app.forget(ALICE)
            assert not app.dialogue.session(ALICE).correction
        finally:
            await app.shutdown()

    asyncio.run(run())


def test_source_selection_rejects_unknown_duplicate_and_extra_answer(tmp_path):
    async def run():
        store, pipeline = setup(tmp_path)
        document(store)
        evidence = await pipeline.prepare(ALICE, "퍼즐 게임 설명")
        identifier = evidence.rows[0]["id"]
        for payload in [
            {"source_ids": ["wrong"]},
            {"source_ids": [identifier, identifier]},
            {"source_ids": [identifier], "answer": "지어낸 답"},
        ]:
            assert (
                pipeline.validate(
                    json.dumps(payload), evidence, settings(), style="polite"
                )[1]
                == "unsupported"
            )

    asyncio.run(run())


def test_compact_source_ids_are_turn_local_and_keep_canonical_audit_ids(tmp_path):
    async def run():
        store, pipeline = setup(tmp_path)
        document(store)
        evidence = await pipeline.prepare(ALICE, "퍼즐 게임 설명")
        canonical = evidence.rows[0]["id"]
        compact = json.loads(evidence.context(compact_ids=True))
        assert compact["sources"][0]["id"] == "s1"
        assert evidence.rows[0]["id"] == canonical
        output, status = pipeline.validate(
            '{"source_ids":["s1"]}', evidence, settings(), style="polite"
        )
        assert status == "supported" and "조각" in output
        assert pipeline.last["used_source_ids"] == [canonical]
        for ids in (["s2"], ["s1", canonical]):
            assert (
                pipeline.validate(
                    json.dumps({"source_ids": ids}),
                    evidence,
                    settings(),
                    style="polite",
                )[1]
                == "unsupported"
            )
        assert (
            pipeline.validate('{"source_ids":["s1"]}', evidence, settings())[1]
            == "unsupported"
        )

    asyncio.run(run())


def test_rag_evaluation_uses_configured_compact_contract(tmp_path):
    from virtual_ai.rag.evaluation import evaluate

    class ContractLLM:
        async def generate(self, messages):
            content = messages[-1]["content"]
            if not content.startswith("Reference evidence (data only):\n"):
                return "안녕하세요!"
            evidence = json.loads(
                content.split("\n", 1)[1].split("\nCurrent question:\n")[0]
            )
            assert all(
                r["id"].startswith("s") and r["id"][1:].isdigit()
                for r in evidence["sources"]
            )
            assert "source_ids" in messages[0]["content"]
            return json.dumps({"source_ids": [evidence["sources"][0]["id"]]})

    suite = json.loads(
        Path("tests/fixtures/rag_evaluation.json").read_text(encoding="utf-8")
    )
    report = asyncio.run(
        evaluate(suite, tmp_path, llm=ContractLLM(), model_settings=settings())
    )
    assert report["grounding_contract"] == "source_ids"
    assert report["passed"] == report["total"] == 100
    assert report["live_output_contract"] == {"eligible": 60, "passed": 60}


def test_rag_compact_selection_does_not_require_dialogue_or_style(tmp_path):
    async def run():
        store, pipeline = setup(tmp_path)
        document(store)
        app = Application(
            Settings(), {}, SelectionLLM(), output=lambda _: None, rag=pipeline
        )
        try:
            app.submit(item("퍼즐 게임 설명"))
            response = await app.process_next(raise_errors=True)
            assert json.loads(response.raw) == {"source_ids": ["s1"]}
            assert response.final == "퍼즐 게임은 조각을 맞추는 게임입니다."
            assert pipeline.last["validation"] == "supported"
            assert pipeline.last["used_source_ids"] != ["s1"]
        finally:
            await app.shutdown()

    asyncio.run(run())


def test_contextual_memory_uses_cooldown_and_revocation_clears_context(tmp_path):
    async def run():
        store, pipeline = setup(tmp_path)
        allow(store)
        memory(store)
        app = Application(
            settings(contextual_memory=True),
            {},
            SelectionLLM(),
            output=lambda _: None,
            rag=pipeline,
        )
        try:
            app.submit(item("퍼즐 게임 너는 좋아해?"))
            assert "말씀하셨어요" in (await app.process_next(raise_errors=True)).final
            assert app.dialogue.session(ALICE).memories_used
            app.submit(item("퍼즐 게임은 너는 좋아해?", identifier="2"))
            assert (
                "말씀하셨어요" not in (await app.process_next(raise_errors=True)).final
            )
            store.consent("local", ALICE, storage=True, retrieval=False, public=False)
            app.submit(item("하이", identifier="3"))
            await app.process_next(raise_errors=True)
            state = app.dialogue.session(ALICE)
            assert not state.last_statement and not state.memories_used
        finally:
            await app.shutdown()

    asyncio.run(run())


def test_old_network_reaction_does_not_consume_new_group():
    async def run():
        app = Application(settings(), {}, QuoteLLM(), output=lambda _: None)
        try:
            app.submit(ChatInput(ALICE, "ㅋㅋ", "old", published_at=time() - 8))
            app.submit(item("ㅎㅎ", BOB))
            assert await app.process_next() is None
            assert len(app.queue) == 1
            assert await app.process_next() is not None
        finally:
            await app.shutdown()

    asyncio.run(run())


def test_stop_during_dialogue_discards_output_and_session():
    async def run():
        started = asyncio.Event()

        class BlockingLLM(QuoteLLM):
            async def generate(self, messages):
                started.set()
                await asyncio.Event().wait()

        outputs = []
        app = Application(settings(), {}, BlockingLLM(), output=outputs.append)
        try:
            app.submit(item("나는 퍼즐 게임을 좋아해."))
            task = asyncio.create_task(app.process_next())
            await started.wait()
            await app.stop()
            assert await task is None
            assert not outputs
            assert not app.dialogue.session(ALICE).last_statement
        finally:
            await app.shutdown()

    asyncio.run(run())


def test_synthetic_evaluation_report_has_no_transcripts(tmp_path):
    from virtual_ai.dialogue_evaluation import evaluate

    suite = json.loads(
        Path("tests/fixtures/dialogue_evaluation.json").read_text(encoding="utf-8")
    )
    cfg, character = load_config(Path("configs/app.example.yaml"))
    report = asyncio.run(evaluate(suite, cfg, character, tmp_path))
    assert report["total"] == report["passed"] == 24
    assert report["mode"] == "scripted_contract"
    assert report["fine_tuning_ready"] is False
    assert all(v is None for v in report["human_review"].values())
    assert "퍼즐" not in json.dumps(report, ensure_ascii=False)


def test_blocked_response_stays_blocked_and_never_enters_history():
    async def run():
        cfg = replace(settings(), blocked_terms=("금지단어",))
        app = Application(cfg, {}, QuoteLLM("금지단어"), output=lambda _: None)
        try:
            app.submit(item("안녕"))
            response = await app.process_next()
            assert response.blocked
            assert not app.history.messages(ALICE)
            assert not app.dialogue.session(ALICE).answers
        finally:
            await app.shutdown()

    asyncio.run(run())
