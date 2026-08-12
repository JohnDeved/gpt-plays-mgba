#!/usr/bin/env python3
"""Initialize, validate, summarize, and rank Run & Bun progress ledgers."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVIDENCE_KINDS = {
    "ram", "rom", "emulator_api", "savestate_decode", "event_flag",
    "trainer_flag", "badge", "key_item",
}
GOAL_STATUSES = {"active", "achieved", "superseded"}
MILESTONE_STATUSES = {"pending", "completed"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def evidence_valid(entry: Any) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get("kind") in EVIDENCE_KINDS
        and isinstance(entry.get("source"), str)
        and bool(entry["source"].strip())
        and entry.get("value") not in (None, "")
    )


def rank_for(record: dict[str, Any], completed_ids: list[str]) -> list[int]:
    by_id = {item["id"]: item for item in record.get("milestones", [])}
    completed = [by_id[item_id] for item_id in completed_ids if item_id in by_id]
    required = [item for item in completed if item.get("required")]
    return [
        max((int(item["order"]) for item in required), default=-1),
        len(required),
        len(completed),
    ]


def validate(record: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    required = (
        "schema_version", "game", "global_goal", "current_goal", "goal_history",
        "milestones", "checkpoints", "furthest_checkpoint_sha256", "next_actions",
        "blockers", "last_reconciled",
    )
    for key in required:
        if key not in record:
            errors.append(f"missing {key}")
    if errors:
        return errors, warnings
    if record["schema_version"] != 1:
        errors.append("schema_version must be 1")

    game = record["game"]
    if len(str(game.get("rom_sha256", ""))) != 64:
        errors.append("game.rom_sha256 must be a 64-character SHA-256")
    for key in ("title", "version"):
        if not str(game.get(key, "")).strip():
            errors.append(f"game.{key} must be non-empty")

    global_goal = record["global_goal"]
    if global_goal.get("status") not in {"active", "complete"}:
        errors.append("global_goal.status must be active or complete")
    for key in ("objective", "completion_oracle"):
        if not str(global_goal.get(key, "")).strip():
            errors.append(f"global_goal.{key} must be non-empty")
    global_evidence = global_goal.get("evidence", [])
    if global_goal.get("status") == "complete":
        if global_goal.get("completion_verified") is not True:
            errors.append("completed global goal requires completion_verified true")
        if not global_evidence or not all(evidence_valid(item) for item in global_evidence):
            errors.append("completed global goal requires authoritative evidence")
    elif global_goal.get("completion_verified"):
        errors.append("active global goal cannot have completion_verified true")

    current = record["current_goal"]
    if not isinstance(current, dict) or current.get("status") != "active":
        errors.append("current_goal must be one active goal")
    else:
        for key in ("id", "objective", "why_now"):
            if not str(current.get(key, "")).strip():
                errors.append(f"current_goal.{key} must be non-empty")
        conditions = current.get("completion_conditions", [])
        if not conditions:
            errors.append("current_goal requires completion_conditions")
        for index, condition in enumerate(conditions):
            if not str(condition.get("claim", "")).strip() or not str(condition.get("oracle", "")).strip():
                errors.append(f"current_goal condition {index} needs claim and oracle")
            evidence = condition.get("evidence", [])
            if condition.get("satisfied") and (not evidence or not all(evidence_valid(item) for item in evidence)):
                errors.append(f"satisfied current_goal condition {index} lacks authoritative evidence")
        if conditions and all(item.get("satisfied") for item in conditions):
            errors.append("current_goal is fully satisfied; archive it and set the next active goal")

    history = record["goal_history"]
    if not isinstance(history, list):
        errors.append("goal_history must be a list")
        history = []
    for index, goal in enumerate(history):
        if goal.get("status") not in GOAL_STATUSES - {"active"}:
            errors.append(f"goal_history[{index}] must be achieved or superseded")
        if goal.get("status") == "superseded" and not str(goal.get("supersede_reason", "")).strip():
            errors.append(f"goal_history[{index}] superseded without reason")

    milestones = record["milestones"]
    if not isinstance(milestones, list):
        errors.append("milestones must be a list")
        milestones = []
    ids = [item.get("id") for item in milestones]
    orders = [item.get("order") for item in milestones]
    if len(ids) != len(set(ids)) or None in ids:
        errors.append("milestone IDs must be present and unique")
    if len(orders) != len(set(orders)) or any(not isinstance(value, int) or value < 0 for value in orders):
        errors.append("milestone orders must be unique nonnegative integers")
    for item in milestones:
        if item.get("status") not in MILESTONE_STATUSES:
            errors.append(f"milestone {item.get('id')} has invalid status")
        evidence = item.get("evidence", [])
        if item.get("status") == "completed" and (not evidence or not all(evidence_valid(value) for value in evidence)):
            errors.append(f"completed milestone {item.get('id')} lacks authoritative evidence")
    completed_required_orders = sorted(
        item["order"] for item in milestones if item.get("required") and item.get("status") == "completed"
    )
    pending_required_orders = sorted(
        item["order"] for item in milestones if item.get("required") and item.get("status") == "pending"
    )
    if completed_required_orders and pending_required_orders and max(completed_required_orders) > min(pending_required_orders):
        errors.append("required milestones are non-monotonic: later completed before earlier pending")

    checkpoints = record["checkpoints"]
    if not isinstance(checkpoints, list):
        errors.append("checkpoints must be a list")
        checkpoints = []
    checkpoint_hashes: set[str] = set()
    milestone_set = set(ids)
    for index, checkpoint in enumerate(checkpoints):
        path = Path(str(checkpoint.get("path", "")))
        digest = checkpoint.get("sha256")
        if checkpoint.get("verified") is not True:
            errors.append(f"checkpoint {index} is not verified")
        if not path.is_file():
            errors.append(f"checkpoint {index} path does not exist")
        elif digest != sha256(path):
            errors.append(f"checkpoint {index} hash mismatch")
        if digest in checkpoint_hashes:
            errors.append(f"duplicate checkpoint SHA-256 {digest}")
        checkpoint_hashes.add(digest)
        completed_ids = checkpoint.get("completed_milestones", [])
        if not set(completed_ids).issubset(milestone_set):
            errors.append(f"checkpoint {index} references unknown milestones")
        expected_rank = rank_for(record, completed_ids)
        if checkpoint.get("rank") != expected_rank:
            errors.append(f"checkpoint {index} rank must be {expected_rank}")

    furthest_hash = record["furthest_checkpoint_sha256"]
    if checkpoints:
        matched = [item for item in checkpoints if item.get("sha256") == furthest_hash]
        if len(matched) != 1:
            errors.append("furthest_checkpoint_sha256 must select exactly one checkpoint")
        else:
            furthest = matched[0]
            furthest_set = set(furthest.get("completed_milestones", []))
            furthest_required = {item["id"] for item in milestones if item.get("required") and item["id"] in furthest_set}
            for candidate in checkpoints:
                candidate_set = set(candidate.get("completed_milestones", []))
                candidate_required = {item["id"] for item in milestones if item.get("required") and item["id"] in candidate_set}
                if furthest_required.issubset(candidate_required) and tuple(candidate["rank"]) > tuple(furthest["rank"]):
                    errors.append("furthest checkpoint is not maximal by verified progression")
    elif furthest_hash is not None:
        errors.append("furthest_checkpoint_sha256 must be null when no checkpoints exist")

    actions = record["next_actions"]
    if not isinstance(actions, list) or not actions:
        errors.append("next_actions must contain at least one action")
    else:
        priorities = [item.get("priority") for item in actions]
        if len(priorities) != len(set(priorities)) or any(not isinstance(value, int) or value < 1 for value in priorities):
            errors.append("next action priorities must be unique positive integers")
        if priorities != sorted(priorities):
            errors.append("next_actions must be sorted by priority")
        current_id = current.get("id") if isinstance(current, dict) else None
        for index, action in enumerate(actions):
            if action.get("advances_goal_id") != current_id:
                errors.append(f"next_actions[{index}] does not advance current goal")
            if action.get("kind") not in {"gameplay", "preparation", "observation", "tooling_repair"}:
                errors.append(f"next_actions[{index}] has invalid kind")
            for key in ("action", "precondition", "expected_postcondition"):
                if not str(action.get(key, "")).strip():
                    errors.append(f"next_actions[{index}].{key} must be non-empty")

    reconciled = record["last_reconciled"]
    if not str(reconciled.get("canonical_state_hash", "")).strip():
        warnings.append("ledger has not yet been reconciled with a canonical state hash")
    sources = reconciled.get("sources", [])
    if sources and not all(evidence_valid(item) for item in sources):
        errors.append("last_reconciled.sources contains non-authoritative evidence")
    return errors, warnings


def initialize(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "game": {"title": args.title, "version": args.version, "rom_sha256": args.rom_sha256},
        "global_goal": {
            "objective": args.global_objective,
            "status": "active",
            "completion_oracle": args.completion_oracle,
            "completion_verified": False,
            "evidence": [],
        },
        "current_goal": {
            "id": args.goal_id,
            "objective": args.goal_objective,
            "status": "active",
            "parent": "global_goal",
            "why_now": args.why_now,
            "created_at": now(),
            "completion_conditions": [{
                "claim": args.goal_condition,
                "oracle": args.goal_oracle,
                "satisfied": False,
                "evidence": [],
            }],
        },
        "goal_history": [],
        "milestones": [],
        "checkpoints": [],
        "furthest_checkpoint_sha256": None,
        "next_actions": [{
            "priority": 1,
            "kind": args.next_action_kind,
            "action": args.next_action,
            "advances_goal_id": args.goal_id,
            "precondition": args.next_precondition,
            "expected_postcondition": args.next_postcondition,
        }],
        "blockers": [],
        "last_reconciled": {"at": None, "canonical_state_hash": "", "sources": []},
    }


def record_checkpoint(record: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.checkpoint).expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"checkpoint does not exist: {path}")
    digest = sha256(path)
    if path.name in {"auto-forward-latest.state", "auto-forward-previous.state"}:
        immutable = path.parent / "progress-checkpoints" / f"{digest}.state"
        immutable.parent.mkdir(parents=True, exist_ok=True)
        if not immutable.exists():
            shutil.copyfile(path, immutable)
        if sha256(immutable) != digest:
            raise SystemExit(f"immutable checkpoint hash mismatch: {immutable}")
        path = immutable
    if any(item.get("sha256") == digest for item in record["checkpoints"]):
        raise SystemExit(f"checkpoint already recorded: {digest}")
    completed_ids = [item["id"] for item in record["milestones"] if item.get("status") == "completed"]
    checkpoint = {
        "path": str(path),
        "sha256": digest,
        "recorded_at": now(),
        "canonical_state_hash": args.state_hash,
        "map": [int(value) for value in args.map.split(",")],
        "completed_milestones": completed_ids,
        "rank": rank_for(record, completed_ids),
        "verified": True,
    }
    if len(checkpoint["map"]) != 4:
        raise SystemExit("--map must be group,number,x,y")
    record["checkpoints"].append(checkpoint)
    current = next((item for item in record["checkpoints"] if item.get("sha256") == record["furthest_checkpoint_sha256"]), None)
    if current is None:
        record["furthest_checkpoint_sha256"] = digest
    else:
        current_set = set(current["completed_milestones"])
        candidate_set = set(completed_ids)
        required_ids = {item["id"] for item in record["milestones"] if item.get("required")}
        current_required = current_set & required_ids
        candidate_required = candidate_set & required_ids
        if current_required.issubset(candidate_required) and (
            tuple(checkpoint["rank"]) > tuple(current["rank"])
            or (checkpoint["rank"] == current["rank"] and current_set < candidate_set)
        ):
            record["furthest_checkpoint_sha256"] = digest
    return checkpoint


def summary(record: dict[str, Any]) -> dict[str, Any]:
    furthest = next(
        (item for item in record["checkpoints"] if item.get("sha256") == record["furthest_checkpoint_sha256"]),
        None,
    )
    return {
        "global": record["global_goal"]["status"],
        "current_goal": {key: record["current_goal"].get(key) for key in ("id", "objective", "why_now")},
        "unsatisfied_conditions": [
            item["claim"] for item in record["current_goal"].get("completion_conditions", []) if not item.get("satisfied")
        ],
        "next_action": record["next_actions"][0] if record["next_actions"] else None,
        "completed_milestones": [item["id"] for item in record["milestones"] if item.get("status") == "completed"],
        "furthest_checkpoint": furthest,
        "blockers": record["blockers"],
        "last_reconciled": record["last_reconciled"],
    }


def reconcile(record: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """Attach one authoritative observation to the ledger."""
    if args.kind not in EVIDENCE_KINDS:
        raise SystemExit(f"unsupported authoritative evidence kind: {args.kind}")
    record["last_reconciled"] = {
        "at": now(),
        "canonical_state_hash": args.state_hash,
        "sources": [{"kind": args.kind, "source": args.source, "value": args.value}],
    }
    return record["last_reconciled"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("path")
    init.add_argument("--title", default="Pokemon Run & Bun")
    init.add_argument("--version", required=True)
    init.add_argument("--rom-sha256", required=True)
    init.add_argument("--global-objective", required=True)
    init.add_argument("--completion-oracle", required=True)
    init.add_argument("--goal-id", required=True)
    init.add_argument("--goal-objective", required=True)
    init.add_argument("--why-now", required=True)
    init.add_argument("--goal-condition", required=True)
    init.add_argument("--goal-oracle", required=True)
    init.add_argument("--next-action", required=True)
    init.add_argument("--next-action-kind", choices=("gameplay", "preparation", "observation", "tooling_repair"), default="gameplay")
    init.add_argument("--next-precondition", required=True)
    init.add_argument("--next-postcondition", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("path")
    show = sub.add_parser("summary")
    show.add_argument("path")
    reconcile_parser = sub.add_parser("reconcile")
    reconcile_parser.add_argument("ledger")
    reconcile_parser.add_argument("--state-hash", required=True)
    reconcile_parser.add_argument("--kind", choices=sorted(EVIDENCE_KINDS), required=True)
    reconcile_parser.add_argument("--source", required=True)
    reconcile_parser.add_argument("--value", required=True)
    checkpoint = sub.add_parser("record-checkpoint")
    checkpoint.add_argument("ledger")
    checkpoint.add_argument("checkpoint")
    checkpoint.add_argument("--state-hash", required=True)
    checkpoint.add_argument("--map", required=True)
    args = parser.parse_args()

    if args.command == "init":
        path = Path(args.path).expanduser().resolve()
        if path.exists():
            raise SystemExit(f"refusing to overwrite ledger: {path}")
        record = initialize(args)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        errors, warnings = validate(record)
        print(json.dumps({"path": str(path), "valid": not errors, "errors": errors, "warnings": warnings}, indent=2))
        raise SystemExit(1 if errors else 0)

    path = Path(args.path if args.command in {"validate", "summary"} else args.ledger).expanduser().resolve()
    record = load(path)
    if args.command == "reconcile":
        observation = reconcile(record, args)
        errors, warnings = validate(record)
        if errors:
            print(json.dumps({"valid": False, "errors": errors, "warnings": warnings}, indent=2))
            raise SystemExit(1)
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"reconciled": observation}, indent=2))
        return
    if args.command == "record-checkpoint":
        added = record_checkpoint(record, args)
        errors, warnings = validate(record)
        if errors:
            print(json.dumps({"valid": False, "errors": errors, "warnings": warnings}, indent=2))
            raise SystemExit(1)
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"recorded": added, "furthest": record["furthest_checkpoint_sha256"]}, indent=2))
        return
    if args.command == "summary":
        print(json.dumps(summary(record), separators=(",", ":")))
        return
    errors, warnings = validate(record)
    print(json.dumps({"path": str(path), "valid": not errors, "errors": errors, "warnings": warnings}, indent=2))
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
