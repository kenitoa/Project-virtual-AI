"""Single-worker orchestration with a separate trusted operator entry point."""

import argparse
import asyncio
import logging
import sys
from contextlib import AsyncExitStack, suppress
from dataclasses import replace
from pathlib import Path
from time import monotonic, time
from uuid import uuid4

from virtual_ai.audio.base import AudioError, AudioPlayer
from virtual_ai.audio.player import WAVPlayer
from virtual_ai.config import load_config
from virtual_ai.dialogue import Dialogue
from virtual_ai.expressions import (
    conservative_expression,
    dialogue_expression,
    select_expression,
)
from virtual_ai.health import DEGRADED, OK, RECOVERY, check_health, report_data
from virtual_ai.inputs.queue import InputQueue
from virtual_ai.inputs.selection import SelectionPolicy
from virtual_ai.integrations.chzzk import ChzzkChat, receive_chzzk
from virtual_ai.integrations.vts.base import AvatarClient, AvatarError
from virtual_ai.integrations.vts.client import VTSClient
from virtual_ai.integrations.youtube import YouTubeChat, receive_chat
from virtual_ai.integrations.youtube_stream import YouTubeStream
from virtual_ai.lipsync import MouthSync
from virtual_ai.llm.base import LLMClient, LLMError
from virtual_ai.llm.koboldcpp import KoboldCppClient
from virtual_ai.llm.mock import FakeLLM
from virtual_ai.memory.base import StoreError
from virtual_ai.memory.recent import RecentHistory
from virtual_ai.memory.retrieval import MemoryContext
from virtual_ai.memory.sqlite_store import SQLiteStore
from virtual_ai.memory.summarizer import summarize
from virtual_ai.prompting import build_messages, load_system_prompt
from virtual_ai.rag.pipeline import FALLBACKS as RAG_FALLBACKS
from virtual_ai.rag.pipeline import RAGPipeline
from virtual_ai.rag.retrieval import Evidence, Plan, terms
from virtual_ai.rag.retrieval import plan as retrieval_plan
from virtual_ai.rag.store import RAGStore
from virtual_ai.runtime_state import RuntimeState
from virtual_ai.safety import prepare_response
from virtual_ai.subtitles import SubtitleError, SubtitleWriter
from virtual_ai.telemetry import ResponseTrace
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
        subtitles: SubtitleWriter | None = None,
        audio_directory=Path("generated_audio"),
        live=False,
        health_checks=(),
        memory=None,
        rag=None,
    ):
        self.settings, self.character, self.llm = settings, character, llm
        self.microphone = None
        self.memory = memory
        self.rag = rag
        self.broadcast_state = {}
        self.runtime = RuntimeState(
            paused=live or settings.youtube.enabled or settings.chzzk.enabled
        )
        self.health_checks = tuple(health_checks)
        self._component_faults = {}
        self.disabled_features = {
            c.component for c in health_checks if c.status == RECOVERY
        }
        self._receipts = {}
        self._input_drops = {"input_locked": 0, "invalid_input": 0}
        self.output = output
        self.system_prompt = load_system_prompt(settings.system_prompt_path)
        self.history = RecentHistory(settings)
        self.dialogue = (
            Dialogue(settings, character) if settings.dialogue.enabled else None
        )
        self.queue = InputQueue(
            settings.queue_size,
            settings.queue_ttl_seconds,
            on_drop=self._discard,
            policy=SelectionPolicy(
                settings.queue_per_user, settings.queue_max_consecutive
            ),
        )
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
        self.subtitles = subtitles if settings.subtitles.enabled else None
        self._subtitle_response_id = None
        self._voice_enabled = (
            settings.tts.enabled
            and settings.audio.enabled
            and not self.disabled_features.intersection(
                {"tts", "tts_reference", "audio", "audio_files", "disk"}
            )
        )
        if self._voice_enabled and (tts is None or player is None):
            raise ValueError("enabled voice pipeline requires TTS and audio clients")
        self._update_subtitle()

    def _update_subtitle(self, response=None):
        if self.subtitles is None:
            return
        self._subtitle_response_id = (
            response.response_id if response is not None else None
        )
        try:
            if response is None:
                self.subtitles.clear()
            else:
                self.subtitles.write(response)
        except SubtitleError:
            self._component_faults["subtitles"] = "file_update_failed"
            logger.warning("subtitle_status=update_failed state=unknown")

    def _discard(self, item, reason):
        receipt = self._receipts.pop(id(item), None)
        if receipt is not None:
            ResponseTrace(*receipt).mark("discard", status=reason)

    def submit(self, item):
        # External text and IDs never enter telemetry.
        stamp, response_id = monotonic(), str(uuid4())
        trace = ResponseTrace(response_id, stamp)
        trace.mark("received", at=stamp)
        if self._closed or self.runtime.input_locked:
            self._input_drops["input_locked"] += 1
            trace.mark("discard", status="input_locked")
            return False
        if (
            not item.text.strip()
            or len(item.text) > self.settings.max_input_chars
            or not item.message_id
            or len(item.message_id) > 256
            or len(item.viewer.user_id) > 256
            or len(item.viewer.platform) > 32
        ):
            self._input_drops["invalid_input"] += 1
            trace.mark("discard", status="invalid_input")
            return False
        accepted = self.queue.put(item)
        if accepted:
            self._receipts[id(item)] = (response_id, stamp)
            self._ready.set()
        else:
            trace.mark("discard", status=self.queue.last_rejection or "duplicate")
        return accepted

    async def stop(self):
        """Trusted local operator only; never dispatch this from model/chat text."""
        self._epoch += 1
        if self.microphone is not None:
            await self.microphone.cancel()
        subtitle_response_id = self._subtitle_response_id
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
            self.runtime.cleanup_failed = True
            logger.warning("audio_status=stop_failed")
        finally:
            audio_stop_done.set()
            # Audio abort precedes file I/O. An old stop must not erase a newer answer.
            if self._subtitle_response_id == subtitle_response_id:
                self._update_subtitle()
        if self.rag:
            await self.rag.drain(cancel=True)
            if self.rag.last.get("capture") == "cleanup_failed":
                self.runtime.cleanup_failed = True

    async def pause(self):
        self.runtime.paused = True
        self.runtime.controls_pending += 1
        try:
            await self.stop()
        finally:
            self.runtime.controls_pending -= 1

    async def panic(self):
        self.runtime.panic = True
        self.runtime.muted = True
        await self.pause()

    def _can_restore(self):
        return not (
            self._closed
            or self.runtime.cleanup_failed
            or self.runtime.controls_pending
            or self.runtime.microphone_active
            or self._lock.locked()
            or not getattr(self.llm, "cleanup_confirmed", True)
        )

    def recover(self):
        """Explicit panic acknowledgement; remains paused and muted."""
        if not self._can_restore():
            return False
        self.runtime.panic = False
        return True

    def resume(self):
        if self.runtime.panic or not self._can_restore():
            return False
        self.queue.clear()
        self.runtime.accept_after = time()
        self.runtime.paused = False
        return True

    async def mute(self):
        self.runtime.muted = True
        self.runtime.voice_epoch += 1
        self.runtime.controls_pending += 1
        if self._mouth_session is not None:
            self._mouth_session.stop()
        if self._voice is not None and not self._voice.done():
            self._voice.cancel()
        done = self._audio_stop_done = asyncio.Event()
        try:
            if self._voice_enabled:
                await self.player.stop()
        except AudioError:
            self.runtime.cleanup_failed = True
            logger.warning("audio_status=stop_failed")
        finally:
            done.set()
            self.runtime.controls_pending -= 1

    def unmute(self):
        if self.runtime.panic or not self._can_restore():
            return False
        self.runtime.muted = False
        return True

    def status(self):
        queue_length = len(self.queue)  # Expire before reporting discard aggregates.
        drops = dict(self.queue.dropped)
        for reason, count in self._input_drops.items():
            drops[reason] = drops.get(reason, 0) + count
        operating_state = OK
        if (
            self.disabled_features
            or self._component_faults
            or any(c.status == DEGRADED for c in self.health_checks)
        ):
            operating_state = DEGRADED
        if self.settings.vts.enabled and not self._avatar_available:
            operating_state = DEGRADED
        if self.runtime.cleanup_failed or not getattr(
            self.llm, "cleanup_confirmed", True
        ):
            operating_state = RECOVERY
        return {
            "operating_state": operating_state,
            "component_faults": dict(self._component_faults),
            "microphone": self.microphone.state if self.microphone else "disabled",
            "startup_checks": report_data(self.health_checks),
            "disabled_features": sorted(self.disabled_features),
            "paused": self.runtime.paused,
            "panic": self.runtime.panic,
            "muted": self.runtime.muted,
            "phase": self.runtime.phase,
            "queue": queue_length,
            "input_drops": drops,
            "cleanup_failed": self.runtime.cleanup_failed,
            "cleanup_pending": bool(
                self.runtime.controls_pending or self._lock.locked()
            ),
            "llm": "cleanup_unconfirmed"
            if not getattr(self.llm, "cleanup_confirmed", True)
            else "not_probed",
            "tts": "not_probed" if self._voice_enabled else "disabled",
            "audio": "cleanup_failed"
            if self.runtime.cleanup_failed
            else "not_probed"
            if self._voice_enabled
            else "disabled",
            "vts": "session_available"
            if self._avatar_available
            else "unavailable"
            if self.settings.vts.enabled
            else "disabled",
            "youtube": "not_probed" if self.settings.youtube.enabled else "disabled",
            "chzzk": "not_probed" if self.settings.chzzk.enabled else "disabled",
            "rag": dict(self.rag.last) if self.rag else {"status": "disabled"},
            "dialogue": dict(self.dialogue.last)
            if self.dialogue
            else {"status": "disabled"},
        }

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
        if self.dialogue:
            self.dialogue.delete(viewer)

    async def forget_rag(self, viewer):
        """Trusted operator command; clear RAM and persistent scoped memories."""
        await self.pause()
        self.history.delete(viewer)
        if self.dialogue:
            self.dialogue.delete(viewer)
        if self.rag:
            await self.rag.forget(viewer)
            return True
        return False

    def set_broadcast_state(self, key, value):
        """Operator/runtime values only; viewer text never calls this method."""
        from virtual_ai.memory.sqlite_store import _text

        if key not in ("game", "title"):
            raise ValueError("allowed state keys: game, title")
        self.broadcast_state[key] = _text(value, 200)

    async def forget_long(self):
        if self.memory:
            self.memory.blocked = True
        await self.pause()
        self.history.delete()
        if self.dialogue:
            self.dialogue.delete()
        if not self.memory:
            return False
        try:
            result = await self.memory.forget()
            self._component_faults.pop("memory", None)
            return result
        except StoreError as exc:
            self._component_faults["memory"] = exc.reason
            return False

    async def summarize_memory(self):
        if (
            not self.memory
            or self.memory.blocked
            or self._lock.locked()
            or self._voice
            or len(self.queue)
        ):
            return False
        async with self._lock:
            try:
                return bool(
                    await self.memory.call(
                        summarize,
                        self.memory.store,
                        self.memory.user_id,
                        self.memory.session_id,
                    )
                )
            except StoreError as exc:
                self._component_faults["memory"] = exc.reason
                return False

    async def _dialogue_evidence(self, item, turn):
        if not turn:
            return await self.rag.prepare(
                item.viewer,
                item.text,
                self.history.messages(item.viewer),
                state=self.broadcast_state,
            )
        query = turn.query if turn else item.text
        route = retrieval_plan(query, self.history.messages(item.viewer))
        if turn and turn.mode in (
            "chat",
            "greeting",
            "reaction",
            "group_reaction",
            "empathy",
            "celebrate",
            "correction",
            "clarify",
        ):
            route = Plan("chat", query)
        if self.dialogue and turn:
            state = self.dialogue.session(item.viewer)
            if (
                route.kind == "memory"
                and state.correction
                and set(terms(query)).intersection(terms(state.correction))
            ):
                self.rag.last = {"retrieval_seconds": 0}
                return Evidence(
                    Plan("session", query),
                    rows=[
                        {
                            "id": "session",
                            "text": state.correction,
                            "kind": "memory",
                            "source": "current_viewer_statement",
                            "version": "session",
                        }
                    ],
                    status="available",
                )
            if (
                route.kind == "chat"
                and turn.mode == "chat"
                and turn.topic
                and self.dialogue.policy.contextual_memory
                and not state.correction
            ):
                found = await self.rag.prepare(
                    item.viewer, turn.topic, route=Plan("memory", turn.topic)
                )
                topic_terms = set(terms(turn.topic)) - {"게임", "선호", "불호"}
                found.rows = [
                    r
                    for r in self.dialogue.memory_eligible(item.viewer, found.rows)
                    if topic_terms.intersection(terms(r["text"]))
                ]
                if found.rows:
                    return found
        return await self.rag.prepare(
            item.viewer,
            query,
            self.history.messages(item.viewer),
            state=self.broadcast_state,
            route=route,
        )

    async def process_next(self, *, raise_errors=False):
        async with self._lock:
            if self._closed or self.runtime.input_locked:
                self.queue.clear()
                return None
            queued = self.queue.pop_timed()
            if queued is None:
                return None
            submitted_at, item = queued
            epoch = self._epoch
            response_id, received = self._receipts.pop(
                id(item), (str(uuid4()), submitted_at)
            )
            trace = ResponseTrace(response_id, received)
            trace.mark("selected")
            deadline = None
            if self.dialogue and item.viewer.platform != "console":
                age = max(
                    monotonic() - submitted_at,
                    time() - item.published_at if item.published_at is not None else 0,
                )
                deadline = (
                    monotonic() + self.dialogue.policy.public_response_ttl_seconds - age
                )
            evidence = None
            displayed = False
            playback = "not_started"
            response = None
            turn = None
            try:
                logger.info(
                    "response_id=%s queue_seconds=%.6f",
                    response_id,
                    monotonic() - submitted_at,
                )
                try:
                    llm_started = None
                    if self._response_expired(deadline, trace):
                        return None
                    memory_context = ""
                    if self.rag:
                        trace.mark("retrieval_started")
                        changed = await self.rag.reconcile_history(self.history)
                        if changed and self.dialogue:
                            self.dialogue.delete()
                    if self.dialogue:
                        try:
                            turn = self.dialogue.prepare(
                                item,
                                age=max(
                                    monotonic() - submitted_at,
                                    time() - item.published_at
                                    if item.published_at is not None
                                    else 0,
                                ),
                            )
                        except StoreError:
                            raise LLMError(
                                "입력을 확인할 수 없습니다. 민감정보 없이 다시 입력해 주세요."
                            ) from None
                        if turn.mode.startswith("skip_"):
                            self.dialogue.last = {"mode": turn.mode}
                            trace.mark("dialogue_selection", status=turn.mode)
                            return None
                        if turn.mode == "reaction":
                            group = self.queue.coalesce_reactions(
                                item,
                                submitted_at,
                                self.dialogue.policy.reaction_window_seconds,
                                ttl=self.dialogue.policy.reaction_ttl_seconds,
                            )
                            turn = replace(
                                turn,
                                mode="group_reaction" if group > 1 else "reaction",
                                group_count=group,
                            )
                        if turn.mode == "correction":
                            self.history.delete(item.viewer)
                    if self.rag:
                        evidence = await self._dialogue_evidence(item, turn)
                        trace.mark("retrieval_complete", status=evidence.status)
                        if evidence.status in ("timeout", "error"):
                            self._component_faults["rag"] = evidence.status
                        else:
                            self._component_faults.pop("rag", None)
                        logger.info(
                            "response_id=%s retrieval_seconds=%.6f",
                            response_id,
                            self.rag.last.get("retrieval_seconds", 0),
                        )
                    if self.memory and not self.rag:
                        try:
                            memory_context = await self.memory.retrieve(
                                item.viewer, item.text
                            )
                            self._component_faults.pop("memory", None)
                        except StoreError as exc:
                            self._component_faults["memory"] = exc.reason
                    if (
                        epoch != self._epoch
                        or self.runtime.input_locked
                        or self._closed
                    ):
                        return None
                    rag_context = (
                        evidence.context(compact_ids=True)
                        if evidence and evidence.status == "available"
                        else ""
                    )
                    messages = (
                        []
                        if turn and turn.direct
                        else build_messages(
                            self.character,
                            self.history.messages(item.viewer),
                            item.text,
                            system_prompt=self.system_prompt,
                            settings=self.settings,
                            memory_context=memory_context,
                            rag_context=rag_context,
                            dialogue_context=self.dialogue.context(item.viewer, turn)
                            if turn
                            else "",
                            selection_only=bool(rag_context),
                        )
                    )
                    if (
                        rag_context
                        and messages
                        and rag_context not in messages[-1]["content"]
                    ):
                        evidence.status, evidence.rows = "budget", []
                    if turn and turn.direct:
                        raw = turn.direct
                    elif (
                        evidence
                        and evidence.plan.kind != "chat"
                        and evidence.status != "available"
                    ):
                        raw = RAG_FALLBACKS.get(evidence.status, RAG_FALLBACKS["error"])
                    else:
                        if self._response_expired(deadline, trace):
                            return None
                        llm_started = monotonic()
                        self.runtime.phase = "llm"
                        trace.mark("llm_started")
                        self._generation = asyncio.create_task(
                            self.llm.generate(messages)
                        )
                        if deadline is None:
                            raw = await self._generation
                        else:
                            # Cancellation reaches the adapter's existing keyed
                            # abort/idle confirmation before another turn can run.
                            end = min(
                                deadline,
                                monotonic()
                                + self.dialogue.policy.generation_timeout_seconds,
                            )
                            async with asyncio.timeout_at(end):
                                raw = await self._generation
                            if monotonic() >= end:
                                raise TimeoutError
                    trace.mark(
                        "llm_complete",
                        status="ok" if llm_started is not None else "skipped",
                    )
                    self._component_faults.pop("llm", None)
                    if asyncio.current_task().cancelling():
                        raise asyncio.CancelledError
                except asyncio.CancelledError:
                    # A stopped response must not terminate the long-lived worker.
                    # Cancellation of the worker itself must still propagate.
                    if asyncio.current_task().cancelling() or epoch == self._epoch:
                        raise
                    return None
                except TimeoutError:
                    self._component_faults["llm"] = "deadline_exceeded"
                    self.dialogue.last = {"mode": "skip_generation_timeout"}
                    trace.mark("llm_complete", status="deadline_exceeded")
                    if not getattr(self.llm, "cleanup_confirmed", True):
                        self.runtime.paused = True
                        self.queue.clear()
                    if raise_errors:
                        raise LLMError(
                            "생방송 응답 제한 시간을 초과했습니다."
                        ) from None
                    return None
                except LLMError as exc:
                    self._component_faults["llm"] = "request_failed"
                    trace.mark("llm_complete", status="failed")
                    logger.warning("response_id=%s llm_status=failed", response_id)
                    if raise_errors:
                        raise
                    if epoch == self._epoch:
                        self.output(str(exc))
                    return None
                finally:
                    self._generation = None
                    self.runtime.phase = "idle"
                    logger.info(
                        "response_id=%s llm_seconds=%.6f",
                        response_id,
                        monotonic() - llm_started if llm_started is not None else 0,
                    )
                if epoch != self._epoch or self._response_expired(deadline, trace):
                    return None
                output_settings = self.settings
                if (
                    turn
                    and turn.short
                    and (not evidence or evidence.plan.kind == "chat")
                ):
                    output_settings = replace(self.settings, max_sentences=1)
                if evidence and evidence.plan.detailed:
                    output_settings = replace(
                        self.settings,
                        max_sentences=8,
                        max_output_chars=max(1200, self.settings.max_output_chars),
                    )
                final_raw = raw
                if evidence:
                    validation_started = monotonic()
                    if not await self.rag.fresh(evidence, state=self.broadcast_state):
                        evidence.status, evidence.rows = "stale", []
                    if epoch != self._epoch or self._closed:
                        return None
                    final_raw, validation = self.rag.validate(
                        raw,
                        evidence,
                        output_settings,
                        selection_only=True,
                        style=self.dialogue.policy.style
                        if turn and self.dialogue.policy.natural_grounding
                        else None,
                    )
                    self.rag.last.update(evidence.diagnostic())
                    self.rag.last["validation"] = validation
                    self.rag.last["response_id"] = response_id
                    trace.mark("grounding_checked", status=validation)
                    logger.info(
                        "response_id=%s validation_seconds=%.6f",
                        response_id,
                        monotonic() - validation_started,
                    )
                response = replace(
                    prepare_response(response_id, final_raw, output_settings), raw=raw
                )
                if turn:
                    grounded = bool(evidence and evidence.plan.kind != "chat")
                    guarded = self.dialogue.guard(
                        item.viewer, turn, response, grounded=grounded
                    )
                    if guarded != response.final:
                        response = replace(
                            prepare_response(response_id, guarded, output_settings),
                            raw=raw,
                        )
                trace.mark(
                    "output_checked", status="blocked" if response.blocked else "ok"
                )
                response = replace(
                    response,
                    expression=dialogue_expression(
                        response,
                        turn,
                        self.settings.allowed_expressions,
                        self.expression_policy,
                    )
                    if turn
                    else select_expression(
                        response,
                        self.settings.allowed_expressions,
                        self.expression_policy,
                    ),
                )
                if self._response_expired(deadline, trace):
                    return None
                self.output(response.final)
                displayed = True
                self._update_subtitle(response)
                if not response.blocked:
                    self.history.add(item.viewer, item.text, response.final)
                    if turn:
                        self.dialogue.corrected(item.viewer, turn)
                        used_ids = (
                            self.rag.last.get("used_source_ids", []) if self.rag else []
                        )
                        self.dialogue.record(
                            item.viewer,
                            item.text,
                            turn,
                            response.final,
                            used_rows=[r for r in evidence.rows if r["id"] in used_ids]
                            if evidence
                            else [],
                        )
                    if self.rag:
                        self.rag.capture(
                            item.viewer,
                            item.text,
                            response_id,
                            valid=lambda: epoch == self._epoch and not self._closed,
                            statement=turn.statement if turn else None,
                        )
                if (
                    self._voice_enabled
                    and not self.runtime.muted
                    and response.speech.strip()
                ):
                    voice_epoch = self.runtime.voice_epoch
                    self._voice = asyncio.create_task(
                        self._speak(
                            response, epoch, trace, evidence=evidence, deadline=deadline
                        )
                    )
                    try:
                        playback = await self._voice or "unknown"
                        if asyncio.current_task().cancelling():
                            raise asyncio.CancelledError
                    except asyncio.CancelledError:
                        playback = "cancelled"
                        if asyncio.current_task().cancelling() or (
                            epoch == self._epoch
                            and voice_epoch == self.runtime.voice_epoch
                        ):
                            raise
                        # Text already displayed and recorded remains valid.
                    finally:
                        self._voice = None
                        self.runtime.phase = "idle"
                return response
            finally:
                if self.rag and displayed:
                    await self.rag.delivery(
                        item.viewer, response_id, displayed, playback
                    )
                    self.rag.last["displayed"] = displayed
                    self.rag.last["playback"] = playback
                trace.mark(
                    "cleanup_complete",
                    status="recovery_required"
                    if self.runtime.cleanup_failed
                    else "cancelled"
                    if epoch != self._epoch
                    else "settled",
                )

    def _response_expired(self, deadline, trace=None):
        if deadline is None or monotonic() < deadline:
            return False
        self.dialogue.last = {"mode": "skip_expired"}
        if trace is not None:
            trace.mark("discard", status="response_expired")
        return True

    async def _speak(
        self, response, epoch, trace=None, *, evidence=None, deadline=None
    ):
        # IDs come from uuid4 above, never model output or incoming message IDs.
        destination = self.audio_directory / f"{response.response_id}.wav"
        voice_epoch = self.runtime.voice_epoch
        avatar_attempted = False
        mouth = None
        playback_status = "not_started"

        def cancelled():
            return (
                self._closed
                or epoch != self._epoch
                or self.runtime.muted
                or voice_epoch != self.runtime.voice_epoch
                or asyncio.current_task().cancelling()
            )

        try:
            if cancelled() or self._response_expired(deadline, trace):
                return "cancelled"
            started = monotonic()
            try:
                self.runtime.phase = "tts"
                await self.tts.synthesize(response.speech, destination)
                if trace is not None:
                    trace.mark("tts_complete")
                self._component_faults.pop("tts", None)
            finally:
                logger.info(
                    "response_id=%s tts_seconds=%.6f",
                    response.response_id,
                    monotonic() - started,
                )
            if cancelled() or self._response_expired(deadline, trace):
                if self._subtitle_response_id == response.response_id:
                    self._update_subtitle()
                return "cancelled"
            if (
                evidence
                and self.rag
                and not await self.rag.fresh(evidence, state=self.broadcast_state)
            ):
                trace.mark("voice_evidence", status="stale") if trace else None
                if self._subtitle_response_id == response.response_id:
                    self._update_subtitle()
                return "cancelled"
            if self._avatar_available:
                avatar_attempted = True
                await self._avatar_call(
                    self.avatar.set_expression,
                    response.expression,
                    valid=lambda: not cancelled(),
                )
            if cancelled() or self._response_expired(deadline, trace):
                if self._subtitle_response_id == response.response_id:
                    self._update_subtitle()
                return "cancelled"
            started = monotonic()
            playback_status = "failed"
            try:
                self.runtime.phase = "playback"
                if self.settings.vts.lipsync_enabled and self._avatar_available:
                    mouth = self._mouth_session = MouthSync(
                        self.settings.vts,
                        self._send_mouth,
                        lambda: (
                            not self._closed
                            and epoch == self._epoch
                            and voice_epoch == self.runtime.voice_epoch
                            and not self.runtime.muted
                            and self._avatar_available
                        ),
                    )
                    played = await self.player.play(destination, levels=mouth.levels)
                else:
                    played = await self.player.play(destination)
                playback_status = "completed" if played else "cancelled"
                self._component_faults.pop("audio", None)
            finally:
                if trace is not None:
                    first = getattr(self.player, "first_callback_at", None)
                    if (
                        isinstance(first, (int, float))
                        and started <= first <= monotonic()
                    ):
                        trace.mark("first_playback_callback", at=first)
                    else:
                        trace.mark("first_playback_callback", status="unobserved")
                    duration = getattr(self.player, "audio_seconds", None)
                    if isinstance(duration, (int, float)):
                        logger.info(
                            "response_id=%s audio_seconds=%.6f",
                            response.response_id,
                            duration,
                        )
                    mouth_at = getattr(mouth, "first_send_at", None)
                    if (
                        isinstance(first, (int, float))
                        and isinstance(mouth_at, (int, float))
                        and started <= first <= mouth_at
                    ):
                        logger.info(
                            "response_id=%s mouth_send_after_callback_seconds=%.6f",
                            response.response_id,
                            mouth_at - first,
                        )
                    trace.mark(
                        "playback_finished",
                        status="cancelled" if cancelled() else playback_status,
                    )
                logger.info(
                    "response_id=%s playback_seconds=%.6f",
                    response.response_id,
                    monotonic() - started,
                )
        except (TTSError, AudioError) as exc:
            playback_status = "failed"
            self._component_faults["tts" if isinstance(exc, TTSError) else "audio"] = (
                "request_or_device_failed"
            )
            if trace is not None:
                trace.mark(
                    "voice_failed",
                    status="tts" if isinstance(exc, TTSError) else "audio",
                )
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
                self.runtime.cleanup_failed = True
                logger.warning(
                    "response_id=%s audio_cleanup=failed", response.response_id
                )
            if interrupted:
                raise asyncio.CancelledError
        return playback_status

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
            while (
                len(self.queue) and not self._closed and not self.runtime.input_locked
            ):
                await self.process_next()


