from __future__ import annotations

from asyncio import Event, create_task, sleep, wait_for
from logging import getLogger
from traceback import print_exception
from typing import TYPE_CHECKING

from aiohttp import ClientSession

from .errors import XMPPClosed, XMPPConnectionError

if TYPE_CHECKING:
    from asyncio import Task

    from aiohttp import ClientWebSocketResponse, WSMsgType

    from .auth import AuthSession
    from .http import XMPPConfig


if __import__("sys").version_info <= (3, 11):
    from asyncio import TimeoutError


__all__ = ("XMLGenerator", "XMLProcessor", "XMPPWebsocketClient")


_logger = getLogger(__name__)


class XMLGenerator: ...


class XMLProcessor: ...


class XMPPWebsocketClient:
    __slots__ = (
        "auth_session",
        "config",
        "session",
        "ws",
        "recv_task",
        "ping_task",
        "cleanup_event",
        "errors",
    )

    def __init__(self, auth_session: AuthSession, /) -> None:
        self.auth_session: AuthSession = auth_session
        self.config: XMPPConfig = auth_session.client.xmpp_config

        self.session: ClientSession | None = None
        self.ws: ClientWebSocketResponse | None = None

        self.recv_task: Task | None = None
        self.ping_task: Task | None = None
        self.cleanup_event: Event | None = None

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

    async def ping(self) -> None:
        # TODO: implement this
        ...

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

                if message.type == WSMsgType.TEXT:
                    # TODO: Process message here
                    ...

                elif message.type == WSMsgType.CLOSED:
                    raise XMPPClosed(message)

                elif message.type == WSMsgType.ERROR:
                    raise XMPPConnectionError(message)

        except Exception as error:
            if isinstance(error, XMPPClosed):
                self.auth_session.action_logger(
                    "Websocket received closing message"
                )
            else:
                self.auth_session.action_logger(
                    "XMPP encountered a fatal error", level=_logger.error
                )
                self.errors.append(error)
                print_exception(error)

            create_task(self.cleanup())  # noqa

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
        self.cleanup_event = Event()

        self.auth_session.action_logger("XMPP started")

        # TODO: Send initial XML stream here

    async def stop(self) -> None:
        if self.running is False:
            return

        # TODO: Send </stream:stream> here

        try:
            await wait_for(self.wait_for_cleanup(), self.config.stop_timeout)
        except TimeoutError:
            await self.cleanup()

    async def wait_for_cleanup(self) -> None:
        if self.cleanup_event is None:
            return
        await self.cleanup_event.wait()

    async def cleanup(self) -> None:
        self.recv_task.cancel()
        self.ping_task.cancel()
        self.cleanup_event.set()

        await self.ws.close()
        await self.session.close()

        self.session = None
        self.ws = None

        self.recv_task = None
        self.ping_task = None
        self.cleanup_event = None

        self.auth_session.action_logger("XMPP stopped")
