from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from typing import Any, Literal, TypedDict

    from .account import PartialAccount
    from .auth import AuthSession
    from .fortnite import SaveTheWorldItem
    from .route import Route

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

    STWItemT_co = TypeVar(
        "STWItemT_co", covariant=True, bound=SaveTheWorldItem
    )

    AuthT = TypeVar("AuthT", bound=AuthSession)
    AccountT = TypeVar("AccountT", bound=PartialAccount)

else:
    AuthT = TypeVar("AuthT", bound="AuthSession")
    AccountT = TypeVar("AccountT", bound="PartialAccount")
