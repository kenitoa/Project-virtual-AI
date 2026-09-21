"""WAV synthesis only; no playback or avatar control."""

from pathlib import Path
from typing import Protocol


class TTSError(Exception):
    pass


class TTSClient(Protocol):
    async def synthesize(self, text: str, destination: Path) -> Path: ...

    async def aclose(self) -> None: ...
