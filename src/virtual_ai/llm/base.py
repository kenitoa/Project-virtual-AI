"""Replaceable text-only LLM contract."""

from typing import Protocol


class LLMError(Exception):
    pass


class LLMClient(Protocol):
    async def generate(self, messages: list[dict]) -> str: ...

    async def aclose(self) -> None: ...
