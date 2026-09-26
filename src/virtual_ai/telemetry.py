"""Response timings contain generated IDs and fixed labels only."""

import logging
from time import monotonic

logger = logging.getLogger(__name__)


class ResponseTrace:
    def __init__(self, response_id, received, observer=None):
        self.response_id, self.received = response_id, received
        self.observer = observer

    def mark(self, stage, *, status="ok", at=None):
        stamp = monotonic() if at is None else at
        if self.observer:
            self.observer(
                self.response_id, stage, status, max(0, stamp - self.received)
            )
        logger.info(
            "response_id=%s stage=%s status=%s elapsed_seconds=%.6f monotonic_seconds=%.6f",
            self.response_id,
            stage,
            status,
            max(0, stamp - self.received),
            stamp,
        )
