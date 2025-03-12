from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from logging import getLogger
from typing import TYPE_CHECKING, Generic

from pyepic.resources import lookup

from ._types import AuthT
from .errors import UnknownTemplateID
from .fortnite.base import AccountBoundMixin
from .fortnite.stw import LeadSurvivor, Schematic, Survivor, SurvivorSquad
from .route import FriendsService

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Coroutine
    from typing import Any, Self

    from ._types import Attributes, DCo, Dict, FriendType, List, STWItemT_co
    from .auth import AuthSession
    from .fortnite.stw import SaveTheWorldItem
    from .http import HTTPClient


__all__ = ("Friend", "PartialAccount", "FullAccount")


_logger = getLogger(__name__)


@dataclass(kw_only=True, slots=True, frozen=True)
class Friend(Generic[AuthT]):
    original: FullAccount[AuthT]
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
        self.__stw_object_cache: (
            dict[tuple[str, type[STWItemT_co]], list[STWItemT_co]] | None
        ) = None

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

    def uncache_stw_object(self, obj: SaveTheWorldItem, /) -> None:
        cls = type(obj)
        cache = self.__stw_object_cache

        if cache is None:
            return

        for key in cache:
            if key[1] is cls:
                try:
                    cache[key].remove(obj)
                except ValueError:
                    continue

    def schematics(
        self, auth_session: AuthSession, /, **kwargs: Any
    ) -> Coroutine[Any, Any, list[Schematic[Self]]]:
        return self.fetch_stw_objects(
            "Schematic:sid", Schematic, auth_session, **kwargs
        )

    def survivors(
        self, auth_session: AuthSession, /, **kwargs: Any
    ) -> Coroutine[Any, Any, list[Survivor[Self]]]:
        return self.fetch_stw_objects(
            "Worker:worker", Survivor, auth_session, **kwargs
        )

    def lead_survivors(
        self, auth_session: AuthSession, /, **kwargs: Any
    ) -> Coroutine[Any, Any, list[LeadSurvivor[Self]]]:
        return self.fetch_stw_objects(
            "Worker:manager", LeadSurvivor, auth_session, **kwargs
        )

    async def survivor_squads(
        self, auth_session: AuthSession, /, **kwargs: Any
    ) -> list[SurvivorSquad[Self]]:
        s1 = await self.survivors(auth_session, **kwargs)
        s2 = await self.lead_survivors(auth_session, **kwargs)
        survivors = s1 + s2

        mapping: dict = {
            squad_id: {"lead": None, "survivors": []}
            for squad_id in lookup["SquadDetails"]
        }

        for survivor in survivors:
            squad_id = survivor.squad_id
            if squad_id is None:
                continue

            elif isinstance(survivor, Survivor):
                mapping[squad_id]["survivors"].append(survivor)
            elif isinstance(survivor, LeadSurvivor):
                mapping[squad_id]["lead"] = survivor

        squads = []

        for squad_id, squad_composition in mapping.items():
            squad = SurvivorSquad(
                self,
                squad_id,
                lead_survivor=squad_composition["lead"],
                survivors=squad_composition["survivors"],
            )
            squads.append(squad)

        return squads

    async def survivor_squad_from_id(
        self, value: str, auth_session: AuthSession, /, **kwargs: Any
    ) -> SurvivorSquad[Self]:
        squads = await self.survivor_squads(auth_session, **kwargs)

        try:
            squad = next(
                squad
                for squad in squads
                if (squad.id == value or squad.name == value)
            )
        except StopIteration:
            raise ValueError("An invalid squad ID/name was passed.")

        return squad


class FullAccount(Generic[AuthT], PartialAccount):
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

    def __init__(self, auth_session: AuthT, data: Dict, /) -> None:
        super().__init__(auth_session.client, data)

        self.auth_session: AuthT = auth_session

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
    ) -> AsyncGenerator[Friend[AuthT], None]:
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

    def __friend(self, friend_id: str, op_type: str, op: str, /) -> DCo:
        route = FriendsService(
            "/friends/api/v1/{account_id}/{op_type}/{friend_id}",
            account_id=self.id,
            op_type=op_type,
            friend_id=friend_id,
        )
        return self.auth_session.access_request(op, route)

    def friend(self, account: PartialAccount, /) -> DCo:
        return self.__friend(account.id, "friends", "post")

    def unfriend(self, account: PartialAccount, /) -> DCo:
        return self.__friend(account.id, "friends", "delete")

    def block(self, account: PartialAccount, /) -> DCo:
        return self.__friend(account.id, "blocklist", "post")

    def unblock(self, account: PartialAccount, /) -> DCo:
        return self.__friend(account.id, "blocklist", "delete")
