"""Opt-in fixed-workload measurement. Reports contain no prompts or responses."""

import asyncio
import hashlib
import json
import logging
import math
import os
import platform
import re
import statistics
import time
from collections import Counter
from pathlib import Path
from uuid import uuid4

from virtual_ai.schemas import ChatInput, Viewer


def stats(values):
    values = sorted(
        v for v in values if isinstance(v, (float, int)) and math.isfinite(v) and v >= 0
    )
    if not values:
        return {"n": 0, "median": None, "p95": None}
    return {
        "n": len(values),
        "median": statistics.median(values),
        "p95": values[math.ceil(0.95 * len(values)) - 1],
    }


def rss_bytes():
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                    (name, ctypes.c_size_t)
                    for name in (
                        "peak_rss",
                        "rss",
                        "peak_paged",
                        "paged",
                        "peak_nonpaged",
                        "nonpaged",
                        "pagefile",
                        "peak_pagefile",
                    )
                ]

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            api = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
            api.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            api.restype = wintypes.BOOL
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if api(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return counters.rss
        elif Path("/proc/self/status").exists():
            match = re.search(r"VmRSS:\s+(\d+)", Path("/proc/self/status").read_text())
            return int(match[1]) * 1024 if match else None
    except (OSError, ValueError, AttributeError):
        pass
    return None


class TraceCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.stages = {}
        self.metrics = {}
        self.response_id = None

    def emit(self, record):
        if record.name not in ("virtual_ai.app", "virtual_ai.telemetry"):
            return
        message = record.getMessage()
        match = re.search(
            r"response_id=([a-f0-9-]+) stage=(\w+) status=(\w+) elapsed_seconds=([0-9.]+)",
            message,
        )
        if match:
            self.response_id = match[1]
            self.stages[match[2]] = (match[3], float(match[4]))
        match = re.search(
            r"response_id=[a-f0-9-]+ (queue_seconds|retrieval_seconds|validation_seconds|llm_seconds|tts_seconds|playback_seconds|audio_seconds|mouth_send_after_callback_seconds)=([0-9.]+)",
            message,
        )
        if match:
            self.metrics[match[1]] = float(match[2])

    def result(self, voice):
        stages = self.stages
        if any(v[0] in ("cancelled", "stopped") for v in stages.values()):
            return "cancelled"
        if any(v[0] == "blocked" for v in stages.values()):
            return "blocked"
        if "voice_failed" in stages or any(
            v[0] in ("failed", "recovery_required") for v in stages.values()
        ):
            return "error"
        if (
            stages.get("cleanup_complete", (None,))[0] != "settled"
            or stages.get("output_checked", (None,))[0] != "ok"
        ):
            return "incomplete"
        if voice and stages.get("playback_finished", (None,))[0] != "completed":
            return "incomplete"
        return "ok"


def summarize(rows):
    result = {}
    for phase in sorted({r["phase"] for r in rows}):
        subset = [r for r in rows if r["phase"] == phase]
        normal = [r for r in subset if r["status"] == "ok"]
        names = sorted({key for row in normal for key in row["metrics"]})
        result[phase] = {
            "outcomes": dict(Counter(r["status"] for r in subset)),
            "all_observed": {
                name: stats([r["metrics"].get(name) for r in subset])
                for name in sorted({key for row in subset for key in row["metrics"]})
            },
            "non_success_rate": (len(subset) - len(normal)) / len(subset),
            "normal_only": {
                name: stats([r["metrics"].get(name) for r in normal]) for name in names
            },
        }
    return result


async def benchmark(app, args):
    source = Path(args.benchmark)
    if source.stat().st_size > 32768:
        raise ValueError("benchmark suite too large")
    questions = json.loads(source.read_text(encoding="utf-8"))
    if (
        not isinstance(questions, list)
        or not 1 <= len(questions) <= 20
        or any(
            not isinstance(q, str)
            or not q.strip()
            or len(q) > app.settings.max_input_chars
            for q in questions
        )
    ):
        raise ValueError("benchmark requires 1..20 bounded questions")
    if (
        not 2 <= args.benchmark_rounds <= 100
        or not 0 <= args.benchmark_soak_seconds <= 14400
    ):
        raise ValueError("invalid benchmark workload")
    if (
        app.runtime.input_locked
        or app.settings.youtube.enabled
        or app.settings.chzzk.enabled
        or app.microphone
        or app.memory
        or (app.rag and app.rag.settings.memory_enabled)
    ):
        raise ValueError(
            "benchmark requires isolated, unlocked input; disable chat, microphone and memory"
        )
    report_path = Path(args.benchmark_report)
    if report_path.exists():
        raise ValueError("benchmark report already exists")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("virtual_ai")
    previous_level, previous_output = logger.level, app.output
    previous_propagate = logger.propagate
    logger.propagate = False
    logger.setLevel(logging.INFO)
    app.output = lambda _: None
    rows = []
    started = time.monotonic()
    run_cpu_started = time.process_time()
    try:
        round_index = 0
        while round_index < 100000 and (
            round_index < args.benchmark_rounds
            or (
                args.benchmark_soak_seconds > 0
                and not any(r["phase"] == "post_soak" for r in rows)
            )
        ):
            app.history.delete()
            after_soak = bool(
                args.benchmark_soak_seconds
                and time.monotonic() - started >= args.benchmark_soak_seconds
            )
            for index, question in enumerate(questions):
                phase = (
                    "first_request"
                    if not rows
                    else "warmup"
                    if round_index == 0
                    else "post_soak"
                    if after_soak
                    else "warm"
                )
                capture = TraceCapture()
                logger.addHandler(capture)
                before, cpu = time.perf_counter(), time.process_time()
                try:
                    accepted = app.submit(
                        ChatInput(
                            Viewer("benchmark", str(round_index)),
                            question,
                            str(uuid4()),
                        )
                    )
                    if accepted:
                        await app.process_next()
                finally:
                    logger.removeHandler(capture)
                elapsed = time.perf_counter() - before
                metrics = dict(capture.metrics)
                if elapsed >= 0.1:
                    metrics["controller_cpu_percent_one_core"] = (
                        100 * (time.process_time() - cpu) / elapsed
                    )
                rss = rss_bytes()
                if rss is not None:
                    metrics["controller_rss_after_bytes"] = rss
                for stage, name in (
                    ("first_playback_callback", "input_to_first_callback_seconds"),
                    ("cleanup_complete", "input_to_cleanup_seconds"),
                ):
                    if stage in capture.stages and capture.stages[stage][0] in (
                        "ok",
                        "settled",
                    ):
                        metrics[name] = capture.stages[stage][1]
                if (
                    "llm_started" in capture.stages
                    and capture.stages.get("llm_complete", (None,))[0] == "ok"
                ):
                    metrics["llm_generation_seconds"] = max(
                        0,
                        capture.stages["llm_complete"][1]
                        - capture.stages["llm_started"][1],
                    )
                rows.append(
                    {
                        "phase": phase,
                        "round": round_index,
                        "question_index": index,
                        "response_id": capture.response_id,
                        "status": capture.result(
                            app._voice_enabled and not app.runtime.muted
                        )
                        if accepted
                        else "rejected",
                        "metrics": metrics,
                    }
                )
            round_index += 1
            # Always take one complete question set after the soak interval.
            if (
                args.benchmark_soak_seconds
                and time.monotonic() - started >= args.benchmark_soak_seconds
                and not any(r["phase"] == "post_soak" for r in rows)
            ):
                continue
            if round_index >= args.benchmark_rounds and (
                not args.benchmark_soak_seconds
                or any(r["phase"] == "post_soak" for r in rows)
            ):
                break
            await asyncio.sleep(0.1 if args.benchmark_soak_seconds else 0)
    finally:
        logger.propagate = previous_propagate
        logger.setLevel(previous_level)
        app.output = previous_output
    report = {
        "schema": 1,
        "controller_cpu_seconds_total": time.process_time() - run_cpu_started,
        "post_soak_completed": any(r["phase"] == "post_soak" for r in rows),
        "cpu_note": "per-request percent omitted below 100ms; total includes measurement overhead",
        "workload": "serial fixed questions; history reset each round; no chat/mic/memory; not queue overload",
        "backend": app.settings.backend,
        "voice_enabled": app._voice_enabled,
        "python": platform.python_version(),
        "os": platform.platform(),
        "logical_cpus": os.cpu_count(),
        "suite_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "settings_sha256": hashlib.sha256(repr(app.settings).encode()).hexdigest(),
        "character_prompt_sha256": hashlib.sha256(
            (
                json.dumps(app.character, sort_keys=True, ensure_ascii=False)
                + app.system_prompt
            ).encode()
        ).hexdigest(),
        "elapsed_seconds": time.monotonic() - started,
        "soak_requested_seconds": args.benchmark_soak_seconds,
        "percentile_method": "nearest-rank",
        "unmeasured": [
            "external_engine_model_loading",
            "external_process_cpu_ram",
            "gpu_vram",
            "visual_lipsync",
            "obs_render_encode",
        ],
        "summary": summarize(rows),
        "requests": rows,
    }
    with report_path.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    return report
