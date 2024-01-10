from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from logging import getLogger
from typing import TYPE_CHECKING

from .errors import UnknownTemplateID
from .fortnite.base import AccountBoundMixin
from .fortnite.stw import LeadSurvivor, Schematic, Survivor
from .route import FriendsService

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Coroutine
    from typing import Any, Self

    from ._types import Attributes, DCo, Dict, FriendType, List, STWItemT_co
    from .auth import AuthSession
    from .http import HTTPClient


__all__ = ("Friend", "PartialAccount", "FullAccount")


_logger = getLogger(__name__)


@dataclass(kw_only=True, slots=True, frozen=True)
class Friend:
    original: FullAccount
    account: PartialAccount
    type: FriendType

    created: datetime | None
    favorite: bool | None
    mutual: int | None
    alias: str | None
    note: str | None

    def __str__(self) -> str:
        return str(self.account)

    def __eq__(self, other: Friend, /) -> bool:
        return (
            isinstance(other, Friend)
            and self.original == other.original
            and self.account == other.account
            and self.type == other.type
        )


class PartialAccount:
    __slots__ = (
        "client",
        "data",
        "id",
        "display_name",
        "__raw_stw_data",
        "__stw_object_cache",
    )

    def __init__(self, client: HTTPClient, data: Dict, /) -> None:
        self.client: HTTPClient = client
        self.data: Dict = data
        self.id: str = data.get("id")
        self.display_name: str = data.get("displayName", "")

        self.__raw_stw_data: Dict | None = None
        self.__stw_object_cache: dict[
            tuple[str, type[STWItemT_co]], list[STWItemT_co]
        ] | None = None

    def __hash__(self) -> int:
        return hash(self.id)

    def __str__(self) -> str:
        return self.display_name

    def __eq__(self, other: PartialAccount, /) -> bool:
        return isinstance(other, PartialAccount) and self.id == other.id

    async def fetch_raw_stw_data(
        self, auth_session: AuthSession, /, *, use_cache: bool = True
    ) -> Dict:
        if self.__raw_stw_data is not None and use_cache is True:
            return self.__raw_stw_data

        data = await auth_session.mcp_operation(
            epic_id=self.id,
            path="public",
            operation="QueryPublicProfile",
            profile_id="campaign",
        )

        if self.client.cache_config.enable_mcp_caching is True:
            self.__raw_stw_data = data

        return data

    async def fetch_stw_objects(
        self,
        template_id_prefix: str,
        cls: type[STWItemT_co],
        auth_session: AuthSession,
        /,
        *,
        strict: bool = False,
        use_cache: bool = True,
    ) -> list[STWItemT_co]:
        key = template_id_prefix, cls
        cache = self.__stw_object_cache

        if cache is not None and use_cache is True and key in cache:
            return cache[key]

        data = await self.fetch_raw_stw_data(auth_session, use_cache=use_cache)
        items_data: Dict = data["profileChanges"][0]["profile"]["items"]

        items: list[STWItemT_co] = []

        item_data: Dict
        for item_id, item_data in items_data.items():
            template_id: str = item_data["templateId"]

            if not template_id.startswith(template_id_prefix):
                continue

            raw_attributes: Attributes = item_data["attributes"]

            if issubclass(cls, AccountBoundMixin):
                args = self, item_id, template_id, raw_attributes
            else:
                args = template_id, raw_attributes

            try:
                item = cls(*args)

            except UnknownTemplateID as error:
                if strict is True:
                    raise error

                _logger.error(error)
                continue

            items.append(item)

        if self.client.cache_config.enable_mcp_caching is True:
            if cache is None:
                self.__stw_object_cache = {key: items}
            else:
                cache[key] = items

        return items

    def schematics(
        self, auth_session: AuthSession, /, **kwargs: bool
    ) -> Coroutine[Any, Any, list[Schematic[Self]]]:
        return self.fetch_stw_objects(
            "Schematic:sid", Schematic, auth_session, **kwargs
        )

    def survivors(
        self, auth_session: AuthSession, /, **kwargs: bool
    ) -> Coroutine[Any, Any, list[Survivor[Self]]]:
        return self.fetch_stw_objects(
            "Worker:worker", Survivor, auth_session, **kwargs
        )

    def lead_survivors(
        self, auth_session: AuthSession, /, **kwargs: bool
    ) -> Coroutine[Any, Any, list[LeadSurvivor[Self]]]:
        return self.fetch_stw_objects(
            "Worker:manager", LeadSurvivor, auth_session, **kwargs
        )


