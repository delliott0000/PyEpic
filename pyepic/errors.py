from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import ClientResponse, WSMessage

    from ._types import Json
    from .fortnite import AccountBoundMixin, BaseEntity, Recyclable, Upgradable


__all__ = (
    "EpicException",
    "HTTPException",
    "WSException",
    "WSConnectionError",
    "XMPPClosed",
    "FortniteException",
    "UnknownTemplateID",
    "BadItemAttributes",
    "ItemIsReadOnly",
    "ItemIsFavorited",
    "InvalidUpgrade",
)


class EpicException(Exception):
    pass


class HTTPException(EpicException):
    def __init__(self, response: ClientResponse, data: Json, /) -> None:
        self.response: ClientResponse = response
        self.data: Json = data.copy()

        _error_data = data if isinstance(data, dict) else {}
        self.server_code: str = _error_data.get(
            "errorCode", "unknown_error_code"
        )
        self.server_message: str = _error_data.get(
            "errorMessage", "An error occurred."
        )
        self.server_vars: list[str] = _error_data.get("messageVars", []).copy()

        self.originating_service: str | None = _error_data.get(
            "originatingService"
        )
        self.intent: str | None = _error_data.get("intent")

    def __str__(self) -> str:
        return f"{self.response.status} {self.response.reason} - {self.server_message}"


# TODO: Implement special methods on WS/XMPP Exception classes


class WSException(EpicException):

    def __init__(self, message: WSMessage, /) -> None:
        self.message: WSMessage = message


class WSConnectionError(WSException):
    pass


class XMPPClosed(WSException):
    pass


class FortniteException(EpicException):
    pass


class UnknownTemplateID(FortniteException):

    def __init__(self, item: BaseEntity, /) -> None:
        self.item: BaseEntity = item

    def __str__(self) -> str:
        return f"Unknown template ID: {self.item.template_id}"


class BadItemAttributes(FortniteException):

    def __init__(self, item: BaseEntity, /) -> None:
        self.item: BaseEntity = item

    def __str__(self) -> str:
        return f"Malformed/invalid item attributes: {self.item.raw_attributes}"


class ItemIsReadOnly(FortniteException):

    def __init__(self, item: AccountBoundMixin, /) -> None:
        self.item: AccountBoundMixin = item

    def __str__(self) -> str:
        return "Item can not be modified as it is not tied to a FullAccount"


class ItemIsFavorited(FortniteException):

    def __init__(self, item: Recyclable, /) -> None:
        self.item: Recyclable = item

    def __str__(self) -> str:
        return "Item is favorited so it can not be recycled"


class InvalidUpgrade(FortniteException):

    def __init__(self, item: Upgradable, /) -> None:
        self.item: Upgradable = item

    def __str__(self) -> str:
        return "An invalid target level/tier was specified"
