"""Bounded PCM WAV playback with a single PortAudio-owning worker."""

import asyncio
import io
import struct
import threading
import time
import wave
from pathlib import Path

from virtual_ai.audio.base import AudioError
from virtual_ai.audio.levels import pcm_rms
from virtual_ai.config import AudioSettings

MAX_WAV_BYTES = 100 * 1024 * 1024


def load_backend():
    # Import lazily: text-only commands and fake-device CI need no PortAudio.
    try:
        import sounddevice
    except (ImportError, OSError):
        raise AudioError(
            "오디오 라이브러리 또는 PortAudio를 사용할 수 없습니다."
        ) from None
    return sounddevice


def _read_wav(path):
    try:
        with Path(path).open("rb") as source:
            data = source.read(MAX_WAV_BYTES + 1)
        if (
            len(data) > MAX_WAV_BYTES
            or data[:4] != b"RIFF"
            or data[8:12] != b"WAVE"
            or int.from_bytes(data[4:8], "little") + 8 != len(data)
        ):
            raise ValueError()
        with wave.open(io.BytesIO(data), "rb") as wav:
            channels, width, rate, frames = (
                wav.getnchannels(),
                wav.getsampwidth(),
                wav.getframerate(),
                wav.getnframes(),
            )
            if (
                channels not in (1, 2)
                or width not in (1, 2, 3, 4)
                or rate <= 0
                or frames <= 0
            ):
                raise ValueError()
            pcm = wav.readframes(frames)
            if len(pcm) != frames * channels * width or wav.getcomptype() != "NONE":
                raise ValueError()
        return pcm, channels, width, rate
    except (OSError, EOFError, wave.Error, ValueError, struct.error):
        raise AudioError(
            "WAV를 읽을 수 없습니다. 100 MiB 이하의 완전한 모노/스테레오 PCM WAV가 필요합니다."
        ) from None


class WAVPlayer:
    def __init__(self, settings: AudioSettings, *, backend=None):
        self.settings = settings
        self._backend = backend
        self._worker = None
        self._stop = threading.Event()
        self._closed = False
        self.first_callback_at = None
        self.audio_seconds = None

    async def _wait(self, worker, stop):
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(worker)
                break
            except asyncio.CancelledError:
                # to_thread cancellation alone does not stop the physical device.
                stop.set()
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def play(self, path: Path, *, levels=None) -> bool:
        if self._closed:
            raise AudioError("재생기가 종료됐습니다.")
        if not self.settings.enabled:
            raise AudioError("audio.enabled가 비활성화돼 있습니다.")
        if self._worker is not None and not self._worker.done():
            raise AudioError("이미 재생 중입니다. 먼저 중지하세요.")
        stop = self._stop = threading.Event()
        self.first_callback_at = None
        self.audio_seconds = None
        worker = self._worker = asyncio.get_running_loop().run_in_executor(
            None, self._play, path, stop, levels
        )
        try:
            return await self._wait(worker, stop)
        finally:
            if levels is not None:
                levels.close()
            if self._worker is worker:
                self._worker = None

    async def stop(self) -> None:
        worker = self._worker
        self._stop.set()
        if worker is not None:
            await self._wait(worker, self._stop)

    async def aclose(self) -> None:
        self._closed = True
        await self.stop()

    def _play(self, path, stop, levels=None):
        pcm, channels, width, rate = _read_wav(path)
        self.audio_seconds = len(pcm) / (channels * width * rate)
        if stop.is_set():
            return False
        backend = self._backend or load_backend()
        finished = threading.Event()
        errors = []
        position = 0
        stream = None
        drained = False
        frame_size = channels * width
        silence = b"\x80" if width == 1 else b"\x00"

        def callback(outdata, frames, timing, status):
            nonlocal position, levels
            outdata[:] = silence * len(outdata)
            if stop.is_set():
                raise backend.CallbackAbort
            try:
                if status:
                    raise AudioError("오디오 버퍼 오류로 재생을 중지했습니다.")
                end = min(position + frames * frame_size, len(pcm))
                outdata[: end - position] = pcm[position:end]
                position = end
                if self.first_callback_at is None:
                    self.first_callback_at = time.monotonic()
            except Exception:
                errors.append(AudioError("오디오 출력 콜백이 실패했습니다."))
                raise backend.CallbackAbort from None
            if levels is not None:
                try:
                    levels.publish(pcm_rms(outdata, width), frames / rate)
                except Exception:
                    # Metering must never interrupt audible output.
                    levels = None
            if position == len(pcm):
                raise backend.CallbackStop

        try:
            dtype = {1: "uint8", 2: "int16", 3: "int24", 4: "int32"}[width]
            backend.check_output_settings(
                device=self.settings.output_device,
                channels=channels,
                dtype=dtype,
                samplerate=rate,
            )
            if stop.is_set():
                return False
            stream = backend.RawOutputStream(
                device=self.settings.output_device,
                channels=channels,
                dtype=dtype,
                samplerate=rate,
                callback=callback,
                finished_callback=finished.set,
            )
            if stop.is_set():
                return False
            stream.start()
            deadline = time.monotonic() + len(pcm) / frame_size / rate + 10
            while not finished.wait(0.01):
                if stop.is_set():
                    return False
                # PortAudio may mark the stream inactive just before invoking
                # finished_callback. Only the callback confirms drained output.
                if time.monotonic() > deadline:
                    raise AudioError(
                        "출력 장치가 중단됐거나 재생 완료 시간이 초과됐습니다."
                    )
            if errors:
                raise errors[0]
            if stop.is_set():
                return False
            if position != len(pcm):
                raise AudioError("출력 장치가 WAV 끝에 도달하기 전에 중단됐습니다.")
            stream.stop(ignore_errors=False)  # Normal EOF: buffered samples drained.
            drained = True
            return True
        except AudioError:
            raise
        except Exception:
            raise AudioError(
                "출력 장치를 열거나 재생할 수 없습니다. 장치와 WAV 형식을 확인하세요."
            ) from None
        finally:
            if stream is not None:
                try:
                    try:
                        if not drained and not stream.stopped:
                            stream.abort(ignore_errors=False)  # Never drain on stop.
                    finally:
                        stream.close(ignore_errors=False)
                except Exception:
                    raise AudioError(
                        "오디오 장치 중지 또는 해제에 실패했습니다."
                    ) from None
