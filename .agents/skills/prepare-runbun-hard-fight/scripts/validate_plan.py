#!/usr/bin/env python3
"""Validate a preparation-first Run & Bun hard-fight plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


SOURCES = {"ram", "rom", "emulator_api", "mixed"}
FORMATS = {"single", "double"}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REPO_ROOT = Path(__file__).resolve().parents[4]


def _promotion_errors(evidence: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(evidence, dict):
        return ["evidence must be an object"]
    profile_path = evidence.get("policy_profile")
    if isinstance(profile_path, str):
        profile = REPO_ROOT / profile_path
        digest = evidence.get("behavior_hash") or evidence.get("policy_bundle_sha256")
        if not profile.is_file():
            errors.append("evidence.policy_profile must name an existing repository file")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            errors.append("evidence.behavior_hash must be 64 lowercase hex characters")
        elif profile.is_file():
            try:
                sys.path.insert(0, str(REPO_ROOT))
                from games.run_and_bun.battle_policy import load_profile, load_strategies, policy_bundle_hash

                actual = policy_bundle_hash(load_profile(profile), load_strategies())
                if actual != digest:
                    errors.append("evidence.behavior_hash does not match policy profile and executable strategies")
            except (OSError, ValueError, json.JSONDecodeError) as error:
                errors.append(f"unable to hash executable policy profile: {error}")
        opening_digest = evidence.get("opening_checkpoint_sha256")
        if not isinstance(opening_digest, str) or not SHA256.fullmatch(opening_digest):
            errors.append("evidence.opening_checkpoint_sha256 must be 64 lowercase hex characters")
        runs = evidence.get("reproductions")
        if not isinstance(runs, list) or len(runs) < 3:
            errors.append("evidence.reproductions must contain three consecutive clean terminal clone wins")
            return errors
        streak = runs[-3:]
        if not all(
            isinstance(run, dict)
            and run.get("terminal_win") is True
            and run.get("clean_review") is True
            and run.get("source") == "clone"
            and run.get("behavior_hash") == digest
            and run.get("opening_checkpoint_sha256") == opening_digest
            for run in streak
        ):
            errors.append("last three reproductions must be clean terminal clone wins for the same behavior bundle and opening checkpoint")
        return errors
    policy_path = evidence.get("executable_policy")
    policy_digest = evidence.get("policy_sha256")
    opening_digest = evidence.get("opening_checkpoint_sha256")
    policy = REPO_ROOT / policy_path if isinstance(policy_path, str) else Path()
    if not isinstance(policy_path, str) or not policy.is_file():
        errors.append("evidence.executable_policy must name an existing repository file")
    if not isinstance(policy_digest, str) or not SHA256.fullmatch(policy_digest):
        errors.append("evidence.policy_sha256 must be 64 lowercase hex characters")
    elif policy.is_file() and hashlib.sha256(policy.read_bytes()).hexdigest() != policy_digest:
        errors.append("evidence.policy_sha256 does not match executable_policy")
    runs = evidence.get("reproductions")
    if not isinstance(runs, list) or len(runs) < 3:
        errors.append("evidence.reproductions must contain three consecutive terminal clone wins")
        return errors
    streak = runs[-3:]
    if not all(
        run.get("terminal_win") is True
        and run.get("source") == "clone"
        and run.get("policy_sha256") == policy_digest
        and run.get("opening_checkpoint_sha256") == opening_digest
        for run in streak
        if isinstance(run, dict)
    ) or not all(isinstance(run, dict) for run in streak):
        errors.append("last three reproductions must be terminal clone wins for the same policy and opening checkpoint")
    return errors


def template() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "fight": {
            "id": "map/local-id-or-trainer-id",
            "required": True,
            "format": "double",
            "identity_source": "ram",
            "roster_complete": False,
            "expected_roster_count": 0,
        },
        "checkpoint": {"path": "/absolute/path/pre-fight.ss", "sha256": ""},
        "level_cap": {"value": 0, "source": "ram"},
        "enemy_roster": [
            {
                "species_id": 0,
                "level": 0,
                "moves": [],
                "ability_id": None,
                "item_id": None,
                "source": "ram",
            }
        ],
        "party": [
            {
                "slot": 0,
                "species_id": 0,
                "level": 0,
                "current_hp": 0,
                "max_hp": 0,
                "status": 0,
                "moves": [],
                "pp": [],
                "max_pp": [],
                "role": "",
            }
        ],
        "leads": [0, 1],
        "plan": {
            "target_priorities": [],
            "opening_turn": [
                {"party_slot": 0, "action": "", "target": ""},
                {"party_slot": 1, "action": "", "target": ""},
            ],
            "contingencies": [],
            "classification": "heuristic",
        },
        "uncertainty": {"action_changing": [], "other": []},
        "readiness": {
            "checkpoint_verified": False,
            "party_healed": False,
            "pp_full": False,
            "level_cap_respected": False,
            "moves_and_items_set": False,
            "live_execution_allowed": False,
            "status": "blocked",
        },
        "evidence": {
            "policy_profile": "games/run_and_bun/policy_profiles/gavi.json",
            "behavior_hash": "",
            "opening_checkpoint_sha256": "",
            "reproductions": [],
        },
        "failure_history": {"same_plan_attempts": 0},
    }


def _dict(value: Any, name: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{name} must be an object")
        return {}
    return value


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def validate(data: Any) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    root = _dict(data, "plan", errors)
    if root.get("schema_version") not in {1, 2}:
        errors.append("schema_version must be 1 or 2")

    fight = _dict(root.get("fight"), "fight", errors)
    if not isinstance(fight.get("id"), str) or not fight.get("id", "").strip():
        errors.append("fight.id is required")
    if fight.get("required") is not True:
        errors.append("fight.required must be true")
    if fight.get("format") not in FORMATS:
        errors.append("fight.format must be single or double")
    if fight.get("identity_source") not in SOURCES:
        errors.append("fight.identity_source must be RAM/ROM/emulator sourced")
    if fight.get("roster_complete") is not True:
        errors.append("fight.roster_complete must be true")

    checkpoint = _dict(root.get("checkpoint"), "checkpoint", errors)
    checkpoint_path = checkpoint.get("path")
    digest = checkpoint.get("sha256")
    if not isinstance(checkpoint_path, str) or not Path(checkpoint_path).is_absolute():
        errors.append("checkpoint.path must be an absolute path")
    elif not Path(checkpoint_path).is_file():
        errors.append(f"checkpoint does not exist: {checkpoint_path}")
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        errors.append("checkpoint.sha256 must be 64 lowercase hex characters")
    elif isinstance(checkpoint_path, str) and Path(checkpoint_path).is_file():
        actual = hashlib.sha256(Path(checkpoint_path).read_bytes()).hexdigest()
        if actual != digest:
            errors.append("checkpoint.sha256 does not match checkpoint.path")

    cap = _dict(root.get("level_cap"), "level_cap", errors)
    cap_value = cap.get("value")
    if not _positive_int(cap_value):
        errors.append("level_cap.value must be a positive integer")
    if cap.get("source") not in SOURCES:
        errors.append("level_cap.source must be RAM/ROM/emulator sourced")

    enemies = root.get("enemy_roster")
    if not isinstance(enemies, list) or not enemies:
        errors.append("enemy_roster must be a non-empty array")
        enemies = []
    expected = fight.get("expected_roster_count")
    if not _positive_int(expected):
        errors.append("fight.expected_roster_count must be a positive integer")
    elif len(enemies) != expected:
        errors.append(f"enemy_roster has {len(enemies)} entries; expected {expected}")
    for index, enemy in enumerate(enemies):
        enemy = _dict(enemy, f"enemy_roster[{index}]", errors)
        if not _positive_int(enemy.get("species_id")):
            errors.append(f"enemy_roster[{index}].species_id must be positive")
        if not _positive_int(enemy.get("level")):
            errors.append(f"enemy_roster[{index}].level must be positive")
        moves = enemy.get("moves")
        if not isinstance(moves, list) or not 1 <= len(moves) <= 4 or not all(
            _positive_int(move) for move in moves
        ):
            errors.append(f"enemy_roster[{index}].moves must contain 1..4 move IDs")
        if enemy.get("source") not in SOURCES:
            errors.append(f"enemy_roster[{index}].source is not authoritative")

    party = root.get("party")
    if not isinstance(party, list) or not party:
        errors.append("party must be a non-empty array")
        party = []
    slots: set[int] = set()
    for index, mon in enumerate(party):
        mon = _dict(mon, f"party[{index}]", errors)
        slot = mon.get("slot")
        if not isinstance(slot, int) or isinstance(slot, bool) or not 0 <= slot <= 5:
            errors.append(f"party[{index}].slot must be 0..5")
        elif slot in slots:
            errors.append(f"duplicate party slot {slot}")
        else:
            slots.add(slot)
        if not _positive_int(mon.get("species_id")):
            errors.append(f"party[{index}].species_id must be positive")
        level = mon.get("level")
        if not _positive_int(level):
            errors.append(f"party[{index}].level must be positive")
        elif _positive_int(cap_value) and level > cap_value:
            errors.append(f"party[{index}].level {level} exceeds cap {cap_value}")
        hp, max_hp = mon.get("current_hp"), mon.get("max_hp")
        if not _positive_int(max_hp) or hp != max_hp:
            errors.append(f"party[{index}] must be fully healed")
        if mon.get("status") not in (0, None):
            errors.append(f"party[{index}] has nonzero status")
        moves, pp, max_pp = mon.get("moves"), mon.get("pp"), mon.get("max_pp")
        if not isinstance(moves, list) or not moves or not all(_positive_int(move) for move in moves):
            errors.append(f"party[{index}].moves must contain move IDs")
        if not isinstance(pp, list) or not isinstance(max_pp, list) or pp != max_pp:
            errors.append(f"party[{index}] PP must be full and paired with max_pp")
        if not isinstance(mon.get("role"), str) or not mon.get("role", "").strip():
            errors.append(f"party[{index}].role is required")

    lead_count = 2 if fight.get("format") == "double" else 1
    leads = root.get("leads")
    if not isinstance(leads, list) or len(leads) != lead_count:
        errors.append(f"leads must contain exactly {lead_count} party slots")
        leads = []
    elif len(set(leads)) != len(leads) or any(lead not in slots for lead in leads):
        errors.append("leads must be unique existing party slots")

    battle_plan = _dict(root.get("plan"), "plan.plan", errors)
    priorities = battle_plan.get("target_priorities")
    if not isinstance(priorities, list) or not priorities:
        errors.append("plan.target_priorities must be non-empty")
    opening = battle_plan.get("opening_turn")
    if not isinstance(opening, list) or len(opening) != lead_count:
        errors.append(f"plan.opening_turn must contain {lead_count} allied actions")
    else:
        opening_slots = set()
        for index, action in enumerate(opening):
            action = _dict(action, f"plan.opening_turn[{index}]", errors)
            opening_slots.add(action.get("party_slot"))
            if not isinstance(action.get("action"), str) or not action.get("action", "").strip():
                errors.append(f"plan.opening_turn[{index}].action is required")
            if not isinstance(action.get("target"), str) or not action.get("target", "").strip():
                errors.append(f"plan.opening_turn[{index}].target is required")
        if opening_slots != set(leads):
            errors.append("plan.opening_turn must cover every lead exactly once")
    contingencies = battle_plan.get("contingencies")
    if not isinstance(contingencies, list) or not contingencies:
        errors.append("plan.contingencies must be non-empty")
    if battle_plan.get("classification") not in {"forced", "minimax", "expected-best", "heuristic"}:
        errors.append("plan.classification must be forced, minimax, expected-best, or heuristic")

    uncertainty = _dict(root.get("uncertainty"), "uncertainty", errors)
    action_changing = uncertainty.get("action_changing")
    if not isinstance(action_changing, list):
        errors.append("uncertainty.action_changing must be an array")
    elif action_changing:
        errors.append("action-changing uncertainty remains unresolved")

    readiness = _dict(root.get("readiness"), "readiness", errors)
    for key in (
        "checkpoint_verified",
        "party_healed",
        "pp_full",
        "level_cap_respected",
        "moves_and_items_set",
        "live_execution_allowed",
    ):
        if readiness.get(key) is not True:
            errors.append(f"readiness.{key} must be true")
    if readiness.get("status") != "ready":
        errors.append("readiness.status must be ready")
    errors.extend(_promotion_errors(root.get("evidence")))

    history = _dict(root.get("failure_history"), "failure_history", errors)
    attempts = history.get("same_plan_attempts")
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0:
        errors.append("failure_history.same_plan_attempts must be a nonnegative integer")
    elif attempts >= 2:
        errors.append("same plan already failed twice; revise preparation before retrying")

    if enemies and len(party) < len(enemies):
        warnings.append(
            f"party has {len(party)} members against {len(enemies)} enemies; justify reduced depth"
        )
    if _positive_int(cap_value):
        below_cap = [mon.get("slot") for mon in party if _positive_int(mon.get("level")) and mon["level"] < cap_value]
        if below_cap:
            warnings.append(f"party slots below verified cap {cap_value}: {below_cap}")
    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", nargs="?", type=Path)
    parser.add_argument("--emit-template", action="store_true")
    args = parser.parse_args()
    if args.emit_template:
        print(json.dumps(template(), indent=2))
        return 0
    if args.plan is None:
        parser.error("PLAN.json is required unless --emit-template is used")
    try:
        data = json.loads(args.plan.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(json.dumps({"valid": False, "errors": [str(error)], "warnings": []}))
        return 1
    errors, warnings = validate(data)
    print(json.dumps({"valid": not errors, "errors": errors, "warnings": warnings}, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