class FullAccount(PartialAccount):
    __slots__ = (
        "auth_session",
        "display_name_changes",
        "can_update_display_name",
        "first_name",
        "last_name",
        "country",
        "language",
        "email",
        "email_verified",
        "failed_login_attempts",
        "tfa_enabled",
        "last_login",
        "display_name_last_updated",
    )

    def __init__(self, auth_session: AuthSession, data: Dict, /) -> None:
        super().__init__(auth_session.client, data)

        self.auth_session: AuthSession = auth_session

        self.display_name_changes: int = data.get(
            "numberOfDisplayNameChanges", 0
        )
        self.can_update_display_name: bool | None = data.get(
            "canUpdateDisplayName"
        )

        self.first_name: str | None = data.get("name")
        self.last_name: str | None = data.get("lastName")
        self.country: str | None = data.get("country")
        self.language: str | None = data.get("preferredLanguage")

        if isinstance(self.language, str):
            self.language = self.language.capitalize()

        self.email: str | None = data.get("email")
        self.email_verified: bool | None = data.get("emailVerified")

        self.failed_login_attempts: int | None = data.get(
            "failedLoginAttempts"
        )
        self.tfa_enabled: bool | None = data.get("tfaEnabled")

        self.last_login: datetime = datetime.fromisoformat(
            data.get("lastLogin")
        )
        self.display_name_last_updated: datetime = datetime.fromisoformat(
            data.get("lastDisplayNameChange")
        )

    async def friends(
        self, *, friend_type: FriendType = "friends", use_cache: bool = True
    ) -> AsyncGenerator[Friend, None]:
        route = FriendsService(
            "/friends/api/v1/{account_id}/summary", account_id=self.id
        )
        data: Dict = await self.auth_session.access_request("get", route)

        friend_type_data: List = data[friend_type]
        friend_type_data.sort(key=lambda entry: entry["accountId"])

        account_ids = tuple(entry["accountId"] for entry in friend_type_data)
        accounts = [
            account
            async for account in self.auth_session.fetch_accounts(
                *account_ids, use_cache=use_cache
            )
        ]
        # Fetching accounts doesn't necessarily preserve the order that IDs are passed in
        accounts.sort(key=lambda account: account.id)

        for i in range(len(friend_type_data)):
            entry = friend_type_data[i]

            try:
                created = datetime.fromisoformat(entry.get("created"))
            except (ValueError, TypeError):
                created = None

            yield Friend(
                original=self,
                account=accounts[i],
                type=friend_type,
                created=created,
                favorite=entry.get("favorite"),
                mutual=entry.get("mutual"),
                alias=entry.get("alias") or None,
                note=entry.get("note") or None,
            )

    def friend(self, account: PartialAccount, /) -> DCo:
        route = FriendsService(
            "/friends/api/v1/{account_id}/friends/{friend_id}",
            account_id=self.id,
            friend_id=account.id,
        )
        return self.auth_session.access_request("post", route)

    def unfriend(self, account: PartialAccount, /) -> DCo:
        route = FriendsService(
            "/friends/api/v1/{account_id}/friends/{friend_id}",
            account_id=self.id,
            friend_id=account.id,
        )
        return self.auth_session.access_request("delete", route)

    def block(self, account: PartialAccount, /) -> DCo:
        route = FriendsService(
            "/friends/api/v1/{account_id}/blocklist/{friend_id}",
            account_id=self.id,
            friend_id=account.id,
        )
        return self.auth_session.access_request("post", route)

    def unblock(self, account: PartialAccount, /) -> DCo:
        route = FriendsService(
            "/friends/api/v1/{account_id}/blocklist/{friend_id}",
            account_id=self.id,
            friend_id=account.id,
        )
        return self.auth_session.access_request("delete", route)
