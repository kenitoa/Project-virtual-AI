"""Local operator maintenance. No entry point from model or viewer commands."""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

from virtual_ai.config import load_config
from virtual_ai.memory.base import StoreError
from virtual_ai.rag.pipeline import RAGPipeline
from virtual_ai.rag.settings import RAGSettings
from virtual_ai.rag.store import RAGStore
from virtual_ai.schemas import Viewer


def read_json(path):
    source = Path(path)
    if source.stat().st_size > 250000:
        raise StoreError("input_file_limit")
    return json.loads(source.read_text(encoding="utf-8"))


def viewer_options(parser):
    parser.add_argument(
        "--platform", required=True, choices=("console", "youtube", "chzzk")
    )
    parser.add_argument("--user", required=True)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Local RAG operator tools; private output"
    )
    parser.add_argument("--db", default=".local/rag.sqlite3")
    parser.add_argument("--scope", default="local")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest")
    ingest.add_argument(
        "--file",
        required=True,
        help="JSON: source,title,version,content[,days,fact_key]",
    )
    ingest.add_argument("--approve", action="store_true")
    consent = commands.add_parser("consent")
    viewer_options(consent)
    for permission in ("storage", "retrieval", "public"):
        consent.add_argument("--" + permission, action="store_true")
    for name in ("forget", "inspect", "propose", "preview"):
        p = commands.add_parser(name)
        viewer_options(p)
        if name == "propose":
            p.add_argument(
                "--file", required=True, help="JSON: text,kind; exact source quotation"
            )
        if name == "preview":
            p.add_argument("--question", required=True)
            p.add_argument("--memory", action="store_true")
    commands.add_parser("documents")
    delete = commands.add_parser("delete")
    delete.add_argument("--kind", required=True, choices=("document", "memory"))
    delete.add_argument("--id", required=True)
    review = commands.add_parser("review")
    review.add_argument("--id", required=True)
    decision = review.add_mutually_exclusive_group(required=True)
    decision.add_argument("--approve", action="store_true")
    decision.add_argument("--reject", action="store_true")
    review.add_argument("--replaces")
    index = commands.add_parser("reindex")
    index.add_argument(
        "--config", required=True, help="Explicit loopback embedding configuration"
    )
    commands.add_parser("purge")
    args = parser.parse_args()
    try:
        store = RAGStore(args.db)
        viewer = Viewer(args.platform, args.user) if hasattr(args, "platform") else None
        result = {"status": "ok"}
        if args.command == "ingest":
            data = read_json(args.file)
            if not isinstance(data, dict) or set(data) - {
                "source",
                "title",
                "version",
                "content",
                "days",
                "fact_key",
            }:
                raise StoreError("invalid_document")
            result["id"] = store.ingest(args.scope, confirmed=args.approve, **data)
        elif args.command == "consent":
            store.consent(
                args.scope,
                viewer,
                storage=args.storage,
                retrieval=args.retrieval,
                public=args.public,
            )
        elif args.command == "forget":
            store.forget(args.scope, viewer)
        elif args.command == "inspect":
            result = store.inspect(args.scope, viewer)
        elif args.command == "documents":
            result = store.inspect(args.scope)
        elif args.command == "propose":
            data = read_json(args.file)
            if set(data) != {"text", "kind"}:
                raise StoreError("invalid_candidate")
            result["id"] = store.candidate(
                args.scope, viewer, str(uuid4()), "operator", data["text"], data["kind"]
            )
        elif args.command == "review":
            store.review(args.id, approve=args.approve, supersedes=args.replaces)
        elif args.command == "delete":
            store.delete(args.kind, args.id)
        elif args.command == "preview":
            pipeline = RAGPipeline(
                store,
                RAGSettings(enabled=True, scope=args.scope, memory_enabled=args.memory),
            )
            evidence = asyncio.run(pipeline.prepare(viewer, args.question))
            result = {**evidence.diagnostic(), "sources": evidence.rows}
        elif args.command == "reindex":
            settings, _ = load_config(Path(args.config))
            if (
                settings.rag.scope != args.scope
                or Path(settings.rag.db_path).resolve() != store.path
            ):
                raise StoreError("index_config_scope_mismatch")
            result["vectors"] = asyncio.run(
                RAGPipeline(store, settings.rag).reindex(Viewer("console", "operator"))
            )
        elif args.command == "purge":
            store.revision()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except StoreError as exc:
        print("rag_status=failed reason=" + exc.reason)
        return 1
    except (OSError, ValueError, TypeError, KeyError):
        print("rag_status=failed reason=invalid_input_or_file")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
