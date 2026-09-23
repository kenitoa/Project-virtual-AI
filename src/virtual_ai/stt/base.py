"""Bounded input and transcript validation shared by file and microphone paths."""

import io
import wave
from pathlib import Path
from typing import Protocol

from virtual_ai.audio.levels import pcm_rms
from virtual_ai.safety import contains_personal_data, contains_secret

RATE = 16000
MAX_SECONDS = 30
MAX_BYTES = RATE * 2 * MAX_SECONDS + 4096


class STTError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__("STT: " + reason)


class STTClient(Protocol):
    async def transcribe(self, path) -> str: ...


def read_audio(path):
    try:
        with Path(path).open("rb") as source:
            data = source.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise STTError("input_too_large")
        with wave.open(io.BytesIO(data), "rb") as wav:
            if (
                wav.getnchannels(),
                wav.getsampwidth(),
                wav.getframerate(),
                wav.getcomptype(),
            ) != (1, 2, RATE, "NONE"):
                raise STTError("expected_pcm16_mono_16khz")
            frames = wav.getnframes()
            if not RATE // 5 <= frames <= RATE * MAX_SECONDS:
                raise STTError("duration_limit")
            pcm = wav.readframes(frames)
            if len(pcm) != frames * 2:
                raise STTError("truncated_audio")
        if pcm_rms(pcm, 2) < 0.005:
            raise STTError("silence")
        return data
    except (OSError, EOFError, wave.Error, ValueError):
        raise STTError("invalid_audio") from None


def validate_text(text, limit=1000):
    if not isinstance(text, str) or not text.strip():
        raise STTError("no_speech")
    text = text.strip()
    if len(text) > limit:
        raise STTError("transcript_too_large")
    if contains_secret(text) or contains_personal_data(text):
        raise STTError("sensitive_transcript")
    return text
