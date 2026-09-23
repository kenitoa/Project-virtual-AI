"""Deterministic bounded chat selection; no user-provided priority or AI scoring."""

import hashlib
import unicodedata
from dataclasses import dataclass, field


def content_key(item):
    text = unicodedata.normalize("NFKC", item.text).casefold()
    normalized = " ".join(
        "".join(c for c in text if unicodedata.category(c) != "Cf").split()
    )
    return item.viewer, hashlib.sha256(normalized.encode("utf-8")).digest()


@dataclass
class SelectionPolicy:
    per_user: int = 2
    max_consecutive: int = 1
    _last_viewer: object = field(default=None, init=False, repr=False)
    _streak: int = field(default=0, init=False, repr=False)

    def __post_init__(self):
        if type(self.per_user) is not int or not 1 <= self.per_user <= 1000:
            raise ValueError("per_user must be an integer in [1, 1000]")
        if (
            type(self.max_consecutive) is not int
            or not 1 <= self.max_consecutive <= 1000
        ):
            raise ValueError("max_consecutive must be an integer in [1, 1000]")

    def rejection(self, items, item):
        if sum(queued.viewer == item.viewer for _, queued in items) >= self.per_user:
            return "user_limit"
        return None

    def choose(self, items):
        # Oldest waiting user's turn first; served users rotate behind other waiters.
        # A lone viewer can continue; the streak limit applies when someone else waits.
        if self._streak >= self.max_consecutive:
            for index, (_, item) in enumerate(items):
                if item.viewer != self._last_viewer:
                    return index
        return 0

    def selected(self, item):
        self._streak = (
            min(self._streak + 1, self.max_consecutive)
            if item.viewer == self._last_viewer
            else 1
        )
        self._last_viewer = item.viewer
        return self._streak >= self.max_consecutive

    def reset(self):
        self._last_viewer = None
        self._streak = 0
