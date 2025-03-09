from __future__ import annotations

from asyncio import sleep
from logging import getLogger
from typing import TYPE_CHECKING

from aiohttp import ClientSession

if TYPE_CHECKING:
    from asyncio import Task

    from aiohttp import ClientWebSocketResponse

    from .auth import AuthSession
    from .http import XMPPConfig


__all__ = ("XMPPWebsocketClient",)


_logger = getLogger(__name__)


class XMPPWebsocketClient:
    __slots__ = (
        "auth_session",
        "config",
        "session",
        "ws",
        "main_task",
        "recv_task",
        "ping_task",
        "errors",
    )

    def __init__(self, auth_session: AuthSession, /) -> None:
        self.auth_session: AuthSession = auth_session
        self.config: XMPPConfig = auth_session.client.xmpp_config

        self.session: ClientSession | None = None
        self.ws: ClientWebSocketResponse | None = None

        self.main_task: Task | None = None
        self.recv_task: Task | None = None
        self.ping_task: Task | None = None

        self.errors: list[Exception] = []

    @property
    def running(self) -> bool:
        return self.ws is not None and not self.ws.closed

    @property
    def latest_error(self) -> Exception | None:
        try:
            return self.errors[-1]
        except IndexError:
            return None

    async def ping(self) -> None: ...

    async def send(self, data: str, /) -> None:
        ...

        self.auth_session.action_logger("SENT: {0}".format(data))

    async def ping_loop(self) -> None:
        while True:
            await sleep(self.config.ping_interval)
            await self.ping()

    async def recv_loop(self) -> None:
        self.auth_session.action_logger("Websocket receiver running")

        try:
            while True:
                message = await self.ws.receive()
                data = message.data

                self.auth_session.action_logger("RECV: {0}".format(data))

                ...

        except Exception as error:  # noqa
            self.errors.append(error)
            self.auth_session.action_logger(
                "XMPP encountered a fatal error", level=_logger.error
            )

            ...

        finally:
            self.auth_session.action_logger("Websocket receiver stopped")

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
