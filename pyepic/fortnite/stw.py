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
    from collections.abc import Generator, Iterable
    from typing import Any, ClassVar

    from pyepic._types import Attributes, Dict
    from pyepic.auth import AuthSession


__all__ = (
    "SaveTheWorldItem",
    "Recyclable",
    "Upgradable",
    "Schematic",
    "SchematicPerk",
    "SetBonusType",
    "SurvivorBase",
    "Survivor",
    "LeadSurvivor",
    "ActiveSetBonus",
    "FortStat",
    "SurvivorSquad",
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

    async def recycle(self, *, strict: bool = True) -> Dict:
        if self.favorite is True and strict is True:
            raise ItemIsFavorited(self)

        data = await self._auth_checker.mcp_operation(
            operation="RecycleItem",
            profile_id="campaign",
            json={"targetItemId": self.id},
        )

        self.account.uncache_stw_object(self)

        return data


class Upgradable(Generic[AccountT], Recyclable[AccountT]):
    __slots__ = ()

    __tier_mapping__: ClassVar[dict[int, str]] = {
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

        self.template_id = self.template_id.replace(
            f"_t0{self.tier}", f"_t0{new_tier}"
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


# TODO: find a way to implement perk description
# TODO: implement special methods
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

        self.description: str = ...


@dataclass(kw_only=True, slots=True, frozen=True)
class SetBonusType:
    id: str
    name: str
    bonus: int
    requirement: int

    def __str__(self) -> str:
        return self.name

    def __eq__(self, other: SetBonusType, /) -> bool:
        return type(other) is type(self) and self.id == other.id


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

        self.squad_id: str | None = raw_attributes.get("squad_id") or None
        self.squad_index: int | None = _index if _index != -1 else None

    async def squad(
        self,
        auth_session: AuthSession | None = None,
        /,
        *,
        raise_read_only: bool = False,
        **kwargs: Any,
    ) -> SurvivorSquad[AccountT] | None:
        if self.squad_id is None:
            return

        try:
            return await self.account.survivor_squad_from_id(
                self.squad_id, auth_session or self._auth_checker, **kwargs
            )
        except ItemIsReadOnly as error:
            if raise_read_only is True:
                raise error
            raise ValueError("An authorization session needs to be passed")

    async def recycle(self, *, strict: bool = True) -> Dict:
        data = await super().recycle(strict=strict)

        squad = await self.squad(raise_read_only=True)
        squad.unslot_local(self)

        return data


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
            id=_set_bonus_type, **_set_bonus_data
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


@dataclass(kw_only=True, slots=True, frozen=True)
class ActiveSetBonus(Generic[AccountT]):

    squad: SurvivorSquad[AccountT]
    set_bonus_type: SetBonusType

    def __str__(self) -> str:
        return str(self.set_bonus_type)

    def __eq__(self, other: ActiveSetBonus, /) -> bool:
        return (
            type(other) is type(self)
            and self.squad == other.squad
            and self.set_bonus_type == other.set_bonus_type
        )


@dataclass(kw_only=True, slots=True, frozen=True)
class FortStat:
    tech: int
    offense: int
    fortitude: int
    resistance: int

    def __add__(self, other: FortStat, /) -> FortStat:
        return FortStat(
            tech=self.tech + other.tech,
            offense=self.offense + other.offense,
            fortitude=self.fortitude + other.fortitude,
            resistance=self.resistance + other.resistance,
        )


class SurvivorSquad(Generic[AccountT], AccountBoundMixin[AccountT]):
    __slots__ = ("account", "id", "name", "lead_survivor", "survivors")

    def __init__(
        self,
        account: AccountT,
        item_id: str,
        /,
        *,
        lead_survivor: LeadSurvivor | None,
        survivors: Iterable[Survivor],
    ) -> None:
        super().__init__(account, item_id)

        self.name: str = lookup["SquadDetails"][self.id]["name"]
        self.lead_survivor: LeadSurvivor[AccountT] | None = lead_survivor

        slots = [None] * 7
        for survivor in survivors:
            slots[survivor.squad_index - 1] = survivor  # noqa

        self.survivors: tuple[Survivor[AccountT] | None, ...] = tuple(slots)

    def __str__(self) -> str:
        return self.name

    def __iter__(
        self,
    ) -> Generator[LeadSurvivor[AccountT] | Survivor[AccountT], None, None]:
        if self.lead_survivor is not None:
            yield self.lead_survivor
        yield from filter(
            lambda survivor: survivor is not None, self.survivors
        )

    def __contains__(self, survivor: SurvivorBase, /) -> bool:
        return survivor == self.lead_survivor or survivor in self.survivors

    @property
    def active_set_bonuses(self) -> list[ActiveSetBonus[AccountT]]:
        tally: dict[SetBonusType, int] = {}

        for survivor in self.survivors:
            if survivor is None:
                continue
            try:
                tally[survivor.set_bonus_type] += 1
            except KeyError:
                tally[survivor.set_bonus_type] = 1

        active_set_bonuses = []

        for set_bonus_type, count in tally.items():

            sets = count // set_bonus_type.requirement
            for _ in range(sets):
                active_set_bonus = ActiveSetBonus(
                    squad=self, set_bonus_type=set_bonus_type
                )
                active_set_bonuses.append(active_set_bonus)

        return active_set_bonuses

    @property
    def total_fort_stats(self) -> FortStat:
        fort_stats_data = dict(tech=0, offense=0, fortitude=0, resistance=0)

        count = 0

        lead = self.lead_survivor
        if lead is not None:
            pl = lead.base_power_level
            if lead.preferred_squad_id == self.id:
                count += pl * 2
            else:
                count += pl

        for survivor in self.survivors:
            if survivor is None:
                continue
            pl = survivor.base_power_level
            increments = lookup["LeadBonuses"]

            if lead is not None and lead.personality == survivor.personality:
                pl += increments[lead.rarity][0]
            elif lead is not None:
                pl += increments[lead.rarity][1]

            count += pl

        fort_type = lookup["SquadDetails"][self.id]["fort"]
        fort_stats_data[fort_type] += count

        return FortStat(**fort_stats_data)

    def slot_local(self, survivor: SurvivorBase, index: int, /) -> None:
        if index == 0:
            self.lead_survivor = survivor
        else:
            survivors = list(self.survivors)
            survivors[index - 1] = survivor
            self.survivors = tuple(survivors)

    def unslot_local(self, survivor: SurvivorBase, /) -> None:
        if survivor == self.lead_survivor:
            self.lead_survivor = None
        else:
            self.survivors = tuple(
                None if survivor == old_survivor else old_survivor
                for old_survivor in self.survivors
            )

    def unslot_all_local(self) -> None:
        self.lead_survivor = None
        self.survivors = None, None, None, None, None, None, None

    async def slot(self, survivor: SurvivorBase, index: int, /) -> Dict:
        previous_squad = await survivor.squad(raise_read_only=True)
        if previous_squad is not None:
            previous_squad.unslot_local(survivor)

        data = await self._auth_checker.mcp_operation(
            operation="AssignWorkerToSquad",
            profile_id="campaign",
            json={
                "characterId": survivor.id,
                "squadId": self.id,
                "slotIndex": index,
            },
        )

        self.slot_local(survivor, index)

        return data

    async def unslot(self, survivor: SurvivorBase, /) -> Dict:
        if survivor == self.lead_survivor:
            index = 0
        else:
            index = self.survivors.index(survivor) + 1

        data = await self._auth_checker.mcp_operation(
            operation="AssignWorkerToSquad",
            profile_id="campaign",
            json={"characterId": survivor.id, "squadId": "", "index": index},
        )

        self.unslot_local(survivor)

        return data

    async def unslot_all(self) -> Dict:
        data = await self._auth_checker.mcp_operation(
            operation="UnassignAllSquads",
            profile_id="campaign",
            json={"squadIds": [self.id]},
        )

        self.unslot_all_local()

        return data
