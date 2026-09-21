"""Synthesize a fixed Korean test sentence into a WAV file."""

import argparse
import asyncio
import logging
from pathlib import Path
from time import monotonic

from virtual_ai.app import configure_console_output
from virtual_ai.config import load_config
from virtual_ai.tts.base import TTSError
from virtual_ai.tts.gpt_sovits import GPTSoVITSClient

TEST_SENTENCE = "안녕하세요. 음성 합성 테스트입니다."


async def run(args):
    settings, _ = load_config(Path(args.config).resolve())
    client = GPTSoVITSClient(settings.tts)
    started = monotonic()
    try:
        return await client.synthesize(TEST_SENTENCE, Path(args.output))
    finally:
        await client.aclose()
        logging.getLogger(__name__).info("tts_seconds=%.6f", monotonic() - started)


def main():
    configure_console_output()
    parser = argparse.ArgumentParser(
        description="GPT-SoVITS fixed Korean sentence to WAV"
    )
    parser.add_argument("--config", default="configs/app.example.yaml")
    parser.add_argument("--output", default="generated_audio/tts-test.wav")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        path = asyncio.run(run(args))
        print(f"WAV 저장 완료: {path}")
    except TTSError as exc:
        parser.exit(1, f"TTS 오류: {exc}\n")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"설정/실행 오류: {exc}\n")
    except KeyboardInterrupt:
        parser.exit(130, "음성 합성을 취소했습니다.\n")


if __name__ == "__main__":
    main()
