"""Operator-owned state; never populated from viewer or model text."""

from dataclasses import dataclass


@dataclass
class RuntimeState:
    microphone_active: bool = False
    paused: bool = False
    panic: bool = False
    muted: bool = False
    cleanup_failed: bool = False
    controls_pending: int = 0
    voice_epoch: int = 0
    phase: str = "idle"
    accept_after: float = 0.0

    @property
    def input_locked(self):
        return (
            self.paused or self.panic or self.cleanup_failed or self.microphone_active
        )
