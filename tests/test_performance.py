import asyncio
import json
import logging
from types import SimpleNamespace

from test_operator_controls import make_app

from virtual_ai.performance import TraceCapture, benchmark, stats, summarize


def test_nearest_rank_and_non_success_exclusion():
    assert stats(range(1, 101)) == {"n": 100, "median": 50.5, "p95": 95}
    assert stats([])["p95"] is None
    rows = [
        {"phase": "warm", "status": status, "metrics": {"llm_seconds": value}}
        for status, value in [
            ("ok", 1),
            ("ok", 3),
            ("cancelled", 999),
            ("error", 999),
            ("blocked", 999),
        ]
    ]
    report = summarize(rows)["warm"]
    assert report["normal_only"]["llm_seconds"]["median"] == 2
    assert report["normal_only"]["llm_seconds"]["n"] == 2
    assert report["outcomes"]["error"] == 1


def test_cleanup_does_not_make_failed_request_successful():
    trace = TraceCapture()
    trace.stages = {"cleanup_complete": ("settled", 1), "llm_complete": ("failed", 1)}
    assert trace.result(False) == "error"
    trace.stages = {"cleanup_complete": ("settled", 1), "output_checked": ("ok", 1)}
    assert trace.result(False) == "ok"
    assert trace.result(True) == "incomplete"


def test_benchmark_phase_counts_privacy_and_reproducible_suite(tmp_path):
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps(["private question one", "private question two"]))

    async def run():
        app = make_app(tmp_path)
        args = SimpleNamespace(
            benchmark=str(suite),
            benchmark_report=str(tmp_path / "report.json"),
            benchmark_rounds=3,
            benchmark_soak_seconds=0,
        )
        report = await benchmark(app, args)
        assert len(report["requests"]) == 6
        assert report["summary"]["warm"]["outcomes"] == {"ok": 4}
        assert report["summary"]["first_request"]["outcomes"] == {"ok": 1}
        assert "private question" not in json.dumps(report)
        assert (
            "input_to_first_callback_seconds"
            not in report["summary"]["warm"]["normal_only"]
        )
        await app.shutdown()

    asyncio.run(run())


def test_capture_does_not_keep_content():
    capture = TraceCapture()
    capture.emit(
        logging.LogRecord("virtual_ai.app", 20, "", 0, "private question", (), None)
    )
    assert not capture.metrics and not capture.stages


def test_soak_takes_post_interval_samples(tmp_path):
    suite = tmp_path / "suite.json"
    suite.write_text('["question"]')

    async def run():
        app = make_app(tmp_path)
        args = SimpleNamespace(
            benchmark=str(suite),
            benchmark_report=str(tmp_path / "report.json"),
            benchmark_rounds=2,
            benchmark_soak_seconds=0.02,
        )
        report = await benchmark(app, args)
        assert report["post_soak_completed"]
        assert report["summary"]["post_soak"]["outcomes"]["ok"] >= 1
        await app.shutdown()

    asyncio.run(run())
