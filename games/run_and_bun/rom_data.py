"""Cached battle metadata read from the verified Run & Bun v1.07 ROM.

The battle structs in EWRAM contain live move IDs/PP and battler types.  Move
names and the type chart are immutable ROM data, so read them once through the
Lua bridge and keep the hot path compact.  Addresses are profile-specific.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .state import decode_gen3, TYPE_NAMES


ROM_BASE = 0x08000000
ROM_TITLE = b"POKEMON EMER"
ROM_CODE = b"BPEE"

# Verified against Pokemon Run & Bun v1.07. The chart is a contiguous 19x19
# row-major table of unsigned Q4.12 values for type IDs 0..18.
TYPE_CHART_ADDRESS = 0x083ADEE2
TYPE_CHART_TYPES = 19
TYPE_CHART_SCALE = 0x1000

# Fixed cartridge ability IDs that nullify one attacking type.
ABILITY_TYPE_IMMUNITIES = {
    10: 13, 11: 11, 18: 10, 26: 4, 31: 13,
    78: 13, 87: 11, 114: 11, 157: 12,
}

# Move names are fixed 13-byte Gen III strings, indexed from move 1.  Move 0
# is the null move and has no display name.
MOVE_NAMES_ADDRESS = 0x083A4493
MOVE_NAME_STRIDE = 13

# Move 0 is a real null record, so records are indexed directly by move ID.
MOVE_TABLE_ADDRESS = 0x083B0C5E
MOVE_RECORD_STRIDE = 20

# Expanded Run & Bun species records. Species 0 is the null record and IDs
# index this table directly.
SPECIES_TABLE_ADDRESS = 0x083B7CE0
SPECIES_RECORD_STRIDE = 36

# One ROM pointer per species. Each target is a sequence of little-endian
# (move_id, level) pairs terminated by (0xFFFF, 0).
LEVEL_UP_LEARNSET_POINTERS_ADDRESS = 0x083EC73C
MAX_LEVEL_UP_MOVES = 128


class RomProfileError(RuntimeError):
    """The live ROM does not match the verified metadata profile."""


@dataclass(frozen=True)
class RomMove:
    move_id: int
    name: str | None
    power: int
    type_id: int
    type_name: str | None
    accuracy: int
    pp: int
    secondary_chance: int
    target_flags: int
    priority: int
    category: str
    raw_flags: tuple[int, int, int, int, int]


@dataclass(frozen=True)
class RomSpecies:
    species_id: int
    base_stats: tuple[int, int, int, int, int, int]
    type_ids: tuple[int, ...]
    type_names: tuple[str | None, ...]
    catch_rate: int
    exp_yield: int
    held_item_ids: tuple[int, int]
    gender_ratio: int
    egg_cycles: int
    friendship: int
    growth_rate: int
    egg_groups: tuple[int, int]
    ability_ids: tuple[int, ...]

    def zero_ev_stat_ranges(self, level: int) -> dict[str, tuple[int, int]]:
        """Inclusive Gen III IV/nature bounds before a wild Pokémon exists."""
        if not 1 <= level <= 100:
            raise ValueError("level must be between 1 and 100")
        hp = tuple(
            ((2 * self.base_stats[0] + iv) * level // 100) + level + 10
            for iv in (0, 31)
        )
        stats: dict[str, tuple[int, int]] = {"hp": hp}
        for name, base in zip(
            ("attack", "defense", "speed", "sp_attack", "sp_defense"),
            self.base_stats[1:],
        ):
            low = (((2 * base) * level // 100) + 5) * 90 // 100
            high = (((2 * base + 31) * level // 100) + 5) * 110 // 100
            stats[name] = (low, high)
        return stats

    def level_from_experience(self, experience: int) -> int:
        if experience < 0:
            raise ValueError("experience must be nonnegative")
        return max(
            level for level in range(1, 101)
            if experience_for_level(self.growth_rate, level) <= experience
        )


@dataclass(frozen=True)
class RomLearnedMove:
    move_id: int
    level: int


def experience_for_level(growth_rate: int, level: int) -> int:
    """Exact Gen III experience threshold for the six cartridge growth IDs."""
    if not 1 <= level <= 100:
        raise ValueError("level must be between 1 and 100")
    n = level
    if growth_rate == 0:  # Medium Fast
        value = n ** 3
    elif growth_rate == 1:  # Erratic
        if n <= 50:
            value = n ** 3 * (100 - n) // 50
        elif n <= 68:
            value = n ** 3 * (150 - n) // 100
        elif n <= 98:
            value = n ** 3 * ((1911 - 10 * n) // 3) // 500
        else:
            value = n ** 3 * (160 - n) // 100
    elif growth_rate == 2:  # Fluctuating
        if n <= 15:
            value = n ** 3 * ((n + 1) // 3 + 24) // 50
        elif n <= 36:
            value = n ** 3 * (n + 14) // 50
        else:
            value = n ** 3 * (n // 2 + 32) // 50
    elif growth_rate == 3:  # Medium Slow
        value = 6 * n ** 3 // 5 - 15 * n ** 2 + 100 * n - 140
    elif growth_rate == 4:  # Fast
        value = 4 * n ** 3 // 5
    elif growth_rate == 5:  # Slow
        value = 5 * n ** 3 // 4
    else:
        raise RomProfileError(f"unknown growth rate {growth_rate}")
    return max(0, value)


class BattleRomData:
    """Lazy, cached ROM metadata for one mGBA bridge connection."""

    def __init__(self, gba: Any):
        self.gba = gba
        self._validated = False
        self._type_chart_raw: bytes | None = None
        self._type_chart: dict[int, dict[int, float]] | None = None
        self._move_names: dict[int, str | None] = {0: None}
        self._moves: dict[int, RomMove] = {}
        self._species: dict[int, RomSpecies] = {}
        self._level_up_moves: dict[int, tuple[RomLearnedMove, ...]] = {}

    def validate(self) -> None:
        if self._validated:
            return
        header = self.gba.read_range(ROM_BASE + 0xA0, 0x10)
        if header[:12].rstrip(b"\0") != ROM_TITLE or header[12:16] != ROM_CODE:
            raise RomProfileError(
                f"unexpected ROM header title={header[:12]!r} code={header[12:16]!r}"
            )
        chart = self.gba.read_range(TYPE_CHART_ADDRESS, TYPE_CHART_TYPES * TYPE_CHART_TYPES * 2)
        if chart[:2] != (TYPE_CHART_SCALE).to_bytes(2, "little"):
            raise RomProfileError("type chart sentinel is not 1.0")
        ghost = (0 * TYPE_CHART_TYPES + 7) * 2
        if chart[ghost:ghost + 2] != b"\0\0":
            raise RomProfileError("type chart Normal -> Ghost sentinel failed")
        self._type_chart_raw = chart
        self._validated = True

    def type_chart(self) -> dict[int, dict[int, float]]:
        self.validate()
        if self._type_chart is None:
            raw = self._type_chart_raw
            if raw is None:
                raise RomProfileError("validated chart bytes missing")
            self._type_chart = {
                attacker: {
                    defender: int.from_bytes(
                        raw[(attacker * TYPE_CHART_TYPES + defender) * 2:][:2], "little"
                    ) / TYPE_CHART_SCALE
                    for defender in range(TYPE_CHART_TYPES)
                }
                for attacker in range(TYPE_CHART_TYPES)
            }
        return self._type_chart

    def move_name(self, move_id: int) -> str | None:
        self.validate()
        if move_id <= 0:
            return None
        if move_id not in self._move_names:
            raw = self.gba.read_range(
                MOVE_NAMES_ADDRESS + (move_id - 1) * MOVE_NAME_STRIDE,
                MOVE_NAME_STRIDE,
            )
            name = decode_gen3(raw).strip() or None
            self._move_names[move_id] = name
        return self._move_names[move_id]

    def move(self, move_id: int, *, type_id: int | None = None) -> RomMove:
        """Decode one immutable 20-byte move record from the cartridge."""
        self.validate()
        if move_id < 0:
            raise ValueError("move_id must be nonnegative")
        if move_id not in self._moves:
            raw = self.gba.read_range(
                MOVE_TABLE_ADDRESS + move_id * MOVE_RECORD_STRIDE,
                MOVE_RECORD_STRIDE,
            )
            if len(raw) != MOVE_RECORD_STRIDE:
                raise RomProfileError(f"move record {move_id} is truncated")
            resolved_type = raw[2]
            if not 0 <= resolved_type < TYPE_CHART_TYPES:
                raise RomProfileError(f"move {move_id} has invalid type {resolved_type}")
            power = int.from_bytes(raw[0:2], "little")
            category_code = int.from_bytes(raw[14:16], "little")
            category = {0: "physical", 1: "special", 2: "status"}.get(category_code, "unknown")
            self._moves[move_id] = RomMove(
                move_id=move_id,
                name=self.move_name(move_id),
                power=power,
                type_id=resolved_type,
                type_name=TYPE_NAMES.get(resolved_type),
                accuracy=raw[3],
                pp=raw[4],
                secondary_chance=raw[5],
                target_flags=int.from_bytes(raw[6:8], "little"),
                priority=int.from_bytes(raw[8:9], "little", signed=True),
                category=category,
                raw_flags=tuple(
                    int.from_bytes(raw[offset:offset + 2], "little")
                    for offset in (10, 12, 14, 16, 18)
                ),
            )
        move = self._moves[move_id]
        if type_id is not None and type_id != move.type_id:
            raise RomProfileError(
                f"live move type mismatch for {move_id}: ROM={move.type_id} live={type_id}"
            )
        return move

    def remember_move_type(self, move_id: int, type_id: int) -> RomMove:
        return self.move(move_id, type_id=type_id)

    def species(self, species_id: int) -> RomSpecies:
        """Decode one immutable expanded species record from the cartridge."""
        self.validate()
        if species_id <= 0:
            raise ValueError("species_id must be positive")
        if species_id not in self._species:
            raw = self.gba.read_range(
                SPECIES_TABLE_ADDRESS + species_id * SPECIES_RECORD_STRIDE,
                SPECIES_RECORD_STRIDE,
            )
            if len(raw) != SPECIES_RECORD_STRIDE:
                raise RomProfileError(f"species record {species_id} is truncated")
            stats = tuple(raw[:6])
            if not all(stats):
                raise RomProfileError(f"species {species_id} has invalid base stats {stats}")
            raw_types = (raw[6], raw[7])
            if any(type_id >= TYPE_CHART_TYPES for type_id in raw_types):
                raise RomProfileError(f"species {species_id} has invalid types {raw_types}")
            type_ids = tuple(dict.fromkeys(raw_types))
            ability_ids = tuple(
                ability_id
                for offset in (24, 26, 28)
                if (ability_id := int.from_bytes(raw[offset:offset + 2], "little"))
            )
            self._species[species_id] = RomSpecies(
                species_id=species_id,
                base_stats=stats,
                type_ids=type_ids,
                type_names=tuple(TYPE_NAMES.get(type_id) for type_id in type_ids),
                catch_rate=raw[8],
                exp_yield=int.from_bytes(raw[10:12], "little"),
                held_item_ids=(
                    int.from_bytes(raw[14:16], "little"),
                    int.from_bytes(raw[16:18], "little"),
                ),
                gender_ratio=raw[18],
                egg_cycles=raw[19],
                friendship=raw[20],
                growth_rate=raw[21],
                egg_groups=(raw[22], raw[23]),
                ability_ids=ability_ids,
            )
        return self._species[species_id]

    def level_up_moves(
        self, species_id: int, *, through_level: int | None = None
    ) -> tuple[RomLearnedMove, ...]:
        """Read the cartridge level-up learnset for one species."""
        self.species(species_id)
        if species_id not in self._level_up_moves:
            pointer = int.from_bytes(self.gba.read_range(
                LEVEL_UP_LEARNSET_POINTERS_ADDRESS + species_id * 4, 4
            ), "little")
            if not ROM_BASE <= pointer < ROM_BASE + 0x02000000:
                raise RomProfileError(
                    f"species {species_id} has invalid learnset pointer {pointer:#x}"
                )
            raw = self.gba.read_range(pointer, MAX_LEVEL_UP_MOVES * 4)
            learned: list[RomLearnedMove] = []
            for offset in range(0, len(raw), 4):
                move_id = int.from_bytes(raw[offset:offset + 2], "little")
                level = int.from_bytes(raw[offset + 2:offset + 4], "little")
                if move_id == 0xFFFF:
                    break
                if move_id <= 0 or not 1 <= level <= 100:
                    raise RomProfileError(
                        f"species {species_id} has invalid learnset entry "
                        f"move={move_id} level={level}"
                    )
                learned.append(RomLearnedMove(move_id=move_id, level=level))
            else:
                raise RomProfileError(f"species {species_id} learnset has no sentinel")
            self._level_up_moves[species_id] = tuple(learned)
        moves = self._level_up_moves[species_id]
        return moves if through_level is None else tuple(
            learned for learned in moves if learned.level <= through_level
        )

    def compact(self, move_ids: tuple[int, ...] | list[int]) -> list[dict[str, Any]]:
        """Token-efficient move metadata for a live observation."""
        return [
            {
                "id": move.move_id,
                "name": move.name,
                "type": move.type_name,
                "power": move.power,
                "accuracy": move.accuracy,
                "priority": move.priority,
                "category": move.category,
            }
            for move_id in move_ids
            if move_id and (move := self.move(move_id))
        ]