async def run_cli(args):
    from virtual_ai.inputs.console import console
    from virtual_ai.schemas import ChatInput, Viewer

    settings, character = load_config(Path(args.config).resolve())
    if getattr(args, "rag_db", None):
        settings = replace(
            settings,
            rag=replace(
                settings.rag,
                enabled=True,
                db_path=args.rag_db,
                scope=getattr(args, "rag_scope", None) or settings.rag.scope,
            ),
        )
    if args.backend:
        settings = replace(settings, backend=args.backend)
    if getattr(args, "dialogue", False):
        settings = replace(settings, dialogue=replace(settings.dialogue, enabled=True))
    health_checks = ()
    if getattr(args, "local_only", False):
        settings = replace(
            settings,
            youtube=replace(settings.youtube, enabled=False),
            chzzk=replace(settings.chzzk, enabled=False),
        )
    if (
        getattr(args, "live", False)
        or settings.youtube.enabled
        or settings.chzzk.enabled
        or getattr(args, "check_startup", False)
    ):
        health_checks = await check_health(settings)
        for check in health_checks:
            logger.info(
                "startup component=%s status=%s reason=%s seconds=%.6f",
                check.component,
                check.status,
                check.reason,
                check.seconds,
            )
        if any(
            c.status == RECOVERY and c.component in {"config", "prompt_file", "llm"}
            for c in health_checks
        ):
            raise ValueError("Startup requires recovery; run the diagnostics command.")
    disabled = {c.component for c in health_checks if c.status == RECOVERY}
    voice_allowed = (
        settings.tts.enabled
        and settings.audio.enabled
        and not disabled.intersection(
            {"tts", "tts_reference", "audio", "audio_files", "disk"}
        )
    )
    llm = (
        FakeLLM() if settings.backend in ("mock", "fake") else KoboldCppClient(settings)
    )
    async with AsyncExitStack() as resources:
        resources.callback(logger.info, "application_status=closed")
        resources.push_async_callback(llm.aclose)
        tts = player = None
        if voice_allowed:
            tts = GPTSoVITSClient(settings.tts)
            resources.push_async_callback(tts.aclose)
            player = WAVPlayer(settings.audio)
            resources.push_async_callback(player.aclose)
        avatar = None
        if settings.vts.enabled and voice_allowed and "vts" not in disabled:
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
            settings,
            character,
            llm,
            tts=tts,
            player=player,
            avatar=avatar,
            expression_policy=conservative_expression,
            live=getattr(args, "live", False),
            health_checks=health_checks,
            subtitles=SubtitleWriter(settings.subtitles)
            if settings.subtitles.enabled and "subtitles" not in disabled
            else None,
        )
        if settings.rag.enabled:
            store = await asyncio.to_thread(RAGStore, settings.rag.db_path)
            app.rag = RAGPipeline(store, settings.rag)
        if getattr(args, "memory_db", None):
            try:
                store = await asyncio.to_thread(
                    SQLiteStore, args.memory_db, enabled=True
                )
                app.memory = MemoryContext(
                    store,
                    getattr(args, "memory_user", "local"),
                    getattr(args, "memory_session", None),
                )
            except StoreError as exc:
                app._component_faults["memory"] = exc.reason
        if getattr(args, "microphone_device", None) is not None:
            from virtual_ai.inputs.microphone import MicrophoneInput
            from virtual_ai.stt.base import STTError
            from virtual_ai.stt.client import ProcessSTT

            try:
                client = ProcessSTT(
                    args.stt_python, args.stt_model, device=args.stt_device
                )
                app.microphone = MicrophoneInput(app, client, args.microphone_device)
            except (STTError, TypeError):
                app._component_faults["microphone"] = "invalid_configuration"
        resources.push_async_callback(app.shutdown)
        if getattr(args, "benchmark", None):
            from virtual_ai.performance import benchmark

            await benchmark(app, args)
        elif args.once is not None:
            if not app.submit(
                ChatInput(Viewer("console", "local"), args.once, str(uuid4()))
            ):
                raise ValueError("입력이 비었거나 길이 제한을 초과했습니다.")
            await app.process_next(raise_errors=True)
        else:
            chat_task = None
            chzzk_task = None
            if settings.youtube.enabled and "youtube" not in disabled:
                adapter_class = (
                    YouTubeStream
                    if settings.youtube.transport == "stream"
                    else YouTubeChat
                )
                adapter = adapter_class(
                    settings.youtube,
                    max_age=settings.queue_ttl_seconds,
                    accept_after=lambda: app.runtime.accept_after,
                )

                def chat_stopped(reason):
                    app._component_faults["youtube"] = reason

                chat_task = asyncio.create_task(
                    receive_chat(adapter, app.submit, chat_stopped)
                )
            if settings.chzzk.enabled and "chzzk" not in disabled:
                chzzk_adapter = ChzzkChat(
                    settings.chzzk,
                    max_age=settings.queue_ttl_seconds,
                    accept_after=lambda: app.runtime.accept_after,
                )

                def chzzk_stopped(reason):
                    app._component_faults["chzzk"] = reason

                chzzk_task = asyncio.create_task(
                    receive_chzzk(chzzk_adapter, app.submit, chzzk_stopped)
                )
            try:
                await console(app)
            finally:
                if chzzk_task is not None:
                    chzzk_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await chzzk_task
                if chat_task is not None:
                    chat_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await chat_task


