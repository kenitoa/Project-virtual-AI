"""Run offline acceptance gates twice or schedule bounded fixture soak tests."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fingerprint():
    paths = subprocess.check_output(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT
    ).split(b"\0")
    digest = hashlib.sha256()
    for raw in sorted(set(p for p in paths if p)):
        path = ROOT / os.fsdecode(raw)
        if path.is_file():
            digest.update(raw)
            digest.update(path.read_bytes())
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument(
        "--soak-seconds", type=int, choices=(0, 10, 1800, 7200), default=0
    )
    parser.add_argument("--output", default=".local/acceptance")
    args = parser.parse_args()
    if not 1 <= args.repeat <= 10:
        parser.error("repeat must be 1..10")
    destination = Path(args.output).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    manifest = json.loads(
        (ROOT / "tests/fixtures/chat_scenarios/acceptance.json").read_text()
    )
    targets = sorted(
        {path for group in manifest["scenarios"].values() for path in group}
    )
    env = os.environ.copy()
    for name in list(env):
        if name.startswith("YOUTUBE_") or name in (
            "HF_TOKEN",
            "HUGGING_FACE_HUB_TOKEN",
        ):
            env.pop(name)
    initial = fingerprint()
    report = {
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "worktree_sha256": initial,
        "dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)
        ),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "layers": {
            "unit": "separate full pytest gate",
            "fake_integration": "running",
            "real_devices": "pending",
            "operations": "pending",
        },
        "runs": [],
    }
    try:
        for index in range(args.repeat):
            env["VIRTUAL_AI_SOAK_SECONDS"] = str(args.soak_seconds)
            env["VIRTUAL_AI_SOAK_REPORT"] = str(destination / f"soak-{index}.json")
            started = time.monotonic()
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        "-q",
                        *targets,
                        "--junitxml",
                        str(destination / f"tests-{index}.xml"),
                    ],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=max(180, args.soak_seconds + 180),
                )
                code = result.returncode
                # Test output is synthetic; keep failure details local, never upload automatically.
                (destination / f"tests-{index}.log").write_text(
                    result.stdout + result.stderr, encoding="utf-8"
                )
            except subprocess.TimeoutExpired:
                code = 124
            unchanged = fingerprint() == initial
            report["runs"].append(
                {
                    "index": index,
                    "exit_code": code,
                    "elapsed_seconds": time.monotonic() - started,
                    "same_worktree": unchanged,
                }
            )
            if code or not unchanged:
                break
        passed = len(report["runs"]) == args.repeat and all(
            r["exit_code"] == 0 and r["same_worktree"] for r in report["runs"]
        )
        report["layers"]["fake_integration"] = "passed" if passed else "failed"
        return 0 if passed else 1
    finally:
        (destination / "report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    raise SystemExit(main())
