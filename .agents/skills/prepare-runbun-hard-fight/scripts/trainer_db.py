#!/usr/bin/env python3
"""Query, validate, or atomically update the Run & Bun trainer database."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_DB = Path(__file__).resolve().parents[1] / "references" / "trainers.json"
REPO_ROOT = Path(__file__).resolve().parents[4]
HEX_ADDRESS = re.compile(r"^0x08[0-9a-fA-F]{6}$")


def stable_key(map_group: int, map_number: int, local_id: int) -> str:
    return f"map:{map_group}:{map_number}/local:{local_id}"


def load_database(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_database(database: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(database, dict):
        return ["database root must be an object"]
    if database.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    game = database.get("game")
    if not isinstance(game, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(game.get("rom_sha256", ""))):
        errors.append("game.rom_sha256 must be 64 lowercase hex characters")
    trainers = database.get("trainers")
    if not isinstance(trainers, dict):
        return errors + ["trainers must be an object"]
    seen_npcs: set[tuple[int, int, int]] = set()
    for key, trainer in trainers.items():
        prefix = f"trainers[{key!r}]"
        if not isinstance(trainer, dict):
            errors.append(f"{prefix} must be an object")
            continue
        overworld = trainer.get("overworld")
        battle = trainer.get("battle")
        roster = trainer.get("roster")
        if not isinstance(overworld, dict):
            errors.append(f"{prefix}.overworld must be an object")
            continue
        identity = tuple(overworld.get(name) for name in ("map_group", "map_number", "local_id"))
        if not all(isinstance(value, int) and value >= 0 for value in identity):
            errors.append(f"{prefix} has invalid map/local identity")
            continue
        expected_key = stable_key(*identity)
        if key != expected_key or trainer.get("key") != expected_key:
            errors.append(f"{prefix} key must be {expected_key}")
        if identity in seen_npcs:
            errors.append(f"duplicate NPC identity {identity}")
        seen_npcs.add(identity)
        if not isinstance(overworld.get("graphics_id"), int):
            errors.append(f"{prefix}.overworld.graphics_id must be an integer")
        address = overworld.get("script_address")
        if address is not None and not HEX_ADDRESS.fullmatch(str(address)):
            errors.append(f"{prefix}.overworld.script_address must be a ROM address")
        if not isinstance(battle, dict):
            errors.append(f"{prefix}.battle must be an object")
            continue
        if battle.get("format") not in {"single", "double"}:
            errors.append(f"{prefix}.battle.format must be single or double")
        if battle.get("roster_complete") is not True:
            errors.append(f"{prefix}.battle.roster_complete must be true for committed records")
        if not isinstance(roster, list) or not roster:
            errors.append(f"{prefix}.roster must be non-empty")
            continue
        if battle.get("roster_count") != len(roster):
            errors.append(f"{prefix}.battle.roster_count does not match roster")
        positions: set[int] = set()
        for index, mon in enumerate(roster):
            mon_prefix = f"{prefix}.roster[{index}]"
            if not isinstance(mon, dict):
                errors.append(f"{mon_prefix} must be an object")
                continue
            position = mon.get("send_out_position")
            if not isinstance(position, int) or position < 1 or position in positions:
                errors.append(f"{mon_prefix}.send_out_position must be unique and positive")
            positions.add(position)
            for field in ("species_id", "level", "held_item_id"):
                if not isinstance(mon.get(field), int) or mon[field] < 0:
                    errors.append(f"{mon_prefix}.{field} must be a nonnegative integer")
            types = mon.get("types")
            if not isinstance(types, list) or not 1 <= len(types) <= 2 or not all(
                isinstance(value, int) and value >= 0 for value in types
            ):
                errors.append(f"{mon_prefix}.types must contain one or two type IDs")
            ability_id = mon.get("ability_id")
            possible_abilities = mon.get("possible_ability_ids")
            if not isinstance(ability_id, int) or ability_id < 0:
                if ability_id is not None or not isinstance(possible_abilities, list) or not possible_abilities or not all(
                    isinstance(value, int) and value >= 0 for value in possible_abilities
                ):
                    errors.append(f"{mon_prefix}.ability_id must be nonnegative or null with possible_ability_ids")
            moves = mon.get("moves")
            names = mon.get("move_names")
            if not isinstance(moves, list) or not 1 <= len(moves) <= 4 or not all(
                isinstance(move, int) and move > 0 for move in moves
            ):
                errors.append(f"{mon_prefix}.moves must contain 1..4 move IDs")
            if not isinstance(names, list) or len(names) != len(moves or []):
                errors.append(f"{mon_prefix}.move_names must align with moves")
            if "held_item_name" not in mon:
                errors.append(f"{mon_prefix}.held_item_name must be explicit, even when null")
    return errors


def find_record(
    database: dict[str, Any],
    *,
    map_group: int,
    map_number: int,
    local_id: int,
    graphics_id: int | None,
    script_address: str | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    record = database.get("trainers", {}).get(stable_key(map_group, map_number, local_id))
    mismatches: list[str] = []
    if record:
        overworld = record["overworld"]
        if graphics_id is not None and overworld.get("graphics_id") != graphics_id:
            mismatches.append(
                f"graphics_id mismatch: observed {graphics_id}, database {overworld.get('graphics_id')}"
            )
        if script_address is not None and str(overworld.get("script_address", "")).lower() != script_address.lower():
            mismatches.append(
                f"script_address mismatch: observed {script_address}, database {overworld.get('script_address')}"
            )
    return record, mismatches


def atomic_write(path: Path, database: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(database, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    subparsers.add_parser("list")
    find = subparsers.add_parser("find")
    find.add_argument("--map-group", required=True, type=int)
    find.add_argument("--map-number", required=True, type=int)
    find.add_argument("--local-id", required=True, type=int)
    find.add_argument("--graphics-id", type=int)
    find.add_argument("--script-address")
    upsert = subparsers.add_parser("upsert")
    upsert.add_argument("record", type=Path)
    decode = subparsers.add_parser("decode-rom")
    decode.add_argument("rom", type=Path)
    decode.add_argument("--script-address", required=True)
    decode.add_argument("--map-group", required=True, type=int)
    decode.add_argument("--map-number", required=True, type=int)
    decode.add_argument("--local-id", required=True, type=int)
    decode.add_argument("--graphics-id", type=int)
    scan = subparsers.add_parser("find-rom-name")
    scan.add_argument("rom", type=Path)
    scan.add_argument("name")
    scan.add_argument("--max-trainer-id", type=int, default=1024)
    args = parser.parse_args()

    try:
        database = load_database(args.database)
    except (OSError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "errors": [str(error)]}, indent=2))
        return 1

    if args.command == "validate":
        errors = validate_database(database)
        print(json.dumps({"ok": not errors, "records": len(database.get("trainers", {})), "errors": errors}, indent=2))
        return 0 if not errors else 1
    if args.command == "list":
        print(json.dumps(list(database.get("trainers", {}).values()), indent=2, ensure_ascii=False))
        return 0
    if args.command == "find":
        record, mismatches = find_record(
            database,
            map_group=args.map_group,
            map_number=args.map_number,
            local_id=args.local_id,
            graphics_id=args.graphics_id,
            script_address=args.script_address,
        )
        print(json.dumps({"found": record is not None, "record": record, "identity_mismatches": mismatches}, indent=2, ensure_ascii=False))
        return 0 if record is not None and not mismatches else 1
    if args.command == "decode-rom":
        sys.path.insert(0, str(REPO_ROOT))
        from games.run_and_bun.trainer_database import RomImage, profile_from_npc_script

        try:
            profile = profile_from_npc_script(
                RomImage.from_path(args.rom),
                script_address=args.script_address,
                map_group=args.map_group,
                map_number=args.map_number,
                local_id=args.local_id,
                graphics_id=args.graphics_id,
            )
        except (OSError, RuntimeError, ValueError) as error:
            print(json.dumps({"ok": False, "errors": [str(error)]}, indent=2))
            return 1
        print(json.dumps(profile, indent=2, ensure_ascii=False))
        return 0
    if args.command == "find-rom-name":
        sys.path.insert(0, str(REPO_ROOT))
        from games.run_and_bun.state import decode_gen3
        from games.run_and_bun.trainer_database import (
            RomImage, TRAINER_RECORD_STRIDE, TRAINER_TABLE_ADDRESS, decode_rom_trainer,
        )

        rom = RomImage.from_path(args.rom)
        matches = []
        for trainer_id in range(args.max_trainer_id + 1):
            raw = rom.read_range(TRAINER_TABLE_ADDRESS + trainer_id * TRAINER_RECORD_STRIDE, TRAINER_RECORD_STRIDE)
            if decode_gen3(raw[0x10:0x20]).strip(" ?").casefold() == args.name.casefold():
                matches.append(decode_rom_trainer(rom, trainer_id))
        print(json.dumps({"name": args.name, "matches": matches}, indent=2, ensure_ascii=False))
        return 0 if matches else 1

    record = json.loads(args.record.read_text(encoding="utf-8"))
    overworld = record.get("overworld", {})
    key = stable_key(overworld.get("map_group"), overworld.get("map_number"), overworld.get("local_id"))
    record["key"] = key
    candidate = json.loads(json.dumps(database))
    candidate.setdefault("trainers", {})[key] = record
    errors = validate_database(candidate)
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, indent=2))
        return 1
    atomic_write(args.database, candidate)
    print(json.dumps({"ok": True, "key": key, "records": len(candidate["trainers"])}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
