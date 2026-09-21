"""Non-streaming GPT-SoVITS API v2 WAV client."""

import asyncio
import io
import os
import struct
import tempfile
import wave
from pathlib import Path

import httpx

from virtual_ai.config import TTSSettings
from virtual_ai.tts.base import TTSError


def _validate_wav(data: bytes) -> None:
    try:
        if (
            data[:4] != b"RIFF"
            or data[8:12] != b"WAVE"
            or int.from_bytes(data[4:8], "little") + 8 != len(data)
        ):
            raise ValueError()
        offset = 12
        data_sizes = []
        while offset < len(data):
            if offset + 8 > len(data):
                raise ValueError()
            size = int.from_bytes(data[offset + 4 : offset + 8], "little")
            if data[offset : offset + 4] == b"data":
                data_sizes.append(size)
            offset += 8 + size + size % 2
            if offset > len(data):
                raise ValueError()
        with wave.open(io.BytesIO(data), "rb") as audio:
            frames = audio.getnframes()
            frame_size = audio.getnchannels() * audio.getsampwidth()
            if (
                audio.getcomptype() != "NONE"
                or audio.getnchannels() not in (1, 2)
                or audio.getframerate() <= 0
                or frames <= 0
                or data_sizes != [frames * frame_size]
                or len(audio.readframes(frames)) != frames * frame_size
            ):
                raise ValueError()
    except (wave.Error, EOFError, ValueError, struct.error):
        raise TTSError("TTS 응답이 완전한 PCM WAV 파일이 아닙니다.") from None


def _save_wav(data: bytes, destination: Path) -> Path:
    temporary = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, suffix=".tmp", delete=False
        ) as file:
            temporary = Path(file.name)
            file.write(data)
        os.replace(temporary, destination)
        return destination
    except OSError:
        raise TTSError(
            "WAV 파일을 저장할 수 없습니다. 출력 경로를 확인하세요."
        ) from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class GPTSoVITSClient:
    def __init__(self, settings: TTSSettings, transport=None):
        self.settings = settings
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            timeout=settings.timeout_seconds, transport=transport, trust_env=False
        )

    async def synthesize(self, text: str, destination: Path) -> Path:
        if not self.settings.enabled:
            raise TTSError("TTS가 비활성화되어 있습니다.")
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > self.settings.max_text_chars
        ):
            raise TTSError("합성할 텍스트가 비었거나 길이 제한을 초과했습니다.")
        destination = Path(destination)
        if destination.suffix.lower() != ".wav":
            raise TTSError("출력 파일의 확장자는 .wav여야 합니다.")
        try:
            async with asyncio.timeout(self.settings.total_timeout_seconds):
                async with self._lock:
                    data = await self._request(text.strip())
                    _validate_wav(data)
                    # Deliver pending cancellation before publishing a file. A transport
                    # that suppresses CancelledError must not publish a late result either.
                    await asyncio.sleep(0)
                    if asyncio.current_task().cancelling():
                        raise asyncio.CancelledError
                    return _save_wav(data, destination)
        except (TimeoutError, httpx.TimeoutException):
            raise TTSError("TTS 응답 시간이 초과되었습니다.") from None
        except httpx.ConnectError:
            raise TTSError(
                "TTS 서버에 연결할 수 없습니다. 서버와 주소를 확인하세요."
            ) from None
        except httpx.HTTPStatusError as exc:
            raise TTSError(
                f"TTS 요청이 실패했습니다 (HTTP {exc.response.status_code})."
            ) from None
        except httpx.RequestError:
            raise TTSError("TTS 서버 통신 오류가 발생했습니다.") from None

    async def _request(self, text: str) -> bytes:
        payload = {
            "text": text,
            "text_lang": self.settings.text_lang,
            "ref_audio_path": self.settings.ref_audio_path,
            "prompt_text": self.settings.prompt_text,
            "prompt_lang": self.settings.prompt_lang,
            "media_type": "wav",
            "streaming_mode": False,
        }
        async with self._client.stream(
            "POST", self.settings.base_url.rstrip("/") + "/tts", json=payload
        ) as response:
            response.raise_for_status()
            media_type = (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .lower()
                .strip()
            )
            if media_type not in ("audio/wav", "audio/x-wav", "audio/wave"):
                raise TTSError("TTS 응답의 Content-Type이 WAV가 아닙니다.")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > self.settings.max_response_bytes:
                    raise TTSError("TTS 응답 크기가 제한을 초과했습니다.")
                body.extend(chunk)
            return bytes(body)

    async def aclose(self) -> None:
        await self._client.aclose()
