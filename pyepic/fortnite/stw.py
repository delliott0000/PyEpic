from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic

from pyepic._types import AccountT
from pyepic.errors import (
    BadItemAttributes,
    InvalidUpgrade,
    ItemIsFavorited,
    ItemIsReadOnly,
    UnknownTemplateID,
)
from pyepic.resources import lookup

from .base import AccountBoundMixin, BaseEntity

if TYPE_CHECKING:
    from typing import ClassVar

    from pyepic._types import Attributes, DCo, Dict
    from pyepic.auth import AuthSession


__all__ = (
    "SaveTheWorldItem",
    "Recyclable",
    "Upgradable",
    "Schematic",
    "SchematicPerk",
    "SurvivorBase",
    "Survivor",
    "LeadSurvivor",
)


class SaveTheWorldItem(BaseEntity):
    __slots__ = ("name", "type", "tier", "level", "rarity", "favorite")

    def __init__(
        self, template_id: str, raw_attributes: Attributes, /
    ) -> None:
        super().__init__(template_id, raw_attributes)

        items: dict[str, dict[str, str]] = lookup["Items"]

        for variation in (
            template_id,
            template_id[:-2] + "01",
            template_id.replace("Trap:tid", "Schematic:sid")[:-2] + "01",
            template_id.replace("Weapon:wid", "Schematic:sid")[:-2] + "01",
        ):
            if variation in items:
                lookup_id = variation
                break
        else:
            raise UnknownTemplateID(self)

        entry = items[lookup_id]

        self.name: str = entry["name"]
        self.type: str = lookup["ItemTypes"][entry["type"]]
        self.level: int = raw_attributes.get("level", 1)
        self.rarity: str = entry["rarity"].capitalize()
        self.favorite: bool = raw_attributes.get("favorite", False)

        tier = template_id[-1]
        try:
            self.tier = int(tier)
        except ValueError:
            self.tier = 1

    def __str__(self) -> str:
        return self.name


class Recyclable(
    Generic[AccountT], AccountBoundMixin[AccountT], SaveTheWorldItem
):
    __slots__ = ("account", "id")

    @property
    def _auth_checker(self) -> AuthSession:
        try:
            return self.account.auth_session
        except AttributeError:
            raise ItemIsReadOnly(self)

    def recycle(self, *, strict: bool = True) -> DCo:
        if self.favorite is True and strict is True:
            raise ItemIsFavorited(self)

        return self._auth_checker.mcp_operation(
            operation="RecycleItem",
            profile_id="campaign",
            json={"targetItemId": self.id},
        )


class Upgradable(Generic[AccountT], Recyclable[AccountT]):
    __slots__ = ()

    __tier_mapping__: ClassVar = {
        1: "i",
        2: "ii",
        3: "iii",
        4: "iv",
        5: "v",
    }

    async def upgrade(
        self, *, new_level: int, new_tier: int, conversion_index: int
    ) -> Dict:
        if new_tier not in range(self.tier, 6) or new_level not in range(
            self.level + 1, 61
        ):
            raise InvalidUpgrade(self)

        data = await self._auth_checker.mcp_operation(
            operation="UpgradeItemBulk",
            profile_id="campaign",
            json={
                "targetItemId": self.id,
                "desiredLevel": new_level,
                "desiredTier": self.__tier_mapping__[new_tier],
                "conversionRecipeIndexChoice": conversion_index,
            },
        )

        self.level, self.tier = new_level, new_tier

        if (
            isinstance(self, Schematic)
            and self.tier > 3
            and conversion_index == 1
        ):
            self.template_id = self.template_id.replace("_ore_", "_crystal_")

        return data


