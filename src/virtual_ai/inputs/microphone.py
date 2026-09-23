"""Trusted push-to-talk, half duplex, bounded PCM; no always-on capture."""

import asyncio
import tempfile
import wave
from pathlib import Path
from uuid import uuid4

from virtual_ai.audio.base import AudioError
from virtual_ai.audio.player import load_backend
from virtual_ai.schemas import ChatInput, Viewer
from virtual_ai.stt.base import MAX_SECONDS, RATE, STTError, read_audio, validate_text


class MicrophoneInput:
    def __init__(self, app, client, device, *, backend=None, max_seconds=MAX_SECONDS):
        if (
            type(device) is not int
            or device < 0
            or not 0.2 <= max_seconds <= MAX_SECONDS
        ):
            raise STTError("invalid_microphone_configuration")
        self.app, self.client, self.device = app, client, device
        self.backend, self.max_seconds = backend, max_seconds
        self.task = None
        self.released = asyncio.Event()
        self.state = "idle"
        self.reason = None

    def start(self):
        if self.task and not self.task.done():
            return False
        if self.app.runtime.input_locked or self.app._closed:
            return False
        self.released = asyncio.Event()
        self.reason = None
        self.app.runtime.microphone_active = True
        self.state = "stopping_output"
        self.task = asyncio.create_task(self._run())
        return True

    def release(self):
        if self.task and not self.task.done():
            self.released.set()
            return True
        return False

    async def cancel(self):
        if (
            self.task
            and self.task is not asyncio.current_task()
            and not self.task.done()
        ):
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.app.runtime.microphone_active = False
            self.app.runtime.paused = True
            self.state = "idle"

    async def _run(self):
        app = self.app
        stream = None
        success = False
        try:
            await app.pause()
            epoch = app._epoch
            async with app._lock:
                pass
            if (
                app.runtime.cleanup_failed
                or app.runtime.panic
                or not getattr(app.llm, "cleanup_confirmed", True)
            ):
                raise STTError("output_cleanup_unconfirmed")
            # A short drain interval is not acoustic echo cancellation; use headphones.
            await asyncio.sleep(0.25)
            if self.released.is_set():
                raise STTError("empty_recording")
            backend = self.backend or load_backend()
            backend.check_input_settings(
                device=self.device, channels=1, dtype="int16", samplerate=RATE
            )
            pcm = bytearray()
            capacity = int(self.max_seconds * RATE) * 2
            fault = []
            loop = asyncio.get_running_loop()
            notified = False

            def callback(indata, frames, timing, status):
                nonlocal notified
                if status and not fault:
                    fault.append(True)
                remaining = capacity - len(pcm)
                if remaining > 0:
                    pcm.extend(bytes(indata)[:remaining])
                if (len(pcm) >= capacity or fault) and not notified:
                    notified = True
                    loop.call_soon_threadsafe(self.released.set)

            stream = backend.RawInputStream(
                device=self.device,
                samplerate=RATE,
                channels=1,
                dtype="int16",
                blocksize=800,
                callback=callback,
            )
            stream.start()
            self.state = "recording"
            try:
                await asyncio.wait_for(self.released.wait(), self.max_seconds)
            except TimeoutError:
                pass
            stream.stop()
            stream.close()
            stream = None
            if fault:
                raise STTError("input_overflow")
            with tempfile.TemporaryDirectory(prefix="virtual-ai-mic-") as folder:
                path = Path(folder) / "input.wav"
                with wave.open(str(path), "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(RATE)
                    wav.writeframes(pcm)
                read_audio(path)
                self.state = "transcribing"
                text = validate_text(
                    await self.client.transcribe(path), app.settings.max_input_chars
                )
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            if epoch != app._epoch or app._closed or app.runtime.panic:
                raise STTError("stale_input")
            app.runtime.microphone_active = False
            if not app.resume():
                raise STTError("resume_blocked")
            if not app.submit(
                ChatInput(Viewer("microphone", "local"), text, str(uuid4()))
            ):
                raise STTError("input_rejected")
            success = True
        except asyncio.CancelledError:
            self.reason = "cancelled"
            raise
        except STTError as exc:
            self.reason = exc.reason
        except (AudioError, OSError, ValueError, RuntimeError):
            self.reason = "microphone_failed"
        except Exception:
            # PortAudio errors differ across installed backends; never expose their payload.
            self.reason = "microphone_failed"
        finally:
            if stream is not None:
                try:
                    stream.abort()
                    stream.close()
                except Exception:
                    app.runtime.cleanup_failed = True
                    self.reason = "microphone_cleanup_failed"
            app.runtime.microphone_active = False
            self.state = "idle"
            if not success:
                app.runtime.paused = True
            if self.reason:
                app._component_faults["microphone"] = self.reason
            else:
                app._component_faults.pop("microphone", None)
