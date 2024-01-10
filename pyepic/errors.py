from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import ClientResponse

    from ._types import Json
    from .fortnite import BaseEntity


__all__ = (
    "EpicException",
    "HTTPException",
    "FortniteException",
    "FortniteItemException",
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


class FortniteException(EpicException):
    pass


class FortniteItemException(FortniteException):
    def __init__(self, item: BaseEntity, /) -> None:
        self.item: BaseEntity = item


class UnknownTemplateID(FortniteItemException):
    def __str__(self) -> str:
        return f"Unknown template ID: {self.item.template_id}"


class BadItemAttributes(FortniteItemException):
    def __str__(self) -> str:
        return f"Malformed/invalid item attributes: {self.item.raw_attributes}"


class ItemIsReadOnly(FortniteItemException):
    def __str__(self) -> str:
        return "Item can not be modified as it is not tied to a FullAccount"


class ItemIsFavorited(FortniteItemException):
    def __str__(self) -> str:
        return "Item is favorited so it can not be recycled."


class InvalidUpgrade(FortniteItemException):
    def __str__(self) -> str:
        return "An invalid target level/tier was specified"
