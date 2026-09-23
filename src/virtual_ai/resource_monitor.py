"""Opt-in PID-scoped resource sampling; never records commands or network addresses."""

import argparse
import asyncio
import json
import math
import os
import shutil
import subprocess
import time
from pathlib import Path

import psutil

from virtual_ai.integrations import obs_stats
from virtual_ai.performance import stats


class ProcessProbe:
    def __init__(self, pid):
        self.process = psutil.Process(pid)
        self.created = self.process.create_time()
        self.process.cpu_percent()

    def sample(self):
        try:
            if (
                not self.process.is_running()
                or self.process.create_time() != self.created
            ):
                return {"status": "exited_or_reused"}
            data = {
                "status": "ok",
                "cpu_percent_one_core": self.process.cpu_percent(),
                "rss_bytes": self.process.memory_info().rss,
                "threads": self.process.num_threads(),
            }
            for name, function in (
                (
                    "handles_or_fds",
                    getattr(self.process, "num_handles", None)
                    or getattr(self.process, "num_fds", None),
                ),
                (
                    "tcp_connections",
                    lambda: len(self.process.net_connections(kind="tcp")),
                ),
            ):
                try:
                    data[name] = function() if function else None
                except (psutil.Error, OSError):
                    data[name] = None
            return data
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return {"status": "exited_or_reused"}
        except psutil.AccessDenied:
            return {"status": "access_denied"}


def gpu_sample():
    executable = shutil.which("nvidia-smi")
    if not executable:
        return {"status": "unavailable"}
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=index,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
        devices = []
        for line in result.stdout.splitlines()[:16]:
            values = [float(v.strip()) for v in line.split(",")]
            if len(values) != 4 or any(not math.isfinite(v) or v < 0 for v in values):
                raise ValueError()
            devices.append(
                dict(
                    zip(
                        ("index", "gpu_percent", "vram_used_mib", "vram_total_mib"),
                        values,
                        strict=True,
                    )
                )
            )
        return {"status": "ok", "devices": devices}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"status": "unavailable"}


def run(pids, seconds, interval, output, *, gpu=False, obs=False, obs_port=4455):
    if (
        not 1 <= seconds <= 7200
        or not 1 <= interval <= 60
        or not 1 <= len(pids) <= 16
        or any(p <= 0 for p in pids)
    ):
        raise ValueError("invalid sampling limits")
    probes = {str(pid): ProcessProbe(pid) for pid in set(pids)}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as destination:
        start = time.monotonic()
        samples = []
        interrupted = False
        try:
            while time.monotonic() - start < seconds:
                time.sleep(min(interval, max(0, seconds - (time.monotonic() - start))))
                row = {
                    "elapsed_seconds": time.monotonic() - start,
                    "processes": {pid: probe.sample() for pid, probe in probes.items()},
                }
                if gpu:
                    row["gpu"] = gpu_sample()
                if obs:
                    row["obs"] = asyncio.run(obs_stats.sample(obs_port))
                samples.append(row)
        except KeyboardInterrupt:
            interrupted = True
        summary = {}
        for pid in probes:
            records = [
                s["processes"][pid]
                for s in samples
                if s["processes"][pid]["status"] == "ok"
            ]
            summary[pid] = {
                key: stats([r.get(key) for r in records])
                for key in (
                    "cpu_percent_one_core",
                    "rss_bytes",
                    "threads",
                    "handles_or_fds",
                    "tcp_connections",
                )
            }
        gpu_summary = {}
        for index in {
            d["index"] for s in samples for d in s.get("gpu", {}).get("devices", [])
        }:
            records = [
                d
                for s in samples
                for d in s.get("gpu", {}).get("devices", [])
                if d["index"] == index
            ]
            gpu_summary[str(int(index))] = {
                key: stats([r[key] for r in records])
                for key in ("gpu_percent", "vram_used_mib")
            }
        report = {
            "schema": 1,
            "interrupted": interrupted,
            "requested_seconds": seconds,
            "gpu_scope": "whole device, not per process",
            "unmeasured": ["encoder_latency", "visual_lipsync"],
            "obs_summary": {
                key: stats(
                    [
                        s["obs"][key]
                        for s in samples
                        if s.get("obs", {}).get("status") == "ok"
                    ]
                )
                for key in obs_stats.FIELDS
            },
            "summary": summary,
            "gpu_summary": gpu_summary,
            "samples": samples,
        }
        json.dump(report, destination, indent=2)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, action="append")
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument("--obs", action="store_true")
    parser.add_argument("--obs-port", type=int, default=4455)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        run(
            args.pid or [os.getpid()],
            args.seconds,
            args.interval,
            args.output,
            gpu=args.gpu,
            obs=args.obs,
            obs_port=args.obs_port,
        )
    except (ValueError, OSError, psutil.Error):
        parser.exit(
            1, "resource_monitor=failed (check PID, permissions, limits and output)\n"
        )


if __name__ == "__main__":
    main()
