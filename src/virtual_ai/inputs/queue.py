"""Bounded expiry/deduplication, with optional fair admission and selection."""

import math
from collections import Counter, OrderedDict, deque
from time import monotonic, time

from virtual_ai.inputs.selection import content_key


class InputQueue:
    def __init__(
        self, size, ttl, clock=monotonic, on_drop=None, policy=None, wall_clock=time
    ):
        self.size, self.ttl, self.clock = size, ttl, clock
        self._items = deque()
        self._seen = OrderedDict()
        self.on_drop = on_drop
        self.policy = policy
        self._content_seen = OrderedDict()
        self.dropped = Counter()
        self.last_rejection = None
        self.wall_clock = wall_clock
        self._deadlines = {}

    def _drop(self, reason):
        _, item = self._items.popleft()
        self._deadlines.pop(id(item), None)
        self.dropped[reason] += 1
        if self.on_drop is not None:
            self.on_drop(item, reason)

    def _expire(self):
        now = self.clock()
        # Network-delayed messages may expire before earlier arrivals.
        for _ in range(len(self._items)):
            if now >= self._deadlines[id(self._items[0][1])]:
                self._drop("expired")
            else:
                self._items.append(self._items.popleft())
        while self._seen and now - next(iter(self._seen.values())) >= self.ttl:
            self._seen.popitem(last=False)
        while (
            self._content_seen
            and now - next(iter(self._content_seen.values())) >= self.ttl
        ):
            self._content_seen.popitem(last=False)

    def _reject(self, reason):
        self.last_rejection = reason
        self.dropped[reason] += 1
        return False

    def put(self, item):
        self._expire()
        self.last_rejection = None
        remaining = self.ttl
        if item.published_at is not None:
            if type(item.published_at) not in (int, float) or not math.isfinite(
                item.published_at
            ):
                return self._reject("invalid_input")
            remaining -= max(0, self.wall_clock() - item.published_at)
            if remaining <= 0:
                return self._reject("expired")
        key = (item.viewer, item.message_id)
        if key in self._seen:
            return self._reject("duplicate")
        fingerprint = None
        if self.policy is not None:
            # Bound retained payload size as well as entry count, even outside Application.
            if (
                not isinstance(item.text, str)
                or not item.text.strip()
                or len(item.text) > 4000
                or not isinstance(item.message_id, str)
                or not 1 <= len(item.message_id) <= 256
                or not 1 <= len(item.viewer.user_id) <= 256
                or not 1 <= len(item.viewer.platform) <= 32
            ):
                return self._reject("invalid_input")
            fingerprint = content_key(item)
            if fingerprint in self._content_seen:
                return self._reject("repeated_content")
            reason = self.policy.rejection(self._items, item)
            if reason:
                return self._reject(reason)
        if len(self._items) >= self.size:
            if self.policy is not None:
                return self._reject("overflow")
            self._drop("overflow")
        now = self.clock()
        self._items.append((now, item))
        self._deadlines[id(item)] = now + remaining
        self._seen[key] = now
        while len(self._seen) > self.size * 2:
            self._seen.popitem(last=False)
        if fingerprint is not None:
            self._content_seen[fingerprint] = now
            while len(self._content_seen) > self.size * 2:
                self._content_seen.popitem(last=False)
        return True

    def pop(self):
        entry = self.pop_timed()
        return entry[1] if entry else None

    def pop_timed(self):
        self._expire()
        if not self._items:
            return None
        index = self.policy.choose(self._items) if self.policy is not None else 0
        entry = self._items[index]
        del self._items[index]
        self._deadlines.pop(id(entry[1]), None)
        if self.policy is not None:
            if self.policy.selected(entry[1]):
                # Rotation never extends the original expiry deadline.
                same_user = []
                for _ in range(len(self._items)):
                    waiting = self._items.popleft()
                    if waiting[1].viewer == entry[1].viewer:
                        same_user.append(waiting)
                    else:
                        self._items.append(waiting)
                self._items.extend(same_user)
        return entry

    def clear(self):
        while self._items:
            self._drop("cleared")
        if self.policy is not None:
            self.policy.reset()

    def coalesce_reactions(self, first, selected_at, window, *, ttl=6):
        """Combine only pure reactions on the same platform in a short time window."""
        from virtual_ai.dialogue import reaction_key

        self._expire()
        key = reaction_key(first.text)
        if not key or first.viewer.platform == "console":
            return 1
        viewers = {first.viewer}
        for _ in range(len(self._items)):
            stamp, item = self._items[0]
            if (
                item.viewer.platform == first.viewer.platform
                and abs(stamp - selected_at) <= window
                and reaction_key(item.text) == key
                and max(
                    self.clock() - stamp,
                    self.wall_clock() - item.published_at
                    if item.published_at is not None
                    else 0,
                )
                < ttl
            ):
                viewers.add(item.viewer)
                self._drop("coalesced_reaction")
            else:
                self._items.append(self._items.popleft())
        return len(viewers)

    def __len__(self):
        self._expire()
        return len(self._items)
