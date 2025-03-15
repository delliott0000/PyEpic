from __future__ import annotations

from asyncio import Event, create_task, sleep, wait_for
from logging import getLogger
from traceback import print_exception
from typing import TYPE_CHECKING
from xml.etree.ElementTree import Element, XMLPullParser

from aiohttp import ClientSession, WSMsgType

from .errors import WSConnectionError, XMPPClosed

if TYPE_CHECKING:
    from asyncio import Task
    from collections.abc import Coroutine
    from typing import Any

    from aiohttp import ClientWebSocketResponse, WSMessage

    from .auth import AuthSession
    from .http import XMPPConfig

    SendCoro = Coroutine[Any, Any, None]


if __import__("sys").version_info <= (3, 11):
    from asyncio import TimeoutError


__all__ = ("XMLGenerator", "XMLProcessor", "XMPPWebsocketClient")


_logger = getLogger(__name__)


class XMLGenerator:
    __slots__ = ("xmpp",)

    def __init__(self, xmpp: XMPPWebsocketClient, /) -> None:
        self.xmpp: XMPPWebsocketClient = xmpp

    @property
    def open(self) -> str:
        # TODO: implement this
        return "..."

    @property
    def ping(self) -> str:
        # TODO: implement this
        return "..."

    @property
    def quit(self) -> str:
        return "</stream:stream>"


class XMLProcessor:
    __slots__ = ("xmpp", "generator", "parser", "xml_depth")

    def __init__(self, xmpp: XMPPWebsocketClient, /) -> None:
        self.xmpp: XMPPWebsocketClient = xmpp

        self.generator: XMLGenerator = XMLGenerator(xmpp)
        self.parser: XMLPullParser | None = None

        self.xml_depth: int = 0

    def init_parser(self) -> None:
        self.parser = XMLPullParser(("start", "end"))

    def process(self, message: WSMessage, /) -> str | None:
        if self.parser is None:
            raise RuntimeError("XML parser has not been created")

        self.parser.feed(message.data)

        response = None
        event: str
        xml: Element
        for event, xml in self.parser.read_events():

            if event == "start":
                self.xml_depth += 1

            elif event == "end":
                self.xml_depth -= 1

            if self.xml_depth == 0:
                self.parser = None
                raise XMPPClosed(message)

        return response


class XMPPWebsocketClient:
    __slots__ = (
        "auth_session",
        "config",
        "session",
        "ws",
        "processor",
        "recv_task",
        "ping_task",
        "cleanup_event",
        "exceptions",
    )

    def __init__(self, auth_session: AuthSession, /) -> None:
        self.auth_session: AuthSession = auth_session
        self.config: XMPPConfig = auth_session.client.xmpp_config

        self.session: ClientSession | None = None
        self.ws: ClientWebSocketResponse | None = None

        self.processor: XMLProcessor = XMLProcessor(self)

        self.recv_task: Task | None = None
        self.ping_task: Task | None = None
        self.cleanup_event: Event | None = None

        self.exceptions: list[Exception] = []

    @property
    def running(self) -> bool:
        return self.ws is not None and not self.ws.closed

    @property
    def most_recent_exception(self) -> Exception | None:
        try:
            return self.exceptions[-1]
        except IndexError:
            return None

    async def send(self, data: str, /) -> None:
        await self.ws.send_str(data)
        self.auth_session.action_logger("SENT: {0}".format(data))

    async def ping_loop(self) -> None:
        while True:
            await sleep(self.config.ping_interval)
            await self.send(self.processor.generator.ping)

    async def recv_loop(self) -> None:
        self.auth_session.action_logger("Websocket receiver running")

        try:
            while True:
                message = await self.ws.receive()

                if message.type == WSMsgType.TEXT:
                    self.auth_session.action_logger(
                        "RECV: {0}".format(message.data)
                    )
                    response = self.processor.process(message)
                    if response is not None:
                        await self.send(response)

                else:
                    raise WSConnectionError(message)

        except Exception as exception:
            if isinstance(exception, XMPPClosed):
                txt = "Websocket received closing message"
                level = _logger.debug
                print_exc = False
            else:
                txt = "Websocket encountered a fatal error"
                level = _logger.error
                print_exc = True

            self.auth_session.action_logger(txt, level=level)
            self.exceptions.append(exception)

            if print_exc is True:
                print_exception(exception)

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
        self.processor.init_parser()

        self.recv_task = create_task(self.recv_loop())
        self.ping_task = create_task(self.ping_loop())
        self.cleanup_event = Event()

        self.auth_session.action_logger("XMPP started")

        # Let one iteration of the event loop pass
        # Before sending our opening message
        # So the receiver can initialise first
        await sleep(0)
        await self.send(self.processor.generator.open)

    async def stop(self) -> None:
        if self.running is False:
            return

        await self.send(self.processor.generator.quit)

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
