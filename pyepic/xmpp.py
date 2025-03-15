from __future__ import annotations

from asyncio import Event, create_task, sleep, wait_for
from base64 import b64encode, urlsafe_b64encode
from logging import getLogger
from random import getrandbits
from traceback import print_exception
from typing import TYPE_CHECKING
from uuid import uuid4
from xml.etree.ElementTree import XMLPullParser, fromstring

from aiohttp import ClientSession, WSMsgType

from .errors import WSConnectionError, XMPPClosed

if TYPE_CHECKING:
    from asyncio import Task
    from collections.abc import Coroutine, Iterable
    from typing import Any, Self
    from xml.etree.ElementTree import Element

    from aiohttp import ClientWebSocketResponse, WSMessage

    from .auth import AuthSession
    from .http import XMPPConfig

    SendCoro = Coroutine[Any, Any, None]


if __import__("sys").version_info <= (3, 11):
    from asyncio import TimeoutError


__all__ = ("XMLGenerator", "XMLProcessor", "XMPPWebsocketClient")


_logger = getLogger(__name__)


class XMLNamespaces:

    CTX = "jabber:client"
    STREAM = "http://etherx.jabber.org/streams"
    SASL = "urn:ietf:params:xml:ns:xmpp-sasl"
    BIND = "urn:ietf:params:xml:ns:xmpp-bind"
    PING = "urn:xmpp:ping"


class Stanza:
    __slots__ = ("_tag", "_text", "_children", "_attributes", "_id")

    def __init__(
        self,
        *,
        tag: str,
        text: str = "",
        children: Iterable[Stanza] = (),
        **attributes: str,
    ) -> None:
        if text and children:
            raise ValueError("Invalid combination of Stanza arguments passed")
        elif attributes.get("id"):
            _logger.warning(
                "Stanza.__init__ received an 'ID' keyword argument"
            )

        self._tag = tag
        self._text = text
        self._children = tuple(children)
        self._attributes = attributes
        self._id = self._attributes["id"] = self.new_id()

    def __str__(self) -> str:
        attrs_str = ""
        for key, value in self._attributes.items():
            attrs_str += f" {key}='{value}'"
        if self._text:
            return f"<{self._tag}{attrs_str}>{self._text}</{self._tag}>"
        elif self._children:
            return f"<{self._tag}{attrs_str}>{''.join(str(child) for child in self._children)}</{self._tag}>"
        else:
            return f"<{self._tag}{attrs_str}/>"

    @property
    def id(self) -> str:
        return self._id

    @classmethod
    def build(cls, source: Element | str, /) -> Self:
        if isinstance(source, str):
            source = fromstring(source)
        return cls(
            tag=source.tag,
            text=source.text or "",
            children=(cls.build(child) for child in source),
            **source.attrib,
        )

    @staticmethod
    def new_id():
        # Taken straight from aioxmpp
        _id = getrandbits(120)
        _id = _id.to_bytes((_id.bit_length() + 7) // 8, "little")
        _id = urlsafe_b64encode(_id).rstrip(b"=").decode("ascii")
        return ":" + _id


class XMLGenerator:
    __slots__ = ("xmpp",)

    def __init__(self, xmpp: XMPPWebsocketClient, /) -> None:
        self.xmpp: XMPPWebsocketClient = xmpp

    @property
    def uuid(self):
        return uuid4().hex.upper()

    @property
    def xml_prolog(self) -> str:
        return f"<?xml version='{self.xmpp.config.xml_version}'?>"

    @property
    def open(self) -> str:
        return (
            f"<stream:stream xmlns='{XMLNamespaces.CTX}' "
            f"xmlns:stream='{XMLNamespaces.STREAM}' "
            f"to='{self.xmpp.config.host}' "
            f"version='{self.xmpp.config.xmpp_version}'>"
        )

    @property
    def quit(self) -> str:
        return "</stream:stream>"

    @property
    def b64_plain(self) -> str:
        acc_id = self.xmpp.auth_session.account_id
        acc_tk = self.xmpp.auth_session.access_token
        return b64encode(f"\x00{acc_id}\x00{acc_tk}".encode()).decode()

    def auth(self, mechanism: str, /) -> Stanza:
        if mechanism == "PLAIN":
            auth = self.b64_plain
        else:
            # Expected authorization mechanism is PLAIN
            # But implement other mechanisms if needed
            raise NotImplementedError
        return Stanza(
            tag="auth",
            text=auth,
            xmlns=XMLNamespaces.SASL,
            mechanism=mechanism,
        )

    @staticmethod
    def ping() -> Stanza:
        child = Stanza(tag="ping", xmlns=XMLNamespaces.PING)
        return Stanza(tag="iq", type="get", children=(child,))


class XMLProcessor:
    __slots__ = ("xmpp", "generator", "parser", "open_events", "outbound_ids")

    def __init__(self, xmpp: XMPPWebsocketClient, /) -> None:
        self.xmpp: XMPPWebsocketClient = xmpp
        self.generator: XMLGenerator = XMLGenerator(self.xmpp)
        self.parser: XMLPullParser | None = None

        self.restore()

    def setup(self) -> None:
        self.parser = XMLPullParser(("start", "end"))

    def restore(self) -> None:
        self.open_events: list[Element] = []  # noqa
        self.outbound_ids: list[str] = []  # noqa

    @property
    def xml_depth(self) -> int:
        return len(self.open_events)

    def process(self, message: WSMessage, /) -> Stanza | None:
        if self.parser is None:
            raise RuntimeError("XML parser has not been created")

        self.parser.feed(message.data)

        response = None
        event: str
        xml: Element
        for event, xml in self.parser.read_events():
            tag, text = xml.tag, xml.text

            if event == "start":
                self.open_events.append(xml)

            elif event == "end":
                self.open_events.remove(xml)

            if self.xml_depth == 0:
                self.restore()
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

    async def send(
        self, source: Stanza | str, /, *, with_xml_prolog: bool = False
    ) -> None:
        if isinstance(source, Stanza):
            self.processor.outbound_ids.append(source.id)
            source = str(source)
        if with_xml_prolog is True:
            source = self.processor.generator.xml_prolog + source

        await self.ws.send_str(source)
        self.auth_session.action_logger(f"SENT: {source}")

    async def ping_loop(self) -> None:
        while True:
            await sleep(self.config.ping_interval)
            await self.send(self.processor.generator.ping())

    async def recv_loop(self) -> None:
        self.auth_session.action_logger("Websocket receiver running")

        try:
            while True:
                message = await self.ws.receive()

                if message.type == WSMsgType.TEXT:
                    self.auth_session.action_logger(f"RECV: {message.data}")
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
            f"wss://{xmpp.domain}:{xmpp.port}",
            timeout=xmpp.connect_timeout,
            protocols=("xmpp",),
        )
        self.processor.setup()

        self.recv_task = create_task(self.recv_loop())
        self.ping_task = create_task(self.ping_loop())
        self.cleanup_event = Event()

        self.auth_session.action_logger("XMPP started")

        # Let one iteration of the event loop pass
        # Before sending our opening message
        # So the receiver can initialise first
        await sleep(0)
        await self.send(self.processor.generator.open, with_xml_prolog=True)

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
