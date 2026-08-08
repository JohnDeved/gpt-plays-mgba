"""ROM-backed wild encounter index for Pokémon Run & Bun v1.07.

The primary wild-header table and every referenced encounter table occupy one
small contiguous ROM region. Reading that region plus the species-name table
provides a complete searchable index without screenshots or external guides.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import struct
from typing import Any, Iterable

from .state import KNOWN_MAPS, decode_gen3


WILD_ROM_BLOCK_START = 0x08625D00
WILD_HEADERS_ADDRESS = 0x0862863C
WILD_HEADERS_SENTINEL = 0x08629078
WILD_ROM_BLOCK_END = WILD_HEADERS_SENTINEL + 20
WILD_HEADER_SIZE = 20

SPECIES_NAMES_ADDRESS = 0x083A0F80
SPECIES_NAME_STRIDE = 11
SPECIES_NAME_COUNT = 1100

METHOD_SLOT_WEIGHTS: dict[str, tuple[int, ...]] = {
    "land": (20, 20, 10, 10, 10, 10, 5, 5, 4, 4, 1, 1),
    "water": (60, 30, 5, 4, 1),
    "rock_smash": (60, 30, 5, 4, 1),
    "old_rod": (70, 30),
    "good_rod": (60, 20, 20),
    "super_rod": (40, 40, 15, 4, 1),
}

# Names used in the current reachable part of the run. Stable map IDs remain
# authoritative when a name has not yet been added.
ENCOUNTER_MAP_NAMES = {
    **KNOWN_MAPS,
    **{(0, number): f"Route{number + 85}" for number in range(16, 50)},
    (24, 7): "GraniteCave_1F",
    (24, 8): "GraniteCave_B1F",
    (24, 9): "GraniteCave_B2F",
}

DEFAULT_VISITED_MAPS_PATH = (
    Path(__file__).resolve().parents[2] / "runtime" / "session" / "visited_maps.json"
)


@dataclass(frozen=True)
class WildEncounter:
    map_group: int
    map_number: int
    method: str
    encounter_rate: int
    slot: int
    slot_percent: int
    min_level: int
    max_level: int
    species_id: int
    species_name: str

    @property
    def map_id(self) -> tuple[int, int]:
        return self.map_group, self.map_number


def _offset(address: int, blob: bytes) -> int:
    offset = address - WILD_ROM_BLOCK_START
    if offset < 0 or offset >= len(blob):
        raise ValueError(f"wild ROM pointer outside verified block: {address:#x}")
    return offset


def _u32(blob: bytes, address: int) -> int:
    return struct.unpack_from("<I", blob, _offset(address, blob))[0]


def _species_names(raw: bytes) -> dict[int, str]:
    names: dict[int, str] = {}
    for species_id in range(min(SPECIES_NAME_COUNT, len(raw) // SPECIES_NAME_STRIDE)):
        start = species_id * SPECIES_NAME_STRIDE
        name = decode_gen3(raw[start:start + SPECIES_NAME_STRIDE]).strip(" ?")
        if name:
            names[species_id] = name
    return names


def _slot_groups(method: str) -> tuple[tuple[str, int, tuple[int, ...]], ...]:
    if method != "fishing":
        weights = METHOD_SLOT_WEIGHTS[method]
        return ((method, 0, weights),)
    return (
        ("old_rod", 0, METHOD_SLOT_WEIGHTS["old_rod"]),
        ("good_rod", 2, METHOD_SLOT_WEIGHTS["good_rod"]),
        ("super_rod", 5, METHOD_SLOT_WEIGHTS["super_rod"]),
    )


def parse_primary_wild_encounters(
    blob: bytes,
    species_names_raw: bytes,
) -> list[WildEncounter]:
    """Parse the verified primary header table from two ROM snapshots."""
    expected_length = WILD_ROM_BLOCK_END - WILD_ROM_BLOCK_START
    if len(blob) < expected_length:
        raise ValueError(f"wild ROM block too short: {len(blob)} < {expected_length}")
    names = _species_names(species_names_raw)
    encounters: list[WildEncounter] = []
    header_address = WILD_HEADERS_ADDRESS
    while header_address <= WILD_HEADERS_SENTINEL:
        header_offset = _offset(header_address, blob)
        map_group, map_number = blob[header_offset:header_offset + 2]
        if (map_group, map_number) == (0xFF, 0xFF):
            if header_address != WILD_HEADERS_SENTINEL:
                raise ValueError(f"early wild-header sentinel at {header_address:#x}")
            break
        if blob[header_offset + 2:header_offset + 4] != b"\0\0":
            raise ValueError(f"wild-header padding mismatch at {header_address:#x}")
        pointers = struct.unpack_from("<4I", blob, header_offset + 4)
        for method, info_address in zip(
            ("land", "water", "rock_smash", "fishing"), pointers
        ):
            if info_address == 0:
                continue
            info_offset = _offset(info_address, blob)
            encounter_rate = blob[info_offset]
            slots_address = _u32(blob, info_address + 4)
            slots_offset = _offset(slots_address, blob)
            for method_name, first_slot, weights in _slot_groups(method):
                for local_slot, weight in enumerate(weights):
                    slot = first_slot + local_slot
                    record = slots_offset + slot * 4
                    min_level, max_level, species_id = struct.unpack_from("<BBH", blob, record)
                    encounters.append(WildEncounter(
                        map_group=map_group,
                        map_number=map_number,
                        method=method_name,
                        encounter_rate=encounter_rate,
                        slot=slot,
                        slot_percent=weight,
                        min_level=min_level,
                        max_level=max_level,
                        species_id=species_id,
                        species_name=names.get(species_id, f"Species#{species_id}"),
                    ))
        header_address += WILD_HEADER_SIZE
    else:
        raise ValueError("verified wild-header sentinel was not found")
    return encounters


def read_primary_wild_encounters(gba: Any) -> list[WildEncounter]:
    """Read and parse the complete primary encounter index in two ROM calls."""
    blob = gba.read_range(
        WILD_ROM_BLOCK_START, WILD_ROM_BLOCK_END - WILD_ROM_BLOCK_START
    )
    species_names = gba.read_range(
        SPECIES_NAMES_ADDRESS, SPECIES_NAME_COUNT * SPECIES_NAME_STRIDE
    )
    return parse_primary_wild_encounters(blob, species_names)


def load_visited_maps(path: Path = DEFAULT_VISITED_MAPS_PATH) -> set[tuple[int, int]]:
    if not path.exists():
        return set()
    payload = json.loads(path.read_text())
    return {
        (int(entry["group"]), int(entry["number"]))
        for entry in payload.get("maps", [])
    }


def find_catch_locations(
    encounters: Iterable[WildEncounter],
    *,
    species_id: int | None = None,
    species_name: str | None = None,
    map_id: tuple[int, int] | None = None,
    methods: set[str] | None = None,
    visited_maps: set[tuple[int, int]] | None = None,
    visited_only: bool = False,
) -> list[dict[str, Any]]:
    """Aggregate searchable locations by map, method, and species."""
    wanted_name = species_name.casefold() if species_id is None and species_name else None
    visited = visited_maps or set()
    grouped: dict[tuple[int, int, str, int], dict[str, Any]] = {}
    for encounter in encounters:
        if species_id is not None and encounter.species_id != species_id:
            continue
        if wanted_name is not None and encounter.species_name.casefold() != wanted_name:
            continue
        if map_id is not None and encounter.map_id != map_id:
            continue
        if methods is not None and encounter.method not in methods:
            continue
        if visited_only and encounter.map_id not in visited:
            continue
        key = (*encounter.map_id, encounter.method, encounter.species_id)
        item = grouped.setdefault(key, {
            "map": {
                "group": encounter.map_group,
                "number": encounter.map_number,
                "name": ENCOUNTER_MAP_NAMES.get(encounter.map_id),
                "visited": encounter.map_id in visited,
            },
            "method": encounter.method,
            "encounter_rate": encounter.encounter_rate,
            "species_id": encounter.species_id,
            "species_name": encounter.species_name,
            "min_level": encounter.min_level,
            "max_level": encounter.max_level,
            "slot_percent": 0,
            "slots": [],
        })
        item["min_level"] = min(item["min_level"], encounter.min_level)
        item["max_level"] = max(item["max_level"], encounter.max_level)
        item["slot_percent"] += encounter.slot_percent
        item["slots"].append(encounter.slot)
    return sorted(
        grouped.values(),
        key=lambda item: (
            not item["map"]["visited"], -item["slot_percent"],
            -item["encounter_rate"], item["map"]["group"],
            item["map"]["number"], item["method"], item["species_id"],
        ),
    )


def lookup_catchable(gba: Any, **filters: Any) -> list[dict[str, Any]]:
    filters.setdefault("visited_maps", load_visited_maps())
    return find_catch_locations(read_primary_wild_encounters(gba), **filters)
