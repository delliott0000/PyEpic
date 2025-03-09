from __future__ import annotations

from logging import getLogger
from typing import TYPE_CHECKING

from aiohttp import ClientSession, ClientWebSocketResponse

if TYPE_CHECKING:
    from .auth import AuthSession
    from .http import XMPPConfig


__all__ = ("XMPPWebsocketClient",)


_logger = getLogger(__name__)


class XMPPWebsocketClient:
    __slots__ = ("auth_session", "config", "session", "ws", "processor")

    def __init__(self, auth_session: AuthSession, /) -> None:
        self.auth_session: AuthSession = auth_session
        self.config: XMPPConfig = auth_session.client.xmpp_config

        self.session: ClientSession | None = None
        self.ws: ClientWebSocketResponse | None = None

    @property
    def running(self) -> bool:
        return self.ws is not None and not self.ws.closed

    async def start(self) -> None:
        if self.running is True:
            ...

        ...

        self.auth_session.action_logger("XMPP started")

    async def stop(self) -> None:
        if self.running is False:
            ...

        ...

        self.auth_session.action_logger("XMPP stopped")
