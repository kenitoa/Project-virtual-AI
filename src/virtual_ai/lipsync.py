"""Latest-only, rate-limited mouth updates; no audio or network work in callbacks."""

import asyncio

from virtual_ai.audio.levels import PlaybackLevels


class MouthSync:
    def __init__(self, settings, send, valid):
        self.settings, self.send, self.valid = settings, send, valid
        self.levels = PlaybackLevels()
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
                await self.send(
                    value, valid=lambda: not self.stopped.is_set() and self.valid()
                )
            try:
                await asyncio.wait_for(
                    self.stopped.wait(), 1 / self.settings.lipsync_hz
                )
            except TimeoutError:
                pass
