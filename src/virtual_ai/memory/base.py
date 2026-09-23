"""Explicit local-only long-term storage contracts, separate from RecentHistory."""

from typing import Protocol


class StoreError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__("Memory store: " + reason)


class MemoryStore(Protocol):
    def allow(self, user_id: str) -> bool: ...
    def forget(self, user_id: str) -> bool: ...
    def lookup(self, user_id: str) -> dict: ...
    def backup(self, path: str) -> bool: ...
