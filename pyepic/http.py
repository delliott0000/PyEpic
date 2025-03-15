from __future__ import annotations

from asyncio import create_task, get_running_loop, sleep
from base64 import b64encode
from collections import OrderedDict
from dataclasses import dataclass
from logging import getLogger
from time import time
from typing import TYPE_CHECKING

from aiohttp import ClientResponseError, ClientSession
from aiohttp.helpers import sentinel

from .auth import AuthManager, AuthSession
from .errors import HTTPException
from .route import AccountService, EpicGamesService

if TYPE_CHECKING:
    from asyncio import AbstractEventLoop, Task
    from types import TracebackType
    from typing import Any, Self

    from aiohttp import BaseConnector, ClientResponse, ClientTimeout

    from ._types import URL, AuthT, DCo, JCo, Json, PartialCacheEntry
    from .account import PartialAccount


__all__ = ("HTTPRetryConfig", "CacheConfig", "XMPPConfig", "HTTPClient")


_logger = getLogger(__name__)


@dataclass(kw_only=True, slots=True, frozen=True)
class HTTPRetryConfig:
    max_retries: int = 5
    max_wait_time: float = 65.0

    handle_ratelimits: bool = True
    max_retry_after: float = 60.0

    handle_backoffs: bool = True
    backoff_factor: float = 1.5
    backoff_start: float = 1.0
    backoff_cap: float = 20


@dataclass(kw_only=True, slots=True, frozen=True)
class CacheConfig:
    enable_partial_caching: bool = True
    partial_cache_max_size: int = 1000
    partial_cache_max_age: float = 900.0
    partial_cache_prune_interval: float = 60.0

    enable_full_caching: bool = True
    full_cache_max_age: float = 900.0

    enable_mcp_caching: bool = True


@dataclass(kw_only=True, slots=True, frozen=True)
class XMPPConfig:

    xml_version: str = "1.0"
    xmpp_version: str = "1.0"

    domain: str = "xmpp-service-prod.ol.epicgames.com"
    host: str = "prod.ol.epicgames.com"
    port: int = 443

    platform: str = "WIN"

    connect_timeout: float = 10.0
    ping_interval: float = 60.0
    stop_timeout: float = 1.0


