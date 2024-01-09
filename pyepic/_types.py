from __future__ import annotations
from typing import TYPE_CHECKING

from typing import TypeVar, Any, Literal, TypedDict
from collections.abc import Coroutine

from .route import Route

if TYPE_CHECKING:
    from .account import PartialAccount
    from .fortnite import SaveTheWorldItem

    STWItemT_co = TypeVar(
        "STWItemT_co", covariant=True, bound=SaveTheWorldItem
    )

    AccountT = TypeVar("AccountT", bound=PartialAccount)
else:
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

Personality = Literal[
    "Competitive",
    "Cooperative",
    "Adventurous",
    "Dependable",
    "Analytical",
    "Pragmatic",
    "Dreamer",
    "Curious",
]


class PartialCacheEntry(TypedDict):
    account: PartialAccount
    expires: float
