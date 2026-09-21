"""Bounded in-memory original turns only; no summaries or persistence."""

from collections import OrderedDict
from time import monotonic


class RecentHistory:
    def __init__(self, settings, clock=monotonic):
        self.settings, self.clock = settings, clock
        self._viewers = OrderedDict()

    def _expire(self):
        now = self.clock()
        for viewer, (timestamp, _) in list(self._viewers.items()):
            if now - timestamp >= self.settings.history_ttl_seconds:
                del self._viewers[viewer]

    def messages(self, viewer):
        self._expire()
        entry = self._viewers.get(viewer)
        if entry is None:
            return []
        return [dict(m) for pair in entry[1] for m in pair]

    def add(self, viewer, user, assistant):
        self._expire()
        _, turns = self._viewers.pop(viewer, (0, []))
        turns.append(
            (
                {"role": "user", "content": user},
                {"role": "assistant", "content": assistant},
            )
        )
        while turns and (
            len(turns) > self.settings.history_turns
            or sum(len(m["content"]) for pair in turns for m in pair)
            > self.settings.history_chars
        ):
            turns.pop(0)
        if turns:
            self._viewers[viewer] = (self.clock(), turns)
        while len(self._viewers) > self.settings.max_viewers:
            self._viewers.popitem(last=False)

    def delete(self, viewer=None):
        if viewer is None:
            self._viewers.clear()
        else:
            self._viewers.pop(viewer, None)
