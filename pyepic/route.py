from __future__ import annotations

from abc import ABC
from typing import ClassVar
from urllib.parse import quote

__all__ = (
    "Route",
    "EpicGamesService",
    "AccountService",
    "FriendsService",
    "MCPService",
)


class Route(ABC):
    BASE: ClassVar[str] = ""

    __slots__ = ("__path", "__kwargs")

    def __init__(self, path: str, /, **kwargs: str) -> None:
        if self.BASE == "":
            raise ValueError("Route must have a base.")

        self.__path: str = path
        self.__kwargs: dict[str, str] = {
            k: self.__quote__(v) for k, v in kwargs.items()
        }

    def __hash__(self) -> int:
        return hash(self.url)

    def __str__(self) -> str:
        return self.url

    def __eq__(self, other: Route, /) -> bool:
        return isinstance(other, Route) and self.url == other.url

    @staticmethod
    def __quote__(string: str, /) -> str:
        string = quote(string)
        string = string.replace("/", "%2F")
        return string

    @property
    def url(self) -> str:
        return self.BASE + self.__path.format(**self.__kwargs)


class EpicGamesService(Route):
    __slots__ = ()

    BASE = "https://www.epicgames.com"


class AccountService(Route):
    __slots__ = ()

    BASE = "https://account-public-service-prod.ol.epicgames.com"


class FriendsService(Route):
    __slots__ = ()

    BASE = "https://friends-public-service-prod.ol.epicgames.com"


class MCPService(Route):
    __slots__ = ()

    BASE = "https://fngw-mcp-gc-livefn.ol.epicgames.com"
