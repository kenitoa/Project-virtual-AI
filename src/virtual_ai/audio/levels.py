"""Bounded PCM RMS and a single latest-value mailbox for playback callbacks."""

import math
import threading
import time


def pcm_rms(pcm, width):
    """Sample at most 2048 interleaved integer samples, preserving both channels."""
    pcm = memoryview(pcm).cast("B")  # PortAudio's CFFI buffer indexes bytes, not ints.
    count = len(pcm) // width
    if not count:
        return 0.0
    stride = max(1, math.ceil(count / 2048))
    if stride % 2 == 0:
        stride += 1  # Do not sample only one channel in large stereo buffers.
    total = samples = 0
    scale = 128 if width == 1 else 2 ** (8 * width - 1)
    for index in range(0, count, stride):
        offset = index * width
        value = (
            pcm[offset] - 128
            if width == 1
            else int.from_bytes(pcm[offset : offset + width], "little", signed=True)
        )
        total += (value / scale) ** 2
        samples += 1
    return min(1.0, math.sqrt(total / samples))


class PlaybackLevels:
    def __init__(self):
        self._lock = threading.Lock()
        self._latest = None
        self._closed = False

    def publish(self, rms, duration):
        # No event-loop callbacks or per-buffer queue; old measurements are replaced.
        with self._lock:
            if not self._closed:
                self._latest = (rms, time.monotonic() + duration + 0.1)

    def latest(self):
        with self._lock:
            if self._closed or self._latest is None:
                return None
            rms, deadline = self._latest
            return rms if time.monotonic() <= deadline else 0.0

    def close(self):
        with self._lock:
            self._closed = True
            self._latest = None
