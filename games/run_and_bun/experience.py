"""Small append-only experience store for verified Run & Bun observations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _path() -> Path:
    path = Path(__file__).resolve().parents[2] / "runtime" / "session" / "runbun_experience.jsonl"
    return path


def load_damage_memory() -> dict[tuple[int, int, int], list[int]]:
    """Load only exact-key damage samples; malformed records are ignored."""
    path = _path()
    if not path.exists():
        return {}
    memory: dict[tuple[int, int, int], list[int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            if record.get("kind") != "damage_sample":
                continue
            key = tuple(int(value) for value in record["key"])
            damage = int(record["damage"])
            if len(key) != 3 or damage < 0:
                continue
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        memory.setdefault(key, []).append(damage)
    return memory


def append_damage_sample(key: tuple[int, int, int], damage: int, *, feedback: str = "") -> None:
    if len(key) != 3 or damage < 0:
        raise ValueError("damage evidence key must be attacker/move/defender and damage >= 0")
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "kind": "damage_sample",
        "key": list(key),
        "damage": int(damage),
    }
    if feedback:
        record["feedback"] = feedback[-240:]
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")


def query_damage(
    *,
    attacker_species: int | None = None,
    move_id: int | None = None,
    defender_species: int | None = None,
) -> list[dict[str, Any]]:
    memory = load_damage_memory()
    result = []
    for (attacker, move, defender), samples in sorted(memory.items()):
        if attacker_species is not None and attacker != attacker_species:
            continue
        if move_id is not None and move != move_id:
            continue
        if defender_species is not None and defender != defender_species:
            continue
        result.append({
            "key": [attacker, move, defender],
            "min": min(samples),
            "max": max(samples),
            "n": len(samples),
        })
    return result
