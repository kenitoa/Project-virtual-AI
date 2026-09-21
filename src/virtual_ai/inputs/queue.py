"""Drop expired input; on overflow drop oldest; bounded duplicate cache."""

from collections import OrderedDict, deque
from time import monotonic


class InputQueue:
    def __init__(self, size, ttl, clock=monotonic):
        self.size, self.ttl, self.clock = size, ttl, clock
        self._items = deque()
        self._seen = OrderedDict()

    def _expire(self):
        now = self.clock()
        while self._items and now - self._items[0][0] >= self.ttl:
            self._items.popleft()
        while self._seen and now - next(iter(self._seen.values())) >= self.ttl:
            self._seen.popitem(last=False)

    def put(self, item):
        self._expire()
        key = (item.viewer, item.message_id)
        if key in self._seen:
            return False
        if len(self._items) >= self.size:
            self._items.popleft()
        now = self.clock()
        self._items.append((now, item))
        self._seen[key] = now
        while len(self._seen) > self.size * 2:
            self._seen.popitem(last=False)
        return True

    def pop(self):
        entry = self.pop_timed()
        return entry[1] if entry else None

    def pop_timed(self):
        self._expire()
        return self._items.popleft() if self._items else None

    def clear(self):
        self._items.clear()

    def __len__(self):
        self._expire()
        return len(self._items)
