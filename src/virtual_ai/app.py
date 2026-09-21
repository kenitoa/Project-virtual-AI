"""Single-worker orchestration with a separate trusted operator entry point."""

import argparse
import asyncio
import logging
import sys
from contextlib import AsyncExitStack
from dataclasses import replace
from pathlib import Path
from time import monotonic
from uuid import uuid4

from virtual_ai.audio.base import AudioError, AudioPlayer
from virtual_ai.audio.player import WAVPlayer
from virtual_ai.config import load_config
from virtual_ai.expressions import select_expression
from virtual_ai.inputs.queue import InputQueue
from virtual_ai.integrations.vts.base import AvatarClient, AvatarError
from virtual_ai.integrations.vts.client import VTSClient
from virtual_ai.lipsync import MouthSync
from virtual_ai.llm.base import LLMClient, LLMError
from virtual_ai.llm.koboldcpp import KoboldCppClient
from virtual_ai.llm.mock import FakeLLM
from virtual_ai.memory.recent import RecentHistory
from virtual_ai.prompting import build_messages, load_system_prompt
from virtual_ai.safety import prepare_response
from virtual_ai.tts.base import TTSClient, TTSError
from virtual_ai.tts.gpt_sovits import GPTSoVITSClient

logger = logging.getLogger(__name__)


class Application:
    def __init__(
        self,
        settings,
        character,
        llm: LLMClient,
        output=print,
        *,
        tts: TTSClient | None = None,
        player: AudioPlayer | None = None,
        avatar: AvatarClient | None = None,
        expression_policy=None,
        audio_directory=Path("generated_audio"),
    ):
        self.settings, self.character, self.llm = settings, character, llm
        self.output = output
        self.system_prompt = load_system_prompt(settings.system_prompt_path)
        self.history = RecentHistory(settings)
        self.queue = InputQueue(settings.queue_size, settings.queue_ttl_seconds)
        self._ready = asyncio.Event()
        self._lock = asyncio.Lock()
        self._epoch = 0
        self._generation: asyncio.Task[str] | None = None
        self._voice: asyncio.Task | None = None
        self.tts, self.player = tts, player
        self.avatar = avatar
        self.expression_policy = expression_policy
        self._avatar_available = settings.vts.enabled and avatar is not None
        self._avatar_pending = set()
        self._mouth_session = None
        self._audio_stop_done = asyncio.Event()
        self._audio_stop_done.set()
        self.audio_directory = Path(audio_directory)
        self._closed = False
        self._voice_enabled = settings.tts.enabled and settings.audio.enabled
        if self._voice_enabled and (tts is None or player is None):
            raise ValueError("enabled voice pipeline requires TTS and audio clients")

    def submit(self, item):
        # All chat goes through this data-only entry point, even '/stop'.
        if (
            self._closed
            or not item.text.strip()
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
        if self._mouth_session is not None:
            self._mouth_session.stop()
        audio_stop_done = self._audio_stop_done = asyncio.Event()
        if self._generation is not None and not self._generation.done():
            self._generation.cancel()
        if self._voice is not None and not self._voice.done():
            self._voice.cancel()
        try:
            if self._voice_enabled:
                await self.player.stop()
        except AudioError:
            logger.warning("audio_status=stop_failed")
        finally:
            audio_stop_done.set()

    async def shutdown(self):
        """Finish in-flight cleanup before the owner closes borrowed clients."""
        self._closed = True
        self._ready.set()
        await self.stop()
        async with self._lock:
            pass

    async def forget(self, viewer=None):
        await self.stop()
        self.history.delete(viewer)

    async def process_next(self, *, raise_errors=False):
        async with self._lock:
            if self._closed:
                return None
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
            try:
                llm_started = monotonic()
                messages = build_messages(
                    self.character,
                    self.history.messages(item.viewer),
                    item.text,
                    system_prompt=self.system_prompt,
                    settings=self.settings,
                )
                self._generation = asyncio.create_task(self.llm.generate(messages))
                raw = await self._generation
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError
            except asyncio.CancelledError:
                # A stopped response must not terminate the long-lived worker.
                # Cancellation of the worker itself must still propagate.
                if asyncio.current_task().cancelling() or epoch == self._epoch:
                    raise
                return None
            except LLMError as exc:
                logger.warning("response_id=%s llm_status=failed", response_id)
                if raise_errors:
                    raise
                if epoch == self._epoch:
                    self.output(str(exc))
                return None
            finally:
                self._generation = None
                logger.info(
                    "response_id=%s llm_seconds=%.6f",
                    response_id,
                    monotonic() - llm_started,
                )
            if epoch != self._epoch:
                return None
            response = prepare_response(response_id, raw, self.settings)
            response = replace(
                response,
                expression=select_expression(
                    response, self.settings.allowed_expressions, self.expression_policy
                ),
            )
            self.output(response.final)
            if not response.blocked:
                self.history.add(item.viewer, item.text, response.final)
            if self._voice_enabled and response.speech.strip():
                self._voice = asyncio.create_task(self._speak(response, epoch))
                try:
                    await self._voice
                    if asyncio.current_task().cancelling():
                        raise asyncio.CancelledError
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling() or epoch == self._epoch:
                        raise
                    # Text already displayed and recorded remains valid.
                finally:
                    self._voice = None
            return response

    async def _speak(self, response, epoch):
        # IDs come from uuid4 above, never model output or incoming message IDs.
        destination = self.audio_directory / f"{response.response_id}.wav"
        avatar_attempted = False
        mouth = None

        def cancelled():
            return (
                self._closed
                or epoch != self._epoch
                or asyncio.current_task().cancelling()
            )

        try:
            if cancelled():
                return
            started = monotonic()
            try:
                await self.tts.synthesize(response.speech, destination)
            finally:
                logger.info(
                    "response_id=%s tts_seconds=%.6f",
                    response.response_id,
                    monotonic() - started,
                )
            if cancelled():
                return
            if self._avatar_available:
                avatar_attempted = True
                await self._avatar_call(
                    self.avatar.set_expression,
                    response.expression,
                    valid=lambda: not cancelled(),
                )
            if cancelled():
                return
            started = monotonic()
            try:
                if self.settings.vts.lipsync_enabled and self._avatar_available:
                    mouth = self._mouth_session = MouthSync(
                        self.settings.vts,
                        self._send_mouth,
                        lambda: (
                            not self._closed
                            and epoch == self._epoch
                            and self._avatar_available
                        ),
                    )
                    await self.player.play(destination, levels=mouth.levels)
                else:
                    await self.player.play(destination)
            finally:
                logger.info(
                    "response_id=%s playback_seconds=%.6f",
                    response.response_id,
                    monotonic() - started,
                )
        except (TTSError, AudioError):
            logger.warning("response_id=%s voice_status=failed", response.response_id)
            if not cancelled():
                self.output("음성 출력을 건너뜁니다. 텍스트 답변은 유지됩니다.")
        finally:
            if avatar_attempted:
                # Keep the process lock until cleanup settles; repeated /stop must
                # not detach a reset that could later affect the next response.
                cleanup = asyncio.create_task(self._reset_avatar(mouth))
                interrupted = False
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        interrupted = True
                cleanup.result()
            else:
                interrupted = False
            if self._mouth_session is mouth:
                self._mouth_session = None
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "response_id=%s audio_cleanup=failed", response.response_id
                )
            if interrupted:
                raise asyncio.CancelledError

    async def _avatar_call(self, method, *args, valid=None):
        """A stalled adapter cannot block voice; quarantine uncertain sessions."""
        if not self._avatar_available:
            return

        async def invoke():
            if self._avatar_available and (valid is None or valid()):
                await method(*args)

        task = asyncio.create_task(invoke())
        self._avatar_pending.add(task)

        def finished(task):
            self._avatar_pending.discard(task)
            if not task.cancelled():
                task.exception()  # Consume late failures without logging private details.

        task.add_done_callback(finished)
        try:
            done, _ = await asyncio.wait(
                {task}, timeout=self.settings.vts.request_timeout_seconds
            )
            if not done:
                raise TimeoutError
            task.result()
        except (AvatarError, TimeoutError, asyncio.CancelledError) as exc:
            self._avatar_available = False
            task.cancel()
            logger.warning("avatar_status=unknown recovery=manual")
            if (
                isinstance(exc, asyncio.CancelledError)
                and asyncio.current_task().cancelling()
            ):
                raise

    async def _send_mouth(self, value, *, valid):
        async def send():
            await self.avatar.set_mouth_open(value, valid=valid)

        await self._avatar_call(send, valid=valid)

    async def _reset_avatar(self, mouth=None):
        if mouth is not None:
            mouth.stop()
            await mouth.task
        await self._audio_stop_done.wait()
        if mouth is not None and self._avatar_available:
            await self._avatar_call(self.avatar.set_mouth_open, 0.0)
        if self._avatar_available:
            await self._avatar_call(self.avatar.reset)

    async def run(self):
        while not self._closed:
            await self._ready.wait()
            self._ready.clear()
            while len(self.queue) and not self._closed:
                await self.process_next()


