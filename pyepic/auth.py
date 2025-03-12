from __future__ import annotations

from datetime import datetime
from logging import getLogger
from time import time
from typing import TYPE_CHECKING, Generic

from ._types import AuthT
from .account import FullAccount, PartialAccount
from .errors import HTTPException
from .route import AccountService, MCPService
from .utils import utc_now
from .xmpp import XMPPWebsocketClient

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Generator
    from types import TracebackType
    from typing import Any, Self

    from ._types import DCo, Dict, JCo, Json, List
    from .http import HTTPClient
    from .route import Route


__all__ = ("AuthManager", "AuthSession")


_logger = getLogger(__name__)


class AuthManager(Generic[AuthT]):
    __slots__ = (
        "__client",
        "__request_coro",
        "__cls",
        "__start_xmpp",
        "__auth_session",
    )

    def __init__(
        self,
        client: HTTPClient,
        request_coro: DCo,
        /,
        *,
        cls: type[AuthT],
        start_xmpp: bool,
    ) -> None:
        self.__client: HTTPClient = client
        self.__request_coro: DCo = request_coro
        self.__cls: type[AuthT] = cls
        self.__start_xmpp: bool = start_xmpp
        self.__auth_session: AuthT | None = None

    def __await__(self) -> Generator[Any, None, AuthT]:
        return self.__construct__().__await__()

    async def __aenter__(self) -> AuthT:
        return await self.__construct__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.__auth_session.kill(stop_xmpp=self.__start_xmpp)

    async def __construct__(self) -> AuthT:
        data = await self.__request_coro
        self.__auth_session: AuthT = self.__cls(self.__client, data)
        if self.__start_xmpp is True:
            await self.__auth_session.xmpp.start()
        return self.__auth_session


class AuthSession:
    __slots__ = (
        "data",
        "client",
        "account_id",
        "access_token",
        "refresh_token",
        "access_expires",
        "refresh_expires",
        "_killed",
        "__cached_account",
        "__cached_account_expires",
        "xmpp",
    )

    def __init__(self, client: HTTPClient, data: Dict, /) -> None:
        self.client: HTTPClient = client

        self._renew_data(data)
        self.action_logger("initialised")

        self._killed = False

        self.__cached_account: FullAccount[Self] | None = None
        self.__cached_account_expires: float | None = None

        self.xmpp: XMPPWebsocketClient = XMPPWebsocketClient(self)

    def _renew_data(self, data: Dict, /) -> None:
        self.data: Dict = data
        self.account_id: str = data.get("account_id")
        self.access_token: str = data.get("access_token")
        self.refresh_token: str = data.get("refresh_token")
        self.access_expires: datetime = datetime.fromisoformat(
            data.get("expires_at")
        )
        self.refresh_expires: datetime = datetime.fromisoformat(
            data.get("refresh_expires_at")
        )

    def action_logger(
        self, action: str, /, *, level: Callable[..., None] = _logger.debug
    ) -> None:
        level(
            "Auth session %s %s. (Account ID: %s)",
            self.access_token,
            action,
            self.account_id,
        )

    @property
    def active(self) -> bool:
        return not self._killed and self.access_expires > utc_now()

    @property
    def expired(self) -> bool:
        return self._killed or self.refresh_expires < utc_now()

    @property
    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"bearer {self.access_token}"}

    async def renew(self, *, force: bool = False) -> Dict | None:
        if self.active is True and force is False:
            return

        data = await self.client.renew_auth_session(self.refresh_token)

        self._renew_data(data)
        self.action_logger("renewed")

        return data

    async def access_request(
        self, method: str, route: Route, /, **kwargs: Any
    ) -> Json:
        headers = kwargs.pop("headers", None) or self.auth_headers

        try:
            return await self.client.request(
                method, route, headers=headers, **kwargs
            )
        except HTTPException as error:
            if error.response.status != 401 or self.expired is True:
                raise error

            await self.renew()
            return await self.client.request(
                method, route, headers=headers, **kwargs
            )

    async def kill(
        self, *, stop_xmpp: bool = True, force: bool = False
    ) -> None:
        if stop_xmpp is True:
            await self.xmpp.stop()

        if self.expired is True and force is False:
            return

        route = AccountService(
            "/account/api/oauth/sessions/kill/{access_token}",
            access_token=self.access_token,
        )
        try:
            await self.access_request("delete", route)
        except HTTPException:
            pass

        self._killed = True
        self.action_logger("killed")

    async def fetch_account(
        self,
        *,
        account_id: str | None = None,
        display_name: str | None = None,
        use_cache: bool = True,
    ) -> PartialAccount:
        lookup = account_id or display_name

        if lookup is None:
            raise ValueError("An account ID or display name is required")

        elif use_cache is True:
            account = self.client.get_account(lookup)
            if account is not None:
                return account

        if account_id is not None:
            route = AccountService(
                "/account/api/public/account/{account_id}",
                account_id=account_id,
            )
        else:
            route = AccountService(
                "/account/api/public/account/displayName/{display}",
                display=display_name,
            )

        data: Dict = await self.access_request("get", route)

        account = PartialAccount(self.client, data)
        self.client.cache_account(account)

        return account

    async def fetch_accounts(
        self, *account_ids: str, use_cache: bool = True
    ) -> AsyncGenerator[PartialAccount, None]:
        account_ids = tuple(set(account_ids))

        if use_cache is True:
            needs_fetching = []

            for account_id in account_ids:
                account = self.client.get_account(account_id)

                if account is not None:
                    yield account
                else:
                    needs_fetching.append(account_id)

        else:
            needs_fetching = account_ids

        if needs_fetching:
            chunks = [
                needs_fetching[i : i + 100]
                for i in range(0, len(needs_fetching), 100)
            ]
            route = AccountService("/account/api/public/account")

            for chunk in chunks:
                data: List = await self.access_request(
                    "get",
                    route,
                    params=[("accountId", account_id) for account_id in chunk],
                )

                for entry in data:
                    account = PartialAccount(self.client, entry)
                    self.client.cache_account(account)

                    yield account

    def __cache_self(self, account: FullAccount, /) -> None:
        self.__cached_account = account
        self.__cached_account_expires = (
            time() + self.client.cache_config.full_cache_max_age
        )

    async def __fetch_self(self) -> FullAccount[Self]:
        route = AccountService(
            "/account/api/public/account/{account_id}",
            account_id=self.account_id,
        )
        data: Dict = await self.access_request("get", route)
        return FullAccount(self, data)

    async def account(self, *, use_cache: bool = True) -> FullAccount[Self]:
        if (
            use_cache is True
            and self.__cached_account is not None
            and self.__cached_account_expires > time()
        ):
            return self.__cached_account

        account = await self.__fetch_self()
        if self.client.cache_config.enable_full_caching is True:
            self.__cache_self(account)
        return account

    def mcp_operation(
        self,
        *,
        method: str = "post",
        epic_id: str | None = None,
        path: str = "client",
        operation: str = "QueryProfile",
        profile_id: str = "athena",
        json: Dict | None = None,
    ) -> JCo:
        epic_id = epic_id or self.account_id
        json = json or {}

        route = MCPService(
            "/fortnite/api/game/v2/profile/{account_id}/{path}/{operation}?profileId={profile_id}",
            account_id=epic_id,
            path=path,
            operation=operation,
            profile_id=profile_id,
        )

        return self.access_request(method, route, json=json)
