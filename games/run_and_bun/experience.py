"""Small append-only experience store for verified Run & Bun observations."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any


def _path() -> Path:
    path = Path(__file__).resolve().parents[2] / "runtime" / "session" / "runbun_experience.jsonl"
    return path


def load_damage_memory() -> dict[tuple[int, int, int], list[int]]:
    """Load only explicitly context-verified samples; legacy records are quarantined."""
    path = _path()
    if not path.exists():
        return {}
    memory: dict[tuple[int, int, int], list[int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            if (
                record.get("kind") != "damage_sample"
                or record.get("schema_version") != 2
                or record.get("context_verified") is not True
            ):
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
        "schema_version": 1,
        "context_verified": False,
        "key": list(key),
        "damage": int(damage),
    }
    if feedback:
        record["feedback"] = feedback[-240:]
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")


def _battle_mons(observation: dict[str, Any]) -> list[dict[str, Any]]:
    fields = (
        "species", "level", "current_hp", "max_hp", "types", "moves", "pp",
        "attack", "defense", "speed", "special_attack", "special_defense",
        "stat_stages", "status", "ability", "held_item",
    )
    return [
        {"slot": mon.get("slot"), **{key: mon.get("state", {}).get(key) for key in fields}}
        for mon in observation.get("battle", {}).get("mons", [])
        if mon.get("present")
    ]


def _mon(observation: dict[str, Any], slot: int) -> dict[str, Any] | None:
    return next(
        (
            mon.get("state")
            for mon in observation.get("battle", {}).get("mons", [])
            if mon.get("present") and mon.get("slot") == slot
        ),
        None,
    )


def _party_mon(observation: dict[str, Any], species: int) -> dict[str, Any] | None:
    matches = [
        mon.get("state")
        for mon in observation.get("party", {}).get("mons", [])
        if mon.get("present") and mon.get("state", {}).get("species") == species
    ]
    return matches[0] if len(matches) == 1 else None


def _context(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if state is None:
        return None
    fields = (
        "species", "level", "current_hp", "max_hp", "types", "attack", "defense",
        "speed", "special_attack", "special_defense", "stat_stages", "status",
        "ability", "held_item",
    )
    return {key: state.get(key) for key in fields}


def _feedback_signals(feedback: str) -> dict[str, Any]:
    lowered = feedback.casefold()
    hit = re.search(r"hit\s+(\d+)\s+time", lowered)
    effectiveness = (
        "immune" if "doesn't affect" in lowered or "does not affect" in lowered
        else "super_effective" if "super effective" in lowered
        else "not_very_effective" if "not very effective" in lowered
        else "neutral_or_unknown"
    )
    return {
        "critical": "critical hit" in lowered,
        "effectiveness": effectiveness,
        "berry_or_item_restoration": "berry" in lowered or "restored health" in lowered,
        "drain": "energy drained" in lowered or "health is sapped" in lowered,
        "residual": any(
            marker in lowered
            for marker in ("hurt by", "poison", "infestation", "leech seed", "wrapped", "bind")
        ),
        "multi_hit_count": int(hit.group(1)) if hit else None,
    }


def _damage_event(
    *,
    source: str,
    move_id: int,
    attacker: dict[str, Any] | None,
    defender_before: dict[str, Any] | None,
    defender_after: dict[str, Any] | None,
    signals: dict[str, Any],
    interrupted: bool = False,
) -> dict[str, Any]:
    same_target = bool(
        defender_before
        and defender_after
        and defender_before.get("species") == defender_after.get("species")
    )
    hp_before = int(defender_before.get("current_hp", 0)) if defender_before else None
    hp_after = int(defender_after.get("current_hp", 0)) if defender_after else None
    net_damage = (
        max(hp_before - hp_after, 0)
        if same_target and hp_before is not None and hp_after is not None
        else None
    )
    censored_ko = bool(same_target and hp_before and hp_after == 0)
    interference = bool(signals["berry_or_item_restoration"] or signals["residual"])
    exact = bool(net_damage and not censored_ko and not interference and not interrupted)
    return {
        "source": source,
        "move_id": int(move_id),
        "attacker": _context(attacker),
        "defender": _context(defender_before),
        "hp_before": hp_before,
        "hp_after": hp_after,
        "net_damage": net_damage,
        "direct_damage": net_damage if exact else None,
        "exact": exact,
        "censored_ko": censored_ko,
        "interference": {
            "berry_or_item_restoration": signals["berry_or_item_restoration"],
            "residual": signals["residual"],
            "drain": signals["drain"],
        },
        "critical": signals["critical"],
        "effectiveness": signals["effectiveness"],
        "multi_hit_count": signals["multi_hit_count"],
    }


def contextual_effects(
    before: dict[str, Any],
    after: dict[str, Any],
    action: dict[str, Any],
    actual: dict[str, Any],
) -> dict[str, Any]:
    feedback = str((actual.get("resolution") or {}).get("feedback", ""))
    signals = _feedback_signals(feedback)
    events: list[dict[str, Any]] = []
    if action.get("kind") == "move" and actual.get("allied_action_outcome") == "executed":
        events.append(_damage_event(
            source="player",
            move_id=int(action["move_id"]),
            attacker=_mon(before, 0),
            defender_before=_mon(before, 1),
            defender_after=_mon(after, 1),
            signals=signals,
        ))
    enemy_move = actual.get("enemy_move_id")
    if enemy_move is not None:
        defender_after = _mon(after, 0)
        defender_before = _mon(before, 0)
        if defender_after and defender_before and defender_after.get("species") != defender_before.get("species"):
            defender_before = _party_mon(before, int(defender_after["species"]))
        events.append(_damage_event(
            source="opponent",
            move_id=int(enemy_move),
            attacker=_mon(before, 1),
            defender_before=defender_before,
            defender_after=defender_after,
            signals=signals,
        ))
    return {"feedback_signals": signals, "damage": events}


def append_battle_transition(
    before: dict[str, Any],
    after: dict[str, Any],
    action: dict[str, Any],
    actual: dict[str, Any],
    *,
    pre_state_hash: str,
    post_state_hash: str,
) -> None:
    """Persist a verified contextual transition; legacy samples remain quarantined."""
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "kind": "battle_transition",
        "schema_version": 2,
        "source": os.environ.get("RUNBUN_SESSION_KIND", "live"),
        "pre_state_hash": pre_state_hash,
        "post_state_hash": post_state_hash,
        "action": action,
        "actual": actual,
        "effects": contextual_effects(before, after, action, actual),
        "before": _battle_mons(before),
        "after": _battle_mons(after),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")


def query_damage(
    *,
    attacker_species: int | None = None,
    move_id: int | None = None,
    defender_species: int | None = None,
) -> list[dict[str, Any]]:
    path = _path()
    if not path.exists():
        return []
    result: list[dict[str, Any]] = []
    legacy: dict[tuple[int, int, int], list[int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("kind") == "damage_sample":
            try:
                key = tuple(int(value) for value in record["key"])
                damage = int(record["damage"])
            except (KeyError, TypeError, ValueError):
                continue
            if len(key) == 3:
                legacy.setdefault(key, []).append(damage)
            continue
        if record.get("kind") != "battle_transition" or record.get("schema_version") != 2:
            continue
        for event in (record.get("effects") or {}).get("damage", []):
            attacker = (event.get("attacker") or {}).get("species")
            defender = (event.get("defender") or {}).get("species")
            move = event.get("move_id")
            if attacker_species is not None and attacker != attacker_species:
                continue
            if move_id is not None and move != move_id:
                continue
            if defender_species is not None and defender != defender_species:
                continue
            result.append({
                "key": [attacker, move, defender],
                "damage": event.get("direct_damage"),
                "net_damage": event.get("net_damage"),
                "exact": event.get("exact", False),
                "censored_ko": event.get("censored_ko", False),
                "context": {"attacker": event.get("attacker"), "defender": event.get("defender")},
                "effects": {
                    "critical": event.get("critical"),
                    "effectiveness": event.get("effectiveness"),
                    "multi_hit_count": event.get("multi_hit_count"),
                    "interference": event.get("interference"),
                },
                "pre_state_hash": record.get("pre_state_hash"),
                "post_state_hash": record.get("post_state_hash"),
                "schema_version": 2,
                "proof": "contextual_transition",
                "steers_tactical_bounds": False,
            })
    for (attacker, move, defender), samples in sorted(legacy.items()):
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
            "schema_version": 1,
            "proof": "legacy_context_missing",
            "steers_tactical_bounds": False,
        })
    return result
