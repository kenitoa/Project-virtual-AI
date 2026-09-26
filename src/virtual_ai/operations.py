"""Bounded, content-free runtime evidence. Callback observation is not listening."""

from collections import Counter, OrderedDict, deque
from time import monotonic


class Observations:
    def __init__(self, capacity=256, *, clock=monotonic):
        self.capacity, self.clock = capacity, clock
        self.active = OrderedDict()
        self.completed = deque(maxlen=capacity)
        self.outcomes = Counter()
        self.last_input_at = None
        self.started = clock()

    def observe(self, identifier, stage, status, elapsed):
        if stage == "received":
            self.last_input_at = self.clock()
        row = self.active.setdefault(identifier, {})
        row[stage] = (status, elapsed)
        while len(self.active) > self.capacity:
            self.active.popitem(last=False)
            self.outcomes["observation_evicted"] += 1
        # Selected requests finish in cleanup, including late/blocked responses.
        if stage == "discard" and "selected" not in row:
            self._finish(identifier, "discarded:" + status)
        elif stage == "cleanup_complete":
            outcome = status
            if "discard" in row:
                outcome = "discarded:" + row["discard"][0]
            elif row.get("dialogue_selection", ("",))[0].startswith("skip_"):
                outcome = row["dialogue_selection"][0]
            elif "voice_failed" in row:
                outcome = "voice_failed"
            elif row.get("llm_complete", ("ok",))[0] not in ("ok", "skipped"):
                outcome = "llm:" + row["llm_complete"][0]
            elif "playback_finished" in row:
                outcome = "audio:" + row["playback_finished"][0]
            elif row.get("output_checked", (None,))[0] == "blocked":
                outcome = "blocked"
            elif "console_displayed" in row:
                outcome = "text_only"
            elif status == "settled":
                outcome = "no_delivery"
            self._finish(identifier, outcome)

    def _finish(self, identifier, outcome):
        row = self.active.pop(identifier)
        self.outcomes[outcome] += 1
        self.completed.append((outcome, row))

    def snapshot(self):
        from virtual_ai.performance import stats

        pairs = {
            "queue_seconds": ("received", "selected"),
            "retrieval_seconds": ("retrieval_started", "retrieval_complete"),
            "llm_seconds": ("llm_started", "llm_complete"),
            "tts_seconds": ("tts_started", "tts_complete"),
            "validation_seconds": ("llm_complete", "output_checked"),
            "input_to_first_callback_seconds": ("received", "first_playback_callback"),
            "selected_to_first_callback_seconds": (
                "selected",
                "first_playback_callback",
            ),
            "total_seconds": ("received", "cleanup_complete"),
        }
        samples = {key: [] for key in pairs}
        for _, row in self.completed:
            for key, (start, end) in pairs.items():
                if start in row and end in row and row[end][0] != "unobserved":
                    samples[key].append(max(0, row[end][1] - row[start][1]))
        return {
            "uptime_seconds": max(0, self.clock() - self.started),
            "last_input_age_seconds": None
            if self.last_input_at is None
            else max(0, self.clock() - self.last_input_at),
            "outcomes_since_start": dict(self.outcomes),
            "recent_sample_count": len(self.completed),
            "sample_capacity": self.capacity,
            "all_observed_outcomes": {
                key: stats(values) for key, values in samples.items()
            },
            "active_count": len(self.active),
            "listening_verified": False,
        }
