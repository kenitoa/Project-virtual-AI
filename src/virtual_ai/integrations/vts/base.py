"""Operator-approved avatar expression contract."""

from typing import Protocol


class AvatarError(Exception):
    pass


class AvatarClient(Protocol):
    async def set_expression(self, expression: str) -> None: ...

    async def reset(self) -> None: ...

    async def aclose(self) -> None: ...
