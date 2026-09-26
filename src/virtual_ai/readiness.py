"""Assess recorded session evidence without promoting missing evidence to pass."""

import argparse
import json
from pathlib import Path


def assess(report, duration_target=1800):
    observations = report.get("observations", {})
    metrics = observations.get("all_observed_outcomes", {})
    first = metrics.get("input_to_first_callback_seconds", {})
    outcomes = observations.get("outcomes_since_start", {})
    count = sum(outcomes.values())
    unsuccessful = sum(
        value
        for key, value in outcomes.items()
        if key not in ("audio:completed", "text_only")
    )
    # p95 on a tiny demonstration is reported, but isn't an acceptance sample.
    enough = first.get("n", 0) >= 100
    latency = None if not enough else first.get("p95", float("inf")) <= 7
    return {
        "source_sha256": report.get("identity", {}).get("source_sha256"),
        "duration_target_seconds": duration_target,
        "duration_met": observations.get("uptime_seconds", 0) >= duration_target,
        "first_audio_target_p95_seconds": 7,
        "first_audio_measured": first,
        "first_audio_target_met": latency,
        "minimum_latency_samples": 100,
        "outcomes": outcomes,
        "non_completion_rate": unsuccessful / count if count else None,
        "non_completion_note": "Includes intentional skips/stops; inspect reasons, not an error-rate estimate.",
        "cleanup_confirmed": report.get("cleanup_failed") is False,
        "faults": report.get("component_faults", {}),
        "unverified": [
            "actual_viewer_roundtrip",
            "human_dialogue_quality",
            "human_listening_and_sync",
            "broadcast_asset_permissions",
            "microphone_in_target_room",
            "independent_installation",
            "final_commit_ci",
        ],
        "release_approved": False,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate session evidence; does not authorize broadcasting"
    )
    parser.add_argument("--report", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--duration-target", type=int, choices=(1800, 7200, 14400), default=1800
    )
    args = parser.parse_args()
    source, destination = Path(args.report), Path(args.output)
    if source.stat().st_size > 2 * 1024 * 1024:
        parser.error("session report too large")
    data = json.loads(source.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or data.get("schema") != 1
        or not isinstance(data.get("observations"), dict)
    ):
        parser.error("expected a session report")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(
            assess(data, args.duration_target), stream, ensure_ascii=False, indent=2
        )
    print("Evidence assessment written; release approval remains manual.")


if __name__ == "__main__":
    main()
