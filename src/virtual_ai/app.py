"""Single-worker orchestration with a separate trusted operator entry point."""

import argparse
import asyncio
import logging
from pathlib import Path
from time import monotonic
from uuid import uuid4

from virtual_ai.config import load_config
from virtual_ai.inputs.queue import InputQueue
from virtual_ai.llm.base import LLMClient, LLMError
from virtual_ai.llm.koboldcpp import KoboldCppClient
from virtual_ai.llm.mock import FakeLLM
from virtual_ai.prompting import build_messages
from virtual_ai.safety import prepare_response

logger = logging.getLogger(__name__)


class Application:
    def __init__(self, settings, character, llm: LLMClient, output=print):
        self.settings, self.character, self.llm = settings, character, llm
        self.output = output
        self.queue = InputQueue(settings.queue_size, settings.queue_ttl_seconds)
        self._ready = asyncio.Event()
        self._lock = asyncio.Lock()
        self._epoch = 0

    def submit(self, item):
        # All chat goes through this data-only entry point, even '/stop'.
        if (
            not item.text.strip()
            or len(item.text) > self.settings.max_input_chars
            or not item.message_id
        ):
            return False
        accepted = self.queue.put(item)
        if accepted:
            self._ready.set()
        return accepted

    async def stop(self):
        """Trusted local operator only; never dispatch this from model/chat text."""
        self._epoch += 1
        self.queue.clear()

    async def forget(self, viewer=None):
        await self.stop()

    async def process_next(self, *, raise_errors=False):
        async with self._lock:
            queued = self.queue.pop_timed()
            if queued is None:
                return None
            submitted_at, item = queued
            epoch = self._epoch
            response_id = str(uuid4())
            logger.info(
                "response_id=%s queue_seconds=%.6f",
                response_id,
                monotonic() - submitted_at,
            )
            messages = build_messages(self.character, [], item.text)
            try:
                llm_started = monotonic()
                raw = await self.llm.generate(messages)
            except LLMError as exc:
                logger.warning("response_id=%s llm_status=failed", response_id)
                if raise_errors:
                    raise
                if epoch == self._epoch:
                    self.output(str(exc))
                return None
            finally:
                logger.info(
                    "response_id=%s llm_seconds=%.6f",
                    response_id,
                    monotonic() - llm_started,
                )
            if epoch != self._epoch:
                return None
            response = prepare_response(response_id, raw, self.settings)
            self.output(response.final)
            return response

    async def run(self):
        while True:
            await self._ready.wait()
            self._ready.clear()
            while len(self.queue):
                await self.process_next()


async def run_cli(args):
    from virtual_ai.inputs.console import console
    from virtual_ai.schemas import ChatInput, Viewer

    settings, character = load_config(Path(args.config).resolve())
    from dataclasses import replace

    if args.backend:
        settings = replace(settings, backend=args.backend)
    llm = (
        FakeLLM() if settings.backend in ("mock", "fake") else KoboldCppClient(settings)
    )
    app = Application(settings, character, llm)
    try:
        if args.once is not None:
            if not app.submit(
                ChatInput(Viewer("console", "local"), args.once, str(uuid4()))
            ):
                raise ValueError("입력이 비었거나 길이 제한을 초과했습니다.")
            await app.process_next(raise_errors=True)
        else:
            await console(app)
    finally:
        try:
            await app.stop()
        finally:
            await llm.aclose()
            logger.info("application_status=closed")


def main():
    parser = argparse.ArgumentParser(description="Virtual AI text conversation")
    parser.add_argument("--config", default="configs/app.example.yaml")
    parser.add_argument("--backend", choices=("mock", "fake", "koboldcpp"))
    parser.add_argument(
        "--log-level", choices=("INFO", "WARNING", "ERROR"), default="INFO"
    )
    parser.add_argument("--once", help="한 번 입력하고 종료")
    args = parser.parse_args()
    logging.basicConfig(
        level=args.log_level, format="%(levelname)s %(name)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(run_cli(args))
    except LLMError as exc:
        parser.exit(1, f"LLM 오류: {exc}\n")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"설정/실행 오류: {exc}\n")
    except KeyboardInterrupt:
        pass
