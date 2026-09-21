"""Authenticate, inspect and preview operator-mapped VTS expressions independently."""

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path

from virtual_ai.config import load_config
from virtual_ai.integrations.vts.base import AvatarError
from virtual_ai.integrations.vts.client import VTSClient


async def run(args):
    settings, _ = load_config(Path(args.config).resolve())
    if not settings.vts.enabled:
        raise ValueError("vts.enabled is false; enable VTS in the local configuration.")
    client = VTSClient(settings.vts)
    try:
        await client.connect(authenticate=args.authenticate)
        if args.authenticate:
            print("VTS authentication saved. Use --list-hotkeys to inspect the model.")
        elif args.list_hotkeys:
            print(json.dumps(await client.list_hotkeys(), ensure_ascii=True, indent=2))
        else:
            await client.set_expression(args.expression)
            print(f"VTS expression: {args.expression}", flush=True)
            if args.expression != "neutral":
                await asyncio.sleep(args.hold_seconds)
            await client.reset()
            print("VTS session-managed expressions reset.")
    finally:
        await client.aclose()


def _duration(value):
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 60:
        raise argparse.ArgumentTypeError("hold seconds must be positive and at most 60")
    return number


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Independent local VTube Studio CLI")
    parser.add_argument("--config", default="configs/app.example.yaml")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--authenticate", action="store_true")
    action.add_argument("--list-hotkeys", action="store_true")
    action.add_argument("--expression", choices=("happy", "sad", "neutral"))
    parser.add_argument("--hold-seconds", type=_duration, default=3)
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except AvatarError as exc:
        parser.exit(1, f"VTS error: {exc}\n")
    except (ValueError, OSError):
        parser.exit(
            2, "Invalid or unreadable local configuration; check vts settings.\n"
        )
    except KeyboardInterrupt:
        parser.exit(130, "VTS preview interrupted; connection cleanup completed.\n")


if __name__ == "__main__":
    main()