async def run_cli(args):
    from virtual_ai.inputs.console import console
    from virtual_ai.schemas import ChatInput, Viewer

    settings, character = load_config(Path(args.config).resolve())
    if args.backend:
        settings = replace(settings, backend=args.backend)
    llm = (
        FakeLLM() if settings.backend in ("mock", "fake") else KoboldCppClient(settings)
    )
    async with AsyncExitStack() as resources:
        resources.callback(logger.info, "application_status=closed")
        resources.push_async_callback(llm.aclose)
        tts = player = None
        if settings.tts.enabled and settings.audio.enabled:
            tts = GPTSoVITSClient(settings.tts)
            resources.push_async_callback(tts.aclose)
            player = WAVPlayer(settings.audio)
            resources.push_async_callback(player.aclose)
        avatar = None
        if settings.vts.enabled and settings.tts.enabled and settings.audio.enabled:
            candidate = VTSClient(settings.vts)

            async def close_avatar():
                try:
                    await candidate.aclose()
                except AvatarError:
                    logger.warning("avatar_status=unknown recovery=manual")

            resources.push_async_callback(close_avatar)
            try:
                async with asyncio.timeout(3 * settings.vts.request_timeout_seconds):
                    # Saved token only; never prompt for approval.
                    await candidate.connect()
                avatar = candidate
            except (AvatarError, TimeoutError):
                logger.warning("avatar_status=unknown recovery=manual")
        app = Application(
            settings, character, llm, tts=tts, player=player, avatar=avatar
        )
        resources.push_async_callback(app.shutdown)
        if args.once is not None:
            if not app.submit(
                ChatInput(Viewer("console", "local"), args.once, str(uuid4()))
            ):
                raise ValueError("입력이 비었거나 길이 제한을 초과했습니다.")
            await app.process_next(raise_errors=True)
        else:
            await console(app)


def configure_console_output() -> None:
    """CLI의 표준 출력과 오류 출력을 UTF-8로 통일한다."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


def main():
    configure_console_output()
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
    except (AudioError, TTSError):
        parser.exit(1, "음성 클라이언트 정리 중 오류가 발생했습니다.\n")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"설정/실행 오류: {exc}\n")
    except KeyboardInterrupt:
        pass
