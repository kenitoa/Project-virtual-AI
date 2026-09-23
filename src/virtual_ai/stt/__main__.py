"""Explicit file conversion; no automatic model download or microphone use."""

import argparse
import asyncio
import json

from virtual_ai.stt.base import STTError
from virtual_ai.stt.client import ProcessSTT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("--python", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    args = parser.parse_args()
    try:
        client = ProcessSTT(args.python, args.model, device=args.device)
        text = asyncio.run(client.transcribe(args.audio))
        print(json.dumps({"text": text}, ensure_ascii=True))
    except STTError as exc:
        parser.exit(1, "stt_status=" + exc.reason + "\n")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
