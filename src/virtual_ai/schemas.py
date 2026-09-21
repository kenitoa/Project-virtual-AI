"""External input is data, never an operator capability."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Viewer:
    platform: str
    user_id: str

    def __post_init__(self):
        if not self.platform.strip() or not self.user_id.strip():
            raise ValueError("platform and user_id are required")


@dataclass(frozen=True)
class ChatInput:
    viewer: Viewer
    text: str
    message_id: str


@dataclass(frozen=True)
class Response:
    response_id: str
    raw: str
    final: str
    speech: str
    blocked: bool = False
    expression: str = "neutral"
