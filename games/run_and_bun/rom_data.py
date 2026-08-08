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

# Verified against Pokemon Run & Bun v1.07.  The chart is row-major, stored as
# unsigned Q4.12 values.  This build reserves 20 entries (the live battle
# currently uses IDs 0..18).
TYPE_CHART_ADDRESS = 0x083ADEE0
TYPE_CHART_TYPES = 20
TYPE_CHART_SCALE = 0x1000

# Move names are fixed 13-byte Gen III strings, indexed from move 1.  Move 0
# is the null move and has no display name.
MOVE_NAMES_ADDRESS = 0x083A4493
MOVE_NAME_STRIDE = 13


class RomProfileError(RuntimeError):
    """The live ROM does not match the verified metadata profile."""


@dataclass(frozen=True)
class RomMove:
    move_id: int
    name: str | None
    type_id: int | None = None
    type_name: str | None = None


class BattleRomData:
    """Lazy, cached ROM metadata for one mGBA bridge connection."""

    def __init__(self, gba: Any):
        self.gba = gba
        self._validated = False
        self._type_chart_raw: bytes | None = None
        self._type_chart: dict[int, dict[int, float]] | None = None
        self._move_names: dict[int, str | None] = {0: None}
        self._move_types: dict[int, int] = {}

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
        # This ROM stores the normal row with the historical None column;
        # Ghost's sentinel is therefore at raw column 8.
        ghost = (0 * TYPE_CHART_TYPES + 8) * 2
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
        """Return a compact move record, enriching it with live menu type data."""
        if type_id is not None:
            if not 0 <= type_id < TYPE_CHART_TYPES:
                raise ValueError(f"invalid type id {type_id}")
            self._move_types[move_id] = type_id
        resolved_type = self._move_types.get(move_id)
        return RomMove(
            move_id=move_id,
            name=self.move_name(move_id),
            type_id=resolved_type,
            type_name=TYPE_NAMES.get(resolved_type) if resolved_type is not None else None,
        )

    def remember_move_type(self, move_id: int, type_id: int) -> RomMove:
        return self.move(move_id, type_id=type_id)

    def compact(self, move_ids: tuple[int, ...] | list[int]) -> list[dict[str, Any]]:
        """Token-efficient move metadata for a live observation."""
        return [
            {
                "id": move.move_id,
                "name": move.name,
                "type": move.type_name,
            }
            for move_id in move_ids
            if move_id and (move := self.move(move_id))
        ]
