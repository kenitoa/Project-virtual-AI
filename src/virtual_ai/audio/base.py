"""Playback contract; implementations must abort hardware on cancellation."""

from pathlib import Path
from typing import Protocol


class AudioError(Exception):
    """Invalid audio, unavailable device or playback/cleanup failure."""


class AudioPlayer(Protocol):
    async def play(self, path: Path) -> bool:
        """Return True after draining, False after stop; reject overlapping play."""
        ...

    async def stop(self) -> None:
        """Discard queued device audio and wait for the device to close."""
        ...

    async def aclose(self) -> None:
        """Stop playback, release resources and reject future playback."""
        ...
