from __future__ import annotations

from asyncio import create_task, sleep
from logging import getLogger
from traceback import print_exc
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
        "recv_task",
        "ping_task",
        "errors",
    )

    def __init__(self, auth_session: AuthSession, /) -> None:
        self.auth_session: AuthSession = auth_session
        self.config: XMPPConfig = auth_session.client.xmpp_config

        self.session: ClientSession | None = None
        self.ws: ClientWebSocketResponse | None = None

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
        await self.ws.send_str(data)
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

                # TODO: Process message here

        # This won't catch asyncio.CancelledError
        except Exception as error:  # noqa
            self.errors.append(error)
            self.auth_session.action_logger(
                "XMPP encountered a fatal error", level=_logger.error
            )
            print_exc()

            create_task(self.cleanup(_on_error=True))  # noqa

        finally:
            self.auth_session.action_logger("Websocket receiver stopped")

    async def start(self) -> None:
        if self.running is True:
            return

        http = self.auth_session.client
        xmpp = self.config

        self.session = ClientSession(
            connector=http.connector, connector_owner=http.connector is None
        )
        self.ws = await self.session.ws_connect(
            "wss://{0}:{1}".format(xmpp.domain, xmpp.port),
            timeout=xmpp.connect_timeout,
            protocols=("xmpp",),
        )

        self.recv_task = create_task(self.recv_loop())
        self.ping_task = create_task(self.ping_loop())

        self.auth_session.action_logger("XMPP started")

        # TODO: Send initial XML stream here

    async def stop(self) -> None:
        if self.running is False:
            return
        await self.cleanup()

    async def cleanup(self, *, _on_error: bool = False) -> None:
        # Allow this task to exit naturally
        # If we are cleaning up after a fatal error
        if _on_error is False:
            self.recv_task.cancel()

        self.ping_task.cancel()

        await self.ws.close()
        await self.session.close()

        self.session = None
        self.ws = None

        self.recv_task = None
        self.ping_task = None

        self.auth_session.action_logger("XMPP stopped")
