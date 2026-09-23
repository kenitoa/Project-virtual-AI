"""Standalone worker executed with the separately locked engine Python."""

import argparse
import json
import sys
from pathlib import Path


def transcribe(model, path):
    segments, _ = model.transcribe(
        str(path),
        language="ko",
        vad_filter=True,
        beam_size=5,
        condition_on_previous_text=False,
    )
    texts = []
    total = 0
    for index, segment in enumerate(segments):
        if index >= 128:
            raise ValueError("segment_limit")
        # VAD alone does not establish that a noise segment is speech.
        if segment.no_speech_prob > 0.6 or segment.avg_logprob < -1.0:
            continue
        total += len(segment.text)
        if total > 1000:
            raise ValueError("text_limit")
        texts.append(segment.text.strip())
    return " ".join(texts).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("--model", required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    args = parser.parse_args()
    try:
        if not Path(args.model).is_dir():
            raise ValueError("local_model_required")
        from faster_whisper import WhisperModel

        model = WhisperModel(
            args.model,
            device=args.device,
            compute_type="int8" if args.device == "cpu" else "float16",
            local_files_only=True,
            cpu_threads=2,
            num_workers=1,
        )
        text = transcribe(model, args.audio)
        sys.stdout.buffer.write(
            json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
        )
    except Exception:
        # No engine exception, source audio or transcript goes to diagnostic logs.
        sys.stdout.buffer.write(b'{"error":"engine_failed"}')
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
