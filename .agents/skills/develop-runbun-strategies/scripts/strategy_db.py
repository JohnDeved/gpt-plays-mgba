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


def load() -> dict:
    return json.loads(PATH.read_text(encoding="utf-8"))


def validate(data: dict) -> list[str]:
    errors = []
    if data.get("schema_version") != 1:
        errors.append("schema_version must be 1")
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


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    query = sub.add_parser("query")
    query.add_argument("--tag")
    query.add_argument("--status", choices=STATUSES)
    upsert = sub.add_parser("upsert")
    upsert.add_argument("record", type=Path)
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
