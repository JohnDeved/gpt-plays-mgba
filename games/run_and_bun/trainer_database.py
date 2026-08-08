"""Lookup known Run & Bun trainers by stable overworld NPC identity."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


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


def database_path() -> Path:
    override = os.environ.get("RUNBUN_TRAINER_DATABASE")
    return Path(override).expanduser() if override else DEFAULT_DATABASE


def stable_trainer_key(map_group: int, map_number: int, local_id: int) -> str:
    return f"map:{int(map_group)}:{int(map_number)}/local:{int(local_id)}"


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
