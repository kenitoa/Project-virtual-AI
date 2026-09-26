"""Local rehearsal entry point without changing PowerShell execution policy."""

import argparse
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from virtual_ai.app import configure_console_output, run_cli
from virtual_ai.audio.base import AudioError
from virtual_ai.llm.base import LLMError
from virtual_ai.memory.base import StoreError
from virtual_ai.tts.base import TTSError


def options(args):
    local = Path(".local/operator-rehearsal.yaml")
    use_local = args.config is None and local.is_file()
    config = args.config or (str(local) if use_local else "configs/app.yaml")
    if not Path(config).is_file():
        raise ValueError(
            "설정 파일이 없습니다. configs/app.example.yaml을 복사하고 외부 엔진을 구성하세요."
        )
    obs = args.obs_config
    if obs is None and use_local and Path(".local/obs-operator.json").is_file():
        obs = ".local/obs-operator.json"
    report = Path(".local/session-reports") / (
        datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex + ".json"
    )
    return SimpleNamespace(
        config=str(Path(config).resolve()),
        backend=args.backend,
        once=args.once,
        local_only=True,
        responsive=True,
        check_startup=True,
        warmup_tts=not args.skip_warmup,
        obs_config=obs,
        operator_panel=args.once is None,
        operator_port=args.operator_port,
        session_report=str(report),
    )


def main():
    configure_console_output()
    parser = argparse.ArgumentParser(
        description="Local rehearsal; external chat disabled, no broadcast start"
    )
    parser.add_argument("--config")
    parser.add_argument(
        "--backend", choices=("mock", "fake", "koboldcpp"), default="koboldcpp"
    )
    parser.add_argument("--obs-config")
    parser.add_argument("--skip-warmup", action="store_true")
    parser.add_argument("--operator-port", type=int, default=0)
    parser.add_argument("--once")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        selected = options(args)
        print(
            "로컬 리허설입니다. 외부 채팅은 꺼져 있습니다. LLM·TTS·OBS·VTS는 별도로 준비하세요."
        )
        asyncio.run(run_cli(selected))
        print("세션 기록: " + selected.session_report)
    except (ValueError, OSError, LLMError, StoreError, AudioError, TTSError):
        print(
            "시작 또는 정리에 실패했습니다. 설정·외부 엔진과 diagnostics 결과를 확인하세요."
        )
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
