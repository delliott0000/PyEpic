from __future__ import annotations

from asyncio import Event, create_task, sleep, wait_for
from base64 import b64encode, urlsafe_b64encode
from logging import getLogger
from random import getrandbits
from traceback import print_exception
from typing import TYPE_CHECKING
from uuid import uuid4
from xml.etree.ElementTree import XMLPullParser

from aiohttp import ClientSession, WSMsgType

from .errors import WSConnectionError, XMPPClosed, XMPPException

if TYPE_CHECKING:
    from asyncio import Task
    from collections.abc import Iterable
    from typing import Any
    from xml.etree.ElementTree import Element

    from aiohttp import ClientWebSocketResponse, WSMessage

    from .auth import AuthSession
    from .http import XMPPConfig


if __import__("sys").version_info <= (3, 11):
    from asyncio import TimeoutError


__all__ = (
    "XMLNamespaces",
    "Stanza",
    "XMLGenerator",
    "XMLProcessor",
    "XMPPWebsocketClient",
)


_logger = getLogger(__name__)


def make_stanza_id() -> str:
    # Full credit: aioxmpp
    _id = getrandbits(120)
    _id = _id.to_bytes((_id.bit_length() + 7) // 8, "little")
    _id = urlsafe_b64encode(_id).rstrip(b"=").decode("ascii")
    return ":" + _id


def make_resource(platform: str, /) -> str:
    return f"V2:Fortnite:{platform}::{uuid4().hex.upper()}"


def match(xml: Element, ns: str, tag: str, /) -> bool:
    return xml.tag == f"{{{ns}}}{tag}"


class XMLNamespaces:

    SESSION = "urn:ietf:params:xml:ns:xmpp-session"
    CLIENT = "jabber:client"
    STREAM = "http://etherx.jabber.org/streams"
    SASL = "urn:ietf:params:xml:ns:xmpp-sasl"
    BIND = "urn:ietf:params:xml:ns:xmpp-bind"
    PING = "urn:xmpp:ping"


class Stanza:
    __slots__ = ("name", "text", "children", "attributes")

    def __init__(
        self,
        *,
        name: str,
        text: str = "",
        children: Iterable[Stanza] = (),
        make_id: bool = True,
        **attributes: str,
    ) -> None:
        children = tuple(children)
        if text and children:
            raise ValueError("Invalid combination of Stanza arguments passed")

        self.name: str = name
        self.text: str = text
        self.children: tuple[Stanza, ...] = children
        self.attributes: dict[str, str] = attributes
        if make_id:
            self.attributes["id"] = make_stanza_id()

    def __str__(self) -> str:
        attrs_str = ""
        for key, value in self.attributes.items():
            key = key.strip("_")
            attrs_str += f" {key}='{value}'"
        if self.text:
            return f"<{self.name}{attrs_str}>{self.text}</{self.name}>"
        elif self.children:
            return f"<{self.name}{attrs_str}>{''.join(str(child) for child in self.children)}</{self.name}>"
        else:
            return f"<{self.name}{attrs_str}/>"

    def __eq__(self, other: Stanza | str, /) -> bool:
        return str(self) == str(other)

    @property
    def id(self) -> str | None:
        return self.attributes.get("id")


class XMLGenerator:
    __slots__ = ("xmpp",)

    def __init__(self, xmpp: XMPPWebsocketClient, /) -> None:
        self.xmpp: XMPPWebsocketClient = xmpp

    @property
    def xml_prolog(self) -> str:
        return f"<?xml version='{self.xmpp.config.xml_version}'?>"

    @property
    def open(self) -> str:
        return (
            f"<stream:stream xmlns='{XMLNamespaces.CLIENT}' "
            f"xmlns:stream='{XMLNamespaces.STREAM}' "
            f"to='{self.xmpp.config.host}' "
            f"version='{self.xmpp.config.xmpp_version}'>"
        )

    @property
    def close(self) -> str:
        return "</stream:stream>"

    @property
    def b64_plain(self) -> str:
        acc_id = self.xmpp.auth_session.account_id
        acc_tk = self.xmpp.auth_session.access_token
        return b64encode(f"\x00{acc_id}\x00{acc_tk}".encode()).decode()

    def make_iq(self, **kwargs: Any) -> Stanza:
        return Stanza(
            name="iq", to=self.xmpp.config.host, from_=self.xmpp.jid, **kwargs
        )

    def make_message(self, *, to: str, **kwargs: Any) -> Stanza:
        return Stanza(name="message", to=to, from_=self.xmpp.jid, **kwargs)

    def make_presence(self, **kwargs: Any) -> Stanza:
        return Stanza(name="presence", from_=self.xmpp.jid, **kwargs)

    def auth(self, mechanism: str, /) -> Stanza:
        if mechanism == "PLAIN":
            auth = self.b64_plain
        else:
            # Expected authorization mechanism is PLAIN
            # But implement other mechanisms here if needed
            raise NotImplementedError
        return Stanza(
            name="auth",
            text=auth,
            make_id=False,
            xmlns=XMLNamespaces.SASL,
            mechanism=mechanism,
        )

    def bind(self, resource: str, /) -> Stanza:
        child2 = Stanza(name="resource", text=resource, make_id=False)
        child1 = Stanza(
            name="bind",
            xmlns=XMLNamespaces.BIND,
            children=(child2,),
            make_id=False,
        )
        return self.make_iq(type="set", children=(child1,))

    def ping(self) -> Stanza:
        child = Stanza(name="ping", xmlns=XMLNamespaces.PING, make_id=False)
        return self.make_iq(type="get", children=(child,))

    def session(self) -> Stanza:
        child = Stanza(
            name="session", xmlns=XMLNamespaces.SESSION, make_id=False
        )
        return self.make_iq(type="set", children=(child,))


# noinspection PyAttributeOutsideInit
class XMLProcessor:
    __slots__ = (
        "xmpp",
        "generator",
        "parser",
        "outbound_ids",
        "xml_depth",
    )

    def __init__(self, xmpp: XMPPWebsocketClient, /) -> None:
        self.xmpp: XMPPWebsocketClient = xmpp
        self.generator: XMLGenerator = XMLGenerator(xmpp)
        self.reset()

    def setup(self) -> None:
        self.parser = XMLPullParser(("start", "end"))

    def reset(self) -> None:
        self.parser: XMLPullParser | None = None
        self.outbound_ids: list[str] = []
        self.xml_depth: int = 0

    async def process(self, message: WSMessage, /) -> None:
        if self.parser is None:
            raise RuntimeError("XML parser doesn't exist")

        self.parser.feed(message.data)
        for event, xml in self.parser.read_events():

            if event == "start":
                self.xml_depth += 1

            elif event == "end":
                self.xml_depth -= 1

                if self.xml_depth == 0:
                    raise XMPPClosed(xml, "Stream closed")

                elif self.xml_depth == 1:
                    await self.handler(xml)

    async def handler(self, xml: Element, /) -> None:
        xml_id = xml.attrib.get("id")
        if xml_id:
            if xml_id in self.outbound_ids:
                self.outbound_ids.remove(xml_id)
            else:
                self.xmpp.auth_session.action_logger(
                    f"Unknown message: {xml_id}", level=_logger.warning
                )

        if self.xmpp.negotiated:
            handled = False
        else:
            handled = await self.negotiate(xml)

        if handled is False:
            # TODO: handle events (messages, presences and so on)
            ...

    # TODO: can we improve the way we inspect the xml data?
    async def negotiate(self, xml: Element, /) -> bool:
        xmpp = self.xmpp
        generator = self.generator
        action_logger = xmpp.auth_session.action_logger

        if match(xml, XMLNamespaces.STREAM, "features"):

            for sub_xml_1 in xml:
                if match(sub_xml_1, XMLNamespaces.SASL, "mechanisms"):

                    for sub_xml_2 in sub_xml_1:
                        if match(sub_xml_2, XMLNamespaces.SASL, "mechanism"):

                            mechanism = sub_xml_2.text
                            action_logger(
                                f"Attempting {mechanism} authentication.."
                            )
                            auth = generator.auth(mechanism)
                            await xmpp.send(auth)
                            return True

                    else:
                        raise XMPPException(
                            xml,
                            "Could not determine stream authentication method",
                        )

                elif match(sub_xml_1, XMLNamespaces.BIND, "bind"):

                    resource = make_resource(xmpp.config.platform)
                    await xmpp.send(generator.bind(resource))
                    return True

        elif match(xml, XMLNamespaces.SASL, "success"):

            xmpp.authenticated = True
            action_logger("Authenticated")
            self.reset()
            self.setup()
            await xmpp.send(self.generator.open)
            return True

        elif match(xml, XMLNamespaces.CLIENT, "iq"):

            for sub_xml_1 in xml:
                if match(sub_xml_1, XMLNamespaces.BIND, "bind"):

                    for sub_xml_2 in sub_xml_1:
                        if match(sub_xml_2, XMLNamespaces.BIND, "jid"):

                            jid = sub_xml_2.text
                            resource = jid.split("/")[1]
                            xmpp.resource = resource
                            action_logger(f"Bound to JID {jid}")
                            return True

                    else:
                        raise XMPPException(xml, "Unable to bind JID")

        return False


class XMPPWebsocketClient:
    __slots__ = (
        "auth_session",
        "config",
        "session",
        "ws",
        "processor",
        "recv_task",
        "ping_task",
        "negotiated_event",
        "cleanup_event",
        "exceptions",
        "_resource",
        "_authenticated",
    )

    def __init__(self, auth_session: AuthSession, /) -> None:
        self.auth_session: AuthSession = auth_session
        self.config: XMPPConfig = auth_session.client.xmpp_config

        self.session: ClientSession | None = None
        self.ws: ClientWebSocketResponse | None = None

        self.processor: XMLProcessor = XMLProcessor(self)

        self.recv_task: Task | None = None
        self.ping_task: Task | None = None
        self.negotiated_event: Event | None = None
        self.cleanup_event: Event | None = None

        self.exceptions: list[Exception] = []

        self._resource: str | None = None
        self._authenticated: bool = False

    @property
    def jid(self) -> str:
        if self.resource:
            return f"{self.auth_session.account_id}@{self.config.host}/{self.resource}"
        else:
            return f"{self.auth_session.account_id}@{self.config.host}"

    @property
    def bound(self) -> bool:
        return bool(self.resource)

    @property
    def running(self) -> bool:
        return self.ws is not None and not self.ws.closed

    @property
    def negotiated(self) -> bool:
        return self.bound and self.authenticated

    @property
    def resource(self) -> str | None:
        return self._resource

    @resource.setter
    def resource(self, value: str | None, /) -> None:
        self._resource = value
        if self.negotiated:
            self.negotiated_event.set()

    @property
    def authenticated(self) -> bool:
        return self._authenticated

    @authenticated.setter
    def authenticated(self, value: bool, /) -> None:
        self._authenticated = value
        if self.negotiated:
            self.negotiated_event.set()

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
            if source.id is not None:
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
                    await self.processor.process(message)

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

        client = self.auth_session.client
        config = self.config

        self.session = ClientSession(
            connector=client.connector,
            connector_owner=client.connector is None,
        )
        self.ws = await self.session.ws_connect(
            f"wss://{config.domain}:{config.port}",
            timeout=config.connect_timeout,
            protocols=("xmpp",),
        )
        self.processor.setup()

        self.recv_task = create_task(self.recv_loop())
        self.ping_task = create_task(self.ping_loop())
        self.negotiated_event = Event()
        self.cleanup_event = Event()

        self.auth_session.action_logger("XMPP started")

        # Let one iteration of the event loop pass
        # Before sending our opening message
        # So the receiver can initialise first
        await sleep(0)
        await self.send(self.processor.generator.open)
        create_task(self.setup())  # noqa

    async def stop(self) -> None:
        if self.running is False:
            return

        await self.send(self.processor.generator.close)

        try:
            await wait_for(self.wait_for_cleanup(), self.config.stop_timeout)
        except TimeoutError:
            await self.cleanup()

    async def setup(self) -> None:
        try:
            await wait_for(
                self.wait_for_negotiated(), self.config.connect_timeout
            )
            await self.send(self.processor.generator.session())

        except (Exception, TimeoutError) as exception:
            self.auth_session.action_logger(
                "Setup failed - aborting..", level=_logger.error
            )
            print_exception(exception)
            await self.cleanup()

    async def wait_for_negotiated(self) -> None:
        try:
            await self.negotiated_event.wait()
        except AttributeError:
            raise RuntimeError("XMPP client is not running!")

    async def wait_for_cleanup(self) -> None:
        if self.cleanup_event is None:
            return
        await self.cleanup_event.wait()

    async def cleanup(self) -> None:
        self.recv_task.cancel()
        self.ping_task.cancel()
        self.negotiated_event.set()
        self.cleanup_event.set()

        await self.ws.close()
        await self.session.close()

        self.session = None
        self.ws = None
        self.processor.reset()

        self.recv_task = None
        self.ping_task = None
        self.negotiated_event = None
        self.cleanup_event = None

        self.resource = None
        self.authenticated = False

        self.auth_session.action_logger("XMPP stopped")
