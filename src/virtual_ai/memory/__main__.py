"""Explicit local fixture maintenance. Never invoked by viewers or the model."""

import argparse
import json
from pathlib import Path

from virtual_ai.memory.base import StoreError
from virtual_ai.memory.sqlite_store import SQLiteStore


def main():
    parser = argparse.ArgumentParser(
        description="Opt-in local memory; no automatic chat capture."
    )
    parser.add_argument("--db", default=".local/memory.sqlite3")
    parser.add_argument("--enabled", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("allow", "forget", "lookup"):
        sub.add_parser(name).add_argument("--user", required=True)
    sub.add_parser("store").add_argument("--file", required=True)
    delivery = sub.add_parser("delivery")
    delivery.add_argument("--user", required=True)
    delivery.add_argument("--turn-id", required=True)
    delivery.add_argument("--displayed", action="store_true")
    delivery.add_argument(
        "--playback",
        required=True,
        choices=["completed", "failed", "cancelled", "unknown"],
    )
    for name in ("backup", "restore"):
        sub.add_parser(name).add_argument("--file", required=True)
    sub.add_parser("purge")
    args = parser.parse_args()
    try:
        store = SQLiteStore(args.db, enabled=args.enabled)
        if not args.enabled:
            print("memory_status=disabled")
            return 0
        if args.command in ("allow", "forget", "lookup"):
            result = getattr(store, args.command)(args.user)
        elif args.command in ("backup", "restore"):
            result = getattr(store, args.command)(args.file)
        elif args.command == "purge":
            result = store.purge_expired()
        elif args.command == "delivery":
            result = store.delivery(
                args.user,
                args.turn_id,
                displayed=args.displayed,
                playback=args.playback,
            )
        else:
            path = Path(args.file)
            if path.stat().st_size > 32768:
                raise StoreError("invalid_input")
            data = json.loads(path.read_text(encoding="utf-8"))
            kind = data.pop("kind")
            if (
                kind not in ("turn", "fact", "summary")
                or data.get("source") != "local_operator"
            ):
                raise StoreError("policy_denied")
            result = getattr(store, "add_" + kind)(**data)
        # Lookup is an explicit private operator output, not a normal application log.
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except StoreError as exc:
        print("memory_status=failed reason=" + exc.reason)
        return 1
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        print("memory_status=failed reason=invalid_input_or_file")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
