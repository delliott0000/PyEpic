from __future__ import annotations

from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any, Literal, TypedDict, TypeVar

from .route import Route

if TYPE_CHECKING:
    from .account import PartialAccount
    from .auth import AuthSession
    from .fortnite import SaveTheWorldItem

    STWItemT_co = TypeVar(
        "STWItemT_co", covariant=True, bound=SaveTheWorldItem
    )

    AuthT = TypeVar("AuthT", bound=AuthSession)
    AccountT = TypeVar("AccountT", bound=PartialAccount)
else:
    AuthT = TypeVar("AuthT", bound="AuthSession")
    AccountT = TypeVar("AccountT", bound="PartialAccount")


URL = Route | str

Dict = dict[str, Any]
List = list[Dict]
Json = Dict | List

DCo = Coroutine[Any, Any, Dict]
JCo = Coroutine[Any, Any, Json]

Attributes = dict[str, Any]

FriendType = Literal[
    "friends", "incoming", "outgoing", "suggested", "blocklist"
]


class PartialCacheEntry(TypedDict):
    account: PartialAccount
    expires: float