def configure_console_output() -> None:
    """CLI의 표준 출력과 오류 출력을 UTF-8로 통일한다."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


def main():
    configure_console_output()
    parser = argparse.ArgumentParser(description="Virtual AI text conversation")
    parser.add_argument(
        "--check-startup",
        action="store_true",
        help="Run non-speaking startup diagnostics",
    )
    parser.add_argument("--config", default="configs/app.example.yaml")
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument("--operator-session-id", help=argparse.SUPPRESS)
    parser.add_argument("--backend", choices=("mock", "fake", "koboldcpp"))
    parser.add_argument(
        "--dialogue", action="store_true", help="Enable contextual dialogue planning"
    )
    parser.add_argument(
        "--log-level", choices=("INFO", "WARNING", "ERROR"), default="INFO"
    )
    parser.add_argument(
        "--live", action="store_true", help="Start paused for supervised live operation"
    )
    parser.add_argument("--memory-db", help="Opt-in local memory database")
    parser.add_argument("--memory-user", default="local")
    parser.add_argument("--memory-session")
    parser.add_argument("--rag-db", help="Opt-in scoped RAG database")
    parser.add_argument(
        "--rag-scope", help="Trusted channel/operator scope; used with --rag-db"
    )
    parser.add_argument("--microphone-device", type=int)
    parser.add_argument("--stt-python")
    parser.add_argument("--stt-model")
    parser.add_argument("--stt-device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument(
        "--benchmark", help="JSON question set; isolated repeated measurement"
    )
    parser.add_argument("--benchmark-report", default=".local/performance.json")
    parser.add_argument("--benchmark-rounds", type=int, default=10)
    parser.add_argument("--benchmark-soak-seconds", type=float, default=0)
    parser.add_argument("--once", help="한 번 입력하고 종료")
    args = parser.parse_args()
    if args.rag_scope and not args.rag_db:
        parser.error("--rag-scope requires --rag-db")
    if args.local_only and args.live:
        parser.error("--local-only cannot be combined with --live")
    if args.benchmark and (args.once is not None or args.live):
        parser.error("benchmark cannot be combined with --once or --live")
    if args.live and args.once is not None:
        parser.error("--live cannot be combined with --once")
    logging.basicConfig(
        level=args.log_level, format="%(levelname)s %(name)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(run_cli(args))
    except LLMError as exc:
        parser.exit(1, f"LLM 오류: {exc}\n")
    except StoreError as exc:
        parser.exit(1, "RAG 저장소 오류: " + exc.reason + "\n")
    except (AudioError, TTSError):
        parser.exit(1, "음성 클라이언트 정리 중 오류가 발생했습니다.\n")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"설정/실행 오류: {exc}\n")
    except KeyboardInterrupt:
        pass
