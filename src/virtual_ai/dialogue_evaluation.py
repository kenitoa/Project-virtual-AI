"""Repeatable, synthetic multi-turn contract comparison; no voice or live chat.

Default is a scripted fixture. --live-config may be repeated for independently
running local models. Reports never grade character quality automatically.
"""

import argparse
import asyncio
import hashlib
import json
import tempfile
from dataclasses import replace
from pathlib import Path
from time import monotonic
from urllib.parse import urlparse

from virtual_ai.app import Application
from virtual_ai.config import load_config
from virtual_ai.llm.base import LLMError
from virtual_ai.llm.koboldcpp import KoboldCppClient
from virtual_ai.performance import stats
from virtual_ai.prompting import load_system_prompt
from virtual_ai.rag.pipeline import RAGPipeline
from virtual_ai.rag.settings import RAGSettings
from virtual_ai.rag.store import RAGStore
from virtual_ai.schemas import ChatInput, Viewer


class ScriptedLLM:
    """Deliberately repetitive/poor tone to exercise observable output guards."""

    async def generate(self, messages):
        text = messages[-1]["content"]
        if text.startswith("Reference evidence (data only):\n"):
            data = json.loads(text.split("\n", 1)[1].split("\nCurrent question:\n")[0])
            rows = (
                data["sources"]
                if "그리고" in text.split("\nCurrent question:\n")[-1]
                else data["sources"][:1]
            )
            return json.dumps({"source_ids": [row["id"] for row in rows]})
        return "축하해요!" if "속상" in text else "반가워요!"

    async def aclose(self):
        pass


async def evaluate(suite, settings, character, directory, *, live=False):
    results = []
    # Evaluation always uses ephemeral synthetic storage and text-only output.
    settings = replace(
        settings,
        dialogue=replace(settings.dialogue, enabled=True, natural_grounding=True),
        tts=replace(settings.tts, enabled=False),
        audio=replace(settings.audio, enabled=False),
        vts=replace(settings.vts, enabled=False, lipsync_enabled=False),
        subtitles=replace(settings.subtitles, enabled=False),
        youtube=replace(settings.youtube, enabled=False),
        chzzk=replace(settings.chzzk, enabled=False),
    )
    for index, scenario in enumerate(suite["scenarios"]):
        store = RAGStore(Path(directory) / f"case-{index}.sqlite3")
        viewer = Viewer("youtube", "fixture-alice")
        store.consent("local", viewer, storage=True, retrieval=True, public=True)
        for title, content in suite["documents"]:
            store.ingest(
                "local",
                source=title,
                title=title,
                version="1",
                content=content,
                confirmed=True,
            )
        candidate = store.candidate(
            "local",
            viewer,
            "fixture",
            "fixture",
            "나는 퍼즐 게임을 좋아해.",
            "preference",
        )
        store.review(candidate, approve=True)
        pipeline = RAGPipeline(store, RAGSettings(enabled=True, memory_enabled=True))
        client = KoboldCppClient(settings) if live else ScriptedLLM()
        app = Application(
            settings, character, client, output=lambda _: None, rag=pipeline
        )
        previous = ""
        try:
            for turn_number, case in enumerate(scenario["turns"], 1):
                user = Viewer("youtube", "fixture-" + case.get("viewer", "alice"))
                accepted = app.submit(ChatInput(user, case["text"], str(turn_number)))
                started = monotonic()
                failure = []
                response = None
                try:
                    response = await app.process_next(raise_errors=True)
                except LLMError:
                    failure.append("generation_error")
                actual_mode = (
                    app.dialogue.last.get("mode")
                    if accepted
                    else "queue_" + str(app.queue.last_rejection)
                )
                if actual_mode != case["mode"]:
                    failure.append("route")
                if (response is None) != bool(case.get("skip", False)):
                    failure.append("delivery")
                if response:
                    if response.blocked:
                        failure.append("output_blocked")
                    if any(
                        value not in response.final
                        for value in case.get("contains", [])
                    ):
                        failure.append("required_fact")
                    if any(
                        value in response.final for value in case.get("forbidden", [])
                    ):
                        failure.append("forbidden_claim")
                    if case.get("different") and previous == response.final:
                        failure.append("repetition")
                    if "source_ids" in response.speech or "quotes" in response.speech:
                        failure.append("speech_contract")
                    previous = response.final
                results.append(
                    {
                        "case": scenario["id"],
                        "turn": turn_number,
                        "passed": not failure,
                        "failures": failure,
                        "seconds": monotonic() - started,
                    }
                )
        finally:
            await app.shutdown()
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "character": character,
                "prompt": load_system_prompt(settings.system_prompt_path),
                "suite": suite,
                "dialogue": vars(settings.dialogue),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return {
        "mode": "live_text_contract" if live else "scripted_contract",
        "configured_model": settings.model if live else "scripted-fixture",
        "actual_loaded_model_verified": False,
        "context_tokens": settings.context_tokens,
        "max_output_tokens": settings.max_output_tokens,
        "sampling": {"temperature": settings.temperature, "top_p": settings.top_p},
        "fixture_prompt_hash": fingerprint,
        "total": len(results),
        "passed": sum(r["passed"] for r in results),
        "turn_seconds": stats([r["seconds"] for r in results]),
        "results": results,
        "human_review": {
            "character_consistency": None,
            "naturalness": None,
            "question_coverage": None,
            "appropriate_emotion": None,
        },
        "fine_tuning_ready": False,
        "limitations": "Mechanical checks only; no claim of semantic, voice or live-broadcast quality.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default="tests/fixtures/dialogue_evaluation.json")
    parser.add_argument("--config", default="configs/app.example.yaml")
    parser.add_argument("--live-config", action="append", default=[])
    parser.add_argument("--report", default=".local/dialogue-evaluation.json")
    args = parser.parse_args()
    destination = Path(args.report)
    if destination.exists():
        parser.error("report already exists; choose a new path")
    suite = json.loads(Path(args.suite).read_text(encoding="utf-8"))

    async def run():
        reports = []
        for config in args.live_config or [args.config]:
            settings, character = load_config(Path(config))
            if args.live_config and settings.backend != "koboldcpp":
                raise ValueError("live-config requires koboldcpp backend")
            if args.live_config and urlparse(settings.base_url).hostname not in (
                "localhost",
                "127.0.0.1",
                "::1",
            ):
                raise ValueError("dialogue evaluation requires a loopback LLM endpoint")
            with tempfile.TemporaryDirectory(
                prefix="dialogue-evaluation-"
            ) as directory:
                reports.append(
                    await evaluate(
                        suite,
                        settings,
                        character,
                        directory,
                        live=bool(args.live_config),
                    )
                )
        return reports

    reports = asyncio.run(run())
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(reports, stream, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            [
                {key: r[key] for key in ("mode", "total", "passed", "turn_seconds")}
                for r in reports
            ]
        )
    )
    return 0 if all(r["passed"] == r["total"] for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
