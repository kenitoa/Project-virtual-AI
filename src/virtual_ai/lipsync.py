"""Latest-only, rate-limited mouth updates; no audio or network work in callbacks."""

import asyncio
import time

from virtual_ai.audio.levels import PlaybackLevels


class MouthSync:
    def __init__(self, settings, send, valid):
        self.settings, self.send, self.valid = settings, send, valid
        self.levels = PlaybackLevels()
        self.first_send_at = None
        self.stopped = asyncio.Event()
        self.task = asyncio.create_task(self.run())

    def stop(self):
        self.levels.close()
        self.stopped.set()

    async def run(self):
        value = 0.0
        while not self.stopped.is_set() and self.valid():
            level = self.levels.latest()
            if level is not None:
                target = min(1.0, max(0.0, level * self.settings.lipsync_gain))
                value += self.settings.lipsync_smoothing * (target - value)
                if target == 0:
                    value = 0.0
                if self.first_send_at is None:
                    self.first_send_at = time.monotonic()
                await self.send(
                    value, valid=lambda: not self.stopped.is_set() and self.valid()
                )
            # Windows 3.11's event-loop clock may wake timers one tick early.
            # Recheck a high-resolution deadline instead of sending another frame.
            deadline = time.perf_counter() + 1 / self.settings.lipsync_hz
            while not self.stopped.is_set():
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    break
                delay = max(remaining, time.get_clock_info("monotonic").resolution)
                try:
                    await asyncio.wait_for(self.stopped.wait(), delay)
                except TimeoutError:
                    pass
