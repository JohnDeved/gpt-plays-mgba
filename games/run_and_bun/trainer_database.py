"""Lookup known Run & Bun trainers by stable overworld NPC identity."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .rom_data import BattleRomData, ROM_BASE
from .state import decode_gen3
from .wild_encounters import SPECIES_NAMES_ADDRESS, SPECIES_NAME_STRIDE


REPO_DATABASE = (
    Path(__file__).resolve().parents[2]
    / ".agents"
    / "skills"
    / "prepare-runbun-hard-fight"
    / "references"
    / "trainers.json"
)
GLOBAL_DATABASE = (
    Path.home()
    / ".codex"
    / "skills"
    / "prepare-runbun-hard-fight"
    / "references"
    / "trainers.json"
)
DEFAULT_DATABASE = REPO_DATABASE if REPO_DATABASE.exists() else GLOBAL_DATABASE
HARD_FIGHTS_PATH = Path(__file__).resolve().parents[2] / "runtime" / "session" / "classified_hard_fights.json"

TRAINER_TABLE_ADDRESS = 0x0839889C
TRAINER_RECORD_STRIDE = 0x28
TRAINER_PARTY_RECORD_STRIDE = 0x2C
ITEM_TABLE_ADDRESS = 0x08678D60
ITEM_RECORD_STRIDE = 0x2C
ITEM_NAME_LENGTH = 14


class RomImage:
    """Small read_range adapter for the same decoders used by live mGBA."""

    def __init__(self, data: bytes):
        self.data = data

    @classmethod
    def from_path(cls, path: Path) -> "RomImage":
        return cls(path.read_bytes())

    def read_range(self, address: int, length: int) -> bytes:
        offset = address - ROM_BASE
        if offset < 0 or offset + length > len(self.data):
            raise ValueError(f"ROM read outside image: {address:#010x}+{length}")
        return self.data[offset:offset + length]


def trainer_id_from_script(rom: Any, script_address: int | str) -> dict[str, int]:
    """Decode a direct trainerbattle command from an overworld NPC script."""
    address = int(script_address, 0) if isinstance(script_address, str) else int(script_address)
    raw = rom.read_range(address, 4)
    if len(raw) != 4 or raw[0] != 0x5C:
        raise ValueError(f"script {address:#010x} does not begin with trainerbattle")
    return {"script_address": address, "battle_type": raw[1], "trainer_id": int.from_bytes(raw[2:4], "little")}


def decode_rom_trainer(rom: Any, trainer_id: int) -> dict[str, Any]:
    """Decode the verified v1.07 trainer table and its compact party records."""
    metadata = BattleRomData(rom)
    metadata.validate()
    trainer_id = int(trainer_id)
    address = TRAINER_TABLE_ADDRESS + trainer_id * TRAINER_RECORD_STRIDE
    raw = rom.read_range(address, TRAINER_RECORD_STRIDE)
    party_count = raw[4]
    party_pointer = int.from_bytes(raw[8:12], "little")
    if not 1 <= party_count <= 6:
        raise ValueError(f"trainer {trainer_id} has invalid party count {party_count}")
    if not ROM_BASE <= party_pointer < ROM_BASE + 0x02000000:
        raise ValueError(f"trainer {trainer_id} has invalid party pointer {party_pointer:#010x}")

    roster: list[dict[str, Any]] = []
    uncertainties: list[str] = []
    for position in range(party_count):
        record_address = party_pointer + position * TRAINER_PARTY_RECORD_STRIDE
        mon = rom.read_range(record_address, TRAINER_PARTY_RECORD_STRIDE)
        species_id = int.from_bytes(mon[0x1A:0x1C], "little")
        item_id = int.from_bytes(mon[0x1C:0x1E], "little")
        moves = [int.from_bytes(mon[offset:offset + 2], "little") for offset in range(0x1E, 0x26, 2)]
        moves = [move for move in moves if move]
        species = metadata.species(species_id)
        possible_abilities = list(dict.fromkeys(species.ability_ids))
        ability_id = possible_abilities[0] if len(possible_abilities) == 1 else None
        if ability_id is None:
            uncertainties.append(f"party {position + 1} ability must be resolved from battle RAM")
        species_name = decode_gen3(rom.read_range(
            SPECIES_NAMES_ADDRESS + species_id * SPECIES_NAME_STRIDE,
            SPECIES_NAME_STRIDE,
        )).strip(" ?") or f"Species#{species_id}"
        item_name = None
        if item_id:
            item_name = decode_gen3(rom.read_range(
                ITEM_TABLE_ADDRESS + item_id * ITEM_RECORD_STRIDE,
                ITEM_NAME_LENGTH,
            )).strip(" ?") or None
        roster.append({
            "send_out_position": position + 1,
            "species_id": species_id,
            "species_name": species_name,
            "level": mon[0x19],
            "types": list(species.type_ids),
            "ability_id": ability_id,
            "ability_name": None,
            "possible_ability_ids": possible_abilities,
            "held_item_id": item_id,
            "held_item_name": item_name,
            "moves": moves,
            "move_names": [metadata.move_name(move) for move in moves],
            "rom_record_address": f"0x{record_address:08x}",
        })
    return {
        "trainer_id": trainer_id,
        "trainer_name": decode_gen3(raw[0x10:0x20]).strip(" ?") or None,
        "party_count": party_count,
        "trainer_record_address": f"0x{address:08x}",
        "party_pointer": f"0x{party_pointer:08x}",
        "roster": roster,
        "uncertainties": uncertainties,
    }


def profile_from_npc_script(
    rom: Any,
    *,
    script_address: int | str,
    map_group: int,
    map_number: int,
    local_id: int,
    graphics_id: int | None = None,
) -> dict[str, Any]:
    """Generate an additive trainer-database skeleton from one NPC script."""
    script = trainer_id_from_script(rom, script_address)
    decoded = decode_rom_trainer(rom, script["trainer_id"])
    key = stable_trainer_key(map_group, map_number, local_id)
    complete = not decoded["uncertainties"]
    return {
        "key": key,
        "name": decoded["trainer_name"],
        "overworld": {
            "map_group": int(map_group), "map_number": int(map_number), "local_id": int(local_id),
            "graphics_id": graphics_id, "script_address": f"0x{script['script_address']:08x}",
            "trainer_id": script["trainer_id"],
        },
        "battle": {
            "format": "double" if script["battle_type"] == 2 else "single",
            "required": True, "hard_fight": False, "roster_complete": complete,
            "roster_count": decoded["party_count"],
            "identity_source": "rom_event_script_and_trainer_table",
            "roster_source": "rom_trainer_party_records",
        },
        "roster": decoded["roster"],
        "uncertainties": decoded["uncertainties"],
        "rom_evidence": {
            "trainer_record_address": decoded["trainer_record_address"],
            "party_pointer": decoded["party_pointer"],
            "trainerbattle_type": script["battle_type"],
        },
    }


def database_path() -> Path:
    override = os.environ.get("RUNBUN_TRAINER_DATABASE")
    return Path(override).expanduser() if override else DEFAULT_DATABASE


def stable_trainer_key(map_group: int, map_number: int, local_id: int) -> str:
    return f"map:{int(map_group)}:{int(map_number)}/local:{int(local_id)}"


def is_classified_hard(key: str, path: Path | None = None) -> bool:
    selected = path or HARD_FIGHTS_PATH
    if not selected.is_file():
        return False
    try:
        data = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return key in (data.get("hard_fights") or {})


def mark_hard_fight(key: str, *, review_id: str | None = None, path: Path | None = None) -> dict[str, Any]:
    selected = path or HARD_FIGHTS_PATH
    try:
        data = json.loads(selected.read_text(encoding="utf-8")) if selected.is_file() else {}
    except json.JSONDecodeError:
        data = {}
    data.setdefault("schema_version", 1)
    data.setdefault("hard_fights", {})
    data["hard_fights"].setdefault(key, {"first_loss_review_id": review_id})
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return data["hard_fights"][key]


def load_trainer_database(path: Path | None = None) -> dict[str, Any]:
    selected = path or database_path()
    return json.loads(selected.read_text(encoding="utf-8"))


def lookup_trainer(
    *,
    map_group: int,
    map_number: int,
    local_id: int,
    graphics_id: int | None = None,
    script_address: int | str | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Return a trainer record plus secondary-identity verification."""
    key = stable_trainer_key(map_group, map_number, local_id)
    database = load_trainer_database(path)
    record = database.get("trainers", {}).get(key)
    mismatches: list[str] = []
    if record is not None:
        overworld = record.get("overworld", {})
        if graphics_id is not None and overworld.get("graphics_id") != graphics_id:
            mismatches.append(
                f"graphics_id observed={graphics_id} database={overworld.get('graphics_id')}"
            )
        if script_address is not None:
            observed = (
                f"0x{script_address:08x}"
                if isinstance(script_address, int)
                else str(script_address).lower()
            )
            expected = str(overworld.get("script_address", "")).lower()
            if observed != expected:
                mismatches.append(
                    f"script_address observed={observed} database={expected or None}"
                )
    return {
        "key": key,
        "found": record is not None,
        "trusted": record is not None and not mismatches,
        "identity_mismatches": mismatches,
        "record": record,
        "database": str(path or database_path()),
    }
