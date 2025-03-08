from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .auth import AuthSession


class XMPPWebsocketClient:
    __slots__ = ("auth_session",)

    def __init__(self, auth_session: AuthSession, /) -> None:
        self.auth_session: AuthSession = auth_session

    async def start(self) -> None: ...

    async def stop(self) -> None: ...