class HTTPClient:
    __slots__ = (
        "__loop",
        "retry_config",
        "__cache_config",
        "__connector",
        "__timeout",
        "__session",
        "client_id",
        "client_secret",
        "__partial_cache",
        "__prune_task",
        "__xmpp_config",
    )

    def __init__(
        self,
        *,
        loop: AbstractEventLoop | None = None,
        retry_config: HTTPRetryConfig | None = None,
        cache_config: CacheConfig | None = None,
        xmpp_config: XMPPConfig | None = None,
        connector: BaseConnector | None = None,
        timeout: ClientTimeout | None = None,
    ) -> None:
        self.__loop: AbstractEventLoop = loop or get_running_loop()
        self.retry_config: HTTPRetryConfig = retry_config or HTTPRetryConfig()
        self.__cache_config: CacheConfig = cache_config or CacheConfig()
        self.__xmpp_config: XMPPConfig = xmpp_config or XMPPConfig()

        self.__connector: BaseConnector | None = connector
        self.__timeout: ClientTimeout | None = timeout

        self.__session: ClientSession | None = None

        self.client_id: str = "ec684b8c687f479fadea3cb2ad83f5c6"
        self.client_secret: str = "e1f31c211f28413186262d37a13fc84d"

        self.__partial_cache: OrderedDict[str, PartialCacheEntry] | None = None
        self.__prune_task: Task | None = None

    async def __aenter__(self) -> Self:
        await self.create_connection()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close_connection()

    @property
    def loop(self) -> AbstractEventLoop:
        return self.__loop

    @property
    def cache_config(self) -> CacheConfig:
        return self.__cache_config

    @property
    def xmpp_config(self) -> XMPPConfig:
        return self.__xmpp_config

    @property
    def connector(self) -> BaseConnector:
        return self.__connector

    @property
    def is_open(self) -> bool:
        return self.__session is not None and not self.__session.closed

    async def create_connection(self) -> None:
        self.__session = ClientSession(
            connector=self.__connector,
            connector_owner=self.__connector is None,
            timeout=self.__timeout or sentinel,
        )
        if self.__cache_config.enable_partial_caching is True:
            self.__setup_cache()

    async def close_connection(self) -> None:
        if self.is_open is True:
            await self.__session.close()
        if self.__cache_config.enable_partial_caching is True:
            self.__destroy_cache()

    @staticmethod
    async def response_to_json(response: ClientResponse, /) -> Json:
        try:
            return await response.json()
        except ClientResponseError:
            # This should only happen if we receive an empty response from Epic Games.
            _logger.debug(
                "Failed to decode response payload from %s %s",
                response.method.upper(),
                response.url,
            )
            return {}

    @staticmethod
    def get_retry_after(error: HTTPException, /) -> float | None:
        retry_after = error.response.headers.get("Retry-After")
        if retry_after is not None:
            return float(retry_after)

        try:
            return float(error.server_vars[0])
        except (IndexError, TypeError, ValueError):
            return

    async def make_request(
        self, method: str, raw_url: str, /, **kwargs: Any
    ) -> Json:
        if self.is_open is False:
            raise RuntimeError("HTTP session is closed.")

        pre_time = time()
        async with self.__session.request(
            method, raw_url, **kwargs
        ) as response:
            _logger.debug(
                "%s %s returned %s %s in %.3fs",
                method.upper(),
                raw_url,
                response.status,
                response.reason,
                time() - pre_time,
            )

            data = await self.response_to_json(response)

            if 200 <= response.status < 400:
                return data

            raise HTTPException(response, data)

    async def request(self, method: str, url: URL, /, **kwargs: Any) -> Json:
        url = str(url)
        config = self.retry_config

        tries = 0
        total_slept = 0
        backoff = config.backoff_start

        while True:
            tries += 1
            sleep_time = 0

            try:
                return await self.make_request(method, url, **kwargs)

            except HTTPException as error:
                if tries >= config.max_retries:
                    raise error

                if (
                    error.server_code
                    == "errors.com.epicgames.common.throttled"
                    or error.response.status == 429
                ):
                    retry_after = self.get_retry_after(error)

                    if retry_after is not None:
                        if (
                            config.handle_ratelimits is True
                            and retry_after <= config.max_retry_after
                        ):
                            sleep_time = retry_after

                    else:
                        backoff *= config.backoff_factor
                        if (
                            config.handle_backoffs is True
                            and backoff <= config.backoff_cap
                        ):
                            sleep_time = backoff

                elif (
                    error.server_code
                    == "errors.com.epicgames.common.server_error"
                    or error.server_code
                    == "errors.com.epicgames.common.concurrent_modification_error"
                    or error.response.status >= 500
                ):
                    sleep_time = 2 * (tries - 1) + 0.5

                if sleep_time > 0:
                    total_slept += sleep_time
                    if total_slept > config.max_wait_time:
                        raise error

                    _logger.debug(
                        "Retrying %s %s in %.3fs...",
                        method.upper(),
                        url,
                        sleep_time,
                    )

                    await sleep(sleep_time)
                    continue

                raise error

    def get(self, url: URL, /, **kwargs: Any) -> JCo:
        return self.request("get", url, **kwargs)

    def put(self, url: URL, /, **kwargs: Any) -> JCo:
        return self.request("put", url, **kwargs)

    def post(self, url: URL, /, **kwargs: Any) -> JCo:
        return self.request("post", url, **kwargs)

    def patch(self, url: URL, /, **kwargs: Any) -> JCo:
        return self.request("patch", url, **kwargs)

    def delete(self, url: URL, /, **kwargs: Any) -> JCo:
        return self.request("delete", url, **kwargs)

    @property
    def user_auth_path(self) -> EpicGamesService:
        return EpicGamesService(
            "/id/api/redirect?clientId={client_id}&responseType=code",
            client_id=self.client_id,
        )

    @property
    def auth_exchange_path(self) -> AccountService:
        return AccountService("/account/api/oauth/token")

    @property
    def auth_exchange_secret(self) -> str:
        return b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

    def create_auth_session(
        self,
        auth_code: str,
        /,
        *,
        cls: type[AuthT] = AuthSession,
        start_xmpp: bool = True,
    ) -> AuthManager[AuthT]:
        if not issubclass(cls, AuthSession):
            raise TypeError("Class should be a subclass of AuthSession")

        request_coro: DCo = self.post(
            self.auth_exchange_path,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"basic {self.auth_exchange_secret}",
            },
            data={"grant_type": "authorization_code", "code": auth_code},
        )

        return AuthManager(self, request_coro, cls=cls, start_xmpp=start_xmpp)

    def renew_auth_session(self, refresh_token: str, /) -> DCo:
        return self.post(
            self.auth_exchange_path,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"basic {self.auth_exchange_secret}",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )

    def __setup_cache(self) -> None:
        self.__partial_cache = OrderedDict()
        self.__prune_task = create_task(self.__prune_cache())

    def __destroy_cache(self) -> None:
        task = self.__prune_task
        if task is not None and task.done() is False:
            task.cancel()
        self.__partial_cache = None

    async def __prune_cache(self) -> None:
        while True:
            await sleep(self.__cache_config.partial_cache_prune_interval)

            expired_accounts = []

            for entry in self.__partial_cache.values():
                account = entry["account"]
                if (
                    entry["expires"] < time()
                    and account not in expired_accounts
                ):
                    expired_accounts.append(account)

            for account in expired_accounts:
                self.uncache_account(account)

    def get_account(self, lookup: str, /) -> PartialAccount | None:
        if self.__cache_config.enable_partial_caching is False:
            return

        cache = self.__partial_cache
        entry = cache.get(lookup)

        if entry is not None:
            account = entry.get("account")
            cache.move_to_end(account.id)
            cache.move_to_end(account.display_name)
            return account

    def cache_account(self, account: PartialAccount, /) -> None:
        if self.__cache_config.enable_partial_caching is False:
            return
        # Reject accounts with incomplete lookup data
        # Avoids accidentally overwriting cached accounts
        elif account.display_name == "" or account.id is None:
            return

        config = self.__cache_config
        cache = self.__partial_cache

        new_entry = {
            "account": account,
            "expires": time() + config.partial_cache_max_age,
        }
        for key in account.id, account.display_name:
            cache[key] = new_entry
            cache.move_to_end(key)

        while len(cache) * 2 > config.partial_cache_max_size:
            for _ in range(2):
                cache.popitem(False)

    def uncache_account(self, account: PartialAccount, /) -> None:
        if self.__cache_config.enable_partial_caching is False:
            return

        cache = self.__partial_cache
        cache.pop(account.id, None)
        cache.pop(account.display_name, None)
