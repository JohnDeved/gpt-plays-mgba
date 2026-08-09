#!/usr/bin/env python3
"""Validate and query the small append-by-upsert Run & Bun strategy toolbelt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PATH = Path(__file__).resolve().parents[1] / "references" / "strategies.json"
STATUSES = ("candidate", "tested", "exact_proven", "reusable_proven", "retired")
REQUIRED = ("id", "title", "status", "tags", "intent", "scope", "requirements", "line", "blockers", "abort_rules", "evidence")
PREDICATES = {
    "active_role", "opponent_species", "forced_switch", "fresh_entry",
    "player_hp_lte", "player_hp_gte", "opponent_hp_lte", "opponent_hp_gte",
    "player_status_any", "role_available", "action_count",
}


def load() -> dict:
    return json.loads(PATH.read_text(encoding="utf-8"))


def validate(data: dict) -> list[str]:
    errors = []
    if data.get("schema_version") not in {1, 2}:
        errors.append("schema_version must be 1 or 2")
    if len(data.get("rom_sha256", "")) != 64:
        errors.append("rom_sha256 must be a 64-character hash")
    seen = set()
    for index, strategy in enumerate(data.get("strategies", [])):
        prefix = f"strategies[{index}]"
        for field in REQUIRED:
            if field not in strategy:
                errors.append(f"{prefix} missing {field}")
        sid = strategy.get("id")
        if sid in seen:
            errors.append(f"duplicate strategy id {sid}")
        seen.add(sid)
        if strategy.get("status") not in STATUSES:
            errors.append(f"{prefix} invalid status")
        executable = strategy.get("executable")
        if executable is not None:
            if not isinstance(executable, dict):
                errors.append(f"{prefix}.executable must be an object")
            else:
                blocks = executable.get("rules")
                if blocks is None:
                    blocks = [executable]
                if not isinstance(blocks, list) or not blocks:
                    errors.append(f"{prefix}.executable.rules must be a non-empty array")
                    blocks = []
                for block_index, block in enumerate(blocks):
                    block_prefix = f"{prefix}.executable.rules[{block_index}]"
                    if not isinstance(block, dict):
                        errors.append(f"{block_prefix} must be an object")
                        continue
                    when = block.get("when", {})
                    if not isinstance(when, dict):
                        errors.append(f"{block_prefix}.when must be an object")
                    else:
                        for key in sorted(set(when) - PREDICATES):
                            errors.append(f"{block_prefix}.when.{key} is unsupported")
                    directives = block.get("directives", [])
                    if not isinstance(directives, list) or not directives:
                        errors.append(f"{block_prefix}.directives must be a non-empty array")
                    else:
                        for directive in directives:
                            if not isinstance(directive, dict) or directive.get("kind") not in {"prefer", "forbid", "reserve"}:
                                errors.append(f"{block_prefix} has unsupported directive")
        evidence = strategy.get("evidence", {})
        for field in ("reproductions", "exhaustive_searches", "distinct_state_hashes", "trainer_keys", "counterexamples"):
            if not isinstance(evidence.get(field), list):
                errors.append(f"{prefix}.evidence.{field} must be a list")
    return errors


def eligible_status(strategy: dict) -> str:
    if strategy.get("status") == "retired":
        return "retired"
    evidence = strategy["evidence"]
    if evidence["counterexamples"]:
        return "candidate"
    wins = [item for item in evidence["reproductions"] if item.get("terminal_win")]
    exact = any(
        item.get("terminal_win") and item.get("legal_actions_complete") and item.get("rng_ai_complete")
        for item in evidence["exhaustive_searches"]
    )
    if exact and len(wins) >= 2:
        if len(set(evidence["distinct_state_hashes"])) >= 3 and len(set(evidence["trainer_keys"])) >= 2:
            return "reusable_proven"
        return "exact_proven"
    return "tested" if wins else "candidate"


def save(data: dict) -> None:
    errors = validate(data)
    if errors:
        raise ValueError("; ".join(errors))
    PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def record_review(data: dict, review: dict, strategy_ids: list[str]) -> None:
    """Merge one concise episode review without rewriting executable policy."""
    selected = set(review["matched_strategy_ids"]) if "matched_strategy_ids" in review else set(strategy_ids)
    for strategy in data["strategies"]:
        if strategy.get("id") not in selected:
            continue
        evidence = strategy.setdefault("evidence", {})
        for field in ("reproductions", "exhaustive_searches", "distinct_state_hashes", "trainer_keys", "counterexamples"):
            evidence.setdefault(field, [])
        if review.get("terminal") == "win":
            evidence["reproductions"].append({
                "source": review.get("source", "cartridge_clone"),
                "pre_state_hash": review.get("opening_state_hash"),
                "terminal_win": True,
                "clean_review": review.get("status") == "clean",
                "behavior_hash": review.get("behavior_hash"),
                "certified_actions": review.get("certified_actions"),
            })
        for finding in review.get("findings", []):
            if finding.get("strategy_id") not in {None, strategy.get("id")}:
                continue
            counterexample = {
                "review_id": review.get("review_id"),
                "kind": finding.get("kind"),
                "summary": finding.get("summary"),
                "state_hash": finding.get("state_hash"),
                "trainer_key": review.get("trainer_key"),
            }
            if counterexample not in evidence["counterexamples"]:
                evidence["counterexamples"].append(counterexample)
        if review.get("opening_state_hash") and review["opening_state_hash"] not in evidence["distinct_state_hashes"]:
            evidence["distinct_state_hashes"].append(review["opening_state_hash"])
        if review.get("trainer_key") and review["trainer_key"] not in evidence["trainer_keys"]:
            evidence["trainer_keys"].append(review["trainer_key"])
        if strategy.get("status") != "retired":
            strategy["status"] = eligible_status(strategy)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    query = sub.add_parser("query")
    query.add_argument("--tag")
    query.add_argument("--status", choices=STATUSES)
    upsert = sub.add_parser("upsert")
    upsert.add_argument("record", type=Path)
    review = sub.add_parser("record-review")
    review.add_argument("review", type=Path)
    review.add_argument("--strategy-id", action="append", default=[])
    promote = sub.add_parser("promote")
    promote.add_argument("id")
    retire = sub.add_parser("retire")
    retire.add_argument("id")
    args = parser.parse_args()
    data = load()
    if args.command == "validate":
        errors = validate(data)
        print(json.dumps({"valid": not errors, "errors": errors}, separators=(",", ":")))
        return bool(errors)
    if args.command == "query":
        rows = [
            item for item in data["strategies"]
            if (not args.tag or args.tag in item["tags"])
            and (not args.status or args.status == item["status"])
        ]
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    if args.command == "upsert":
        record = json.loads(args.record.read_text(encoding="utf-8"))
        rows = [item for item in data["strategies"] if item["id"] != record.get("id")]
        data["strategies"] = sorted(rows + [record], key=lambda item: item["id"])
    elif args.command == "record-review":
        review = json.loads(args.review.read_text(encoding="utf-8"))
        record_review(data, review, args.strategy_id)
    else:
        strategy = next((item for item in data["strategies"] if item["id"] == args.id), None)
        if strategy is None:
            raise ValueError(f"unknown strategy: {args.id}")
        strategy["status"] = "retired" if args.command == "retire" else eligible_status(strategy)
    save(data)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}, separators=(",", ":")), file=sys.stderr)
        raise SystemExit(2)
