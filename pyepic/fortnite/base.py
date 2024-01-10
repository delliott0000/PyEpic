from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING, Generic

from pyepic._types import AccountT

if TYPE_CHECKING:
    from typing import Any

    from pyepic._types import Attributes


__all__ = ("AccountBoundMixin", "BaseEntity")


class AccountBoundMixin(ABC, Generic[AccountT]):
    # Defer to subclasses
    __slots__ = ()

    def __init__(
        self, account: AccountT, item_id: str, *args: Any, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)

        self.account: AccountT = account  # noqa
        self.id: str = item_id  # noqa

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: AccountBoundMixin, /) -> bool:
        return isinstance(other, AccountBoundMixin) and self.id == other.id


class BaseEntity(ABC):
    __slots__ = ("template_id", "raw_attributes")

    def __init__(
        self, template_id: str, raw_attributes: Attributes, /
    ) -> None:
        self.template_id: str = template_id
        self.raw_attributes: Attributes = raw_attributes