class Schematic(Generic[AccountT], Upgradable[AccountT]):
    __slots__ = ("perks",)

    def __init__(
        self,
        account: AccountT,
        item_id: str,
        template_id: str,
        raw_attributes: Attributes,
        /,
    ) -> None:
        try:
            super().__init__(account, item_id, template_id, raw_attributes)
        except UnknownTemplateID:
            super().__init__(
                account,
                item_id,
                template_id.replace("_crystal_", "_ore_"),
                raw_attributes,
            )
            self.template_id = template_id

        self.perks: tuple[SchematicPerk[AccountT], ...] = tuple(
            SchematicPerk(self, perk_id)
            for perk_id in raw_attributes.get("alterations", ())
        )

    @property
    def power_level(self) -> int:
        return lookup["ItemPowerLevels"]["Other"][self.rarity][str(self.tier)][
            str(self.level)
        ]


class SchematicPerk(Generic[AccountT]):
    __slots__ = ("schematic", "id", "rarity", "description")

    def __init__(
        self, schematic: Schematic[AccountT], perk_id: str, /
    ) -> None:
        self.schematic: Schematic[AccountT] = schematic
        self.id: str = perk_id

        try:
            self.rarity: str = (
                "Common",
                "Uncommon",
                "Rare",
                "Epic",
                "Legendary",
            )[int(perk_id[-1]) - 1]
        except (IndexError, ValueError):
            self.rarity: str = "Common"

        # TODO: implement perk description
        self.description: str

    # TODO: implement dunders here


@dataclass(kw_only=True, slots=True, frozen=True)
class SetBonusType:
    type: str
    name: str
    bonus: int
    fort_type: str | None
    requirement: int


class SurvivorBase(Generic[AccountT], Upgradable[AccountT]):
    __slots__ = ("personality", "squad_id", "squad_index")

    def __init__(
        self,
        account: AccountT,
        item_id: str,
        template_id: str,
        raw_attributes: Attributes,
        /,
    ) -> None:
        super().__init__(account, item_id, template_id, raw_attributes)

        try:
            self.personality: str = raw_attributes["personality"].split(".")[
                -1
            ][2:]
            _index = raw_attributes["squad_slot_idx"]
        except KeyError:
            raise BadItemAttributes(self)

        self.squad_id: str | None = raw_attributes.get("squad_id")
        self.squad_index: int | None = _index if _index != -1 else None


class Survivor(Generic[AccountT], SurvivorBase[AccountT]):
    __slots__ = ("set_bonus_type",)

    def __init__(
        self,
        account: AccountT,
        item_id: str,
        template_id: str,
        raw_attributes: Attributes,
        /,
    ) -> None:
        super().__init__(account, item_id, template_id, raw_attributes)

        try:
            _set_bonus_type: str = (
                raw_attributes["set_bonus"]
                .split(".")[-1][2:]
                .replace("Low", "")
                .replace("High", "")
            )
            _set_bonus_data: dict[str, str | int] = lookup["SetBonuses"][
                _set_bonus_type
            ]
        except KeyError:
            raise BadItemAttributes(self)

        self.set_bonus_type: SetBonusType = SetBonusType(
            type=_set_bonus_type, **_set_bonus_data
        )

    @property
    def base_power_level(self) -> int:
        return lookup["ItemPowerLevels"]["Survivor"][self.rarity][
            str(self.tier)
        ][str(self.level)]


class LeadSurvivor(Generic[AccountT], SurvivorBase[AccountT]):
    __slots__ = ("preferred_squad_id",)

    def __init__(
        self,
        account: AccountT,
        item_id: str,
        template_id: str,
        raw_attributes: Attributes,
        /,
    ) -> None:
        super().__init__(account, item_id, template_id, raw_attributes)

        try:
            self.preferred_squad_id: str = lookup["LeadPreferredSquads"][
                raw_attributes["managerSynergy"]
            ]
        except KeyError:
            raise BadItemAttributes(self)

    @property
    def base_power_level(self) -> int:
        return lookup["ItemPowerLevels"]["LeadSurvivor"][self.rarity][
            str(self.tier)
        ][str(self.level)]
