#!/usr/bin/env python3
"""Validate and query the small append-by-upsert Run & Bun strategy toolbelt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PATH = Path(__file__).resolve().parents[1] / "references" / "strategies.json"
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from games.run_and_bun.battle_policy import PREBATTLE_PREDICATES, SUPPORTED_PREDICATES, prebattle_strategy_applicable, strategy_applicable, validate_auto_match
from games.run_and_bun.battle_review import agent_review_complete, unresolved_attached_findings

STATUSES = ("candidate", "tested", "reusable_tested", "exact_proven", "reusable_proven", "retired")
REVIEW_DIR = ROOT / "runtime" / "session" / "battle_reviews"
REQUIRED = ("id", "title", "status", "tags", "intent", "scope", "requirements", "line", "blockers", "abort_rules", "evidence")
PREDICATES = set(SUPPORTED_PREDICATES)


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
                            elif directive.get("kind") == "reserve" and not isinstance(directive.get("role"), str):
                                errors.append(f"{block_prefix} reserve directive needs a role")
        auto_match = strategy.get("auto_match")
        if auto_match is not None:
            errors.extend(validate_auto_match(auto_match, f"{prefix}.auto_match"))
        preconditions = strategy.get("preconditions", {})
        if not isinstance(preconditions, dict):
            errors.append(f"{prefix}.preconditions must be an object")
        else:
            for key in sorted(set(preconditions) - set(PREBATTLE_PREDICATES)):
                errors.append(f"{prefix}.preconditions.{key} is unsupported")
        evidence = strategy.get("evidence", {})
        for field in ("reproductions", "exhaustive_searches", "distinct_state_hashes", "trainer_keys", "counterexamples"):
            if not isinstance(evidence.get(field), list):
                errors.append(f"{prefix}.evidence.{field} must be a list")
    return errors


def eligible_status(strategy: dict) -> str:
    if strategy.get("status") == "retired":
        return "retired"
    evidence = strategy["evidence"]
    if any(
        item.get("requirements_matched") is True
        and not item.get("resolved")
        and not item.get("superseded")
        for item in evidence["counterexamples"]
    ):
        return "candidate"
    wins = [
        item for item in evidence["reproductions"]
        if item.get("terminal_win") and item.get("clean_review")
    ]
    exact = any(
        item.get("terminal_win") and item.get("legal_actions_complete") and item.get("rng_ai_complete")
        for item in evidence["exhaustive_searches"]
    )
    wins_by_trainer: dict[str, int] = {}
    for item in wins:
        trainer = item.get("trainer_key")
        if trainer:
            wins_by_trainer[str(trainer)] = wins_by_trainer.get(str(trainer), 0) + 1
    cross_trainer = sum(count >= 3 for count in wins_by_trainer.values()) >= 2
    if exact and len(wins) >= 2:
        if cross_trainer and len(set(evidence["distinct_state_hashes"])) >= 3:
            return "reusable_proven"
        return "exact_proven"
    if cross_trainer:
        return "reusable_tested"
    return "tested" if wins else "candidate"


def summarize(data: dict) -> dict:
    rows = []
    status_counts = {status: 0 for status in STATUSES}
    for strategy in data.get("strategies", []):
        status = strategy.get("status")
        if status in status_counts:
            status_counts[status] += 1
        evidence = strategy.get("evidence") or {}
        rows.append({
            "id": strategy.get("id"),
            "status": status,
            "eligible_status": eligible_status(strategy),
            "automatic": status in {"reusable_tested", "reusable_proven"} and bool(strategy.get("auto_match")),
            "reproductions": len(evidence.get("reproductions") or []),
            "distinct_states": len(set(evidence.get("distinct_state_hashes") or [])),
            "trainers": len(set(evidence.get("trainer_keys") or [])),
            "unresolved_counterexamples": sum(
                item.get("requirements_matched") is True
                and not item.get("resolved")
                and not item.get("superseded")
                for item in evidence.get("counterexamples") or []
            ),
        })
    return {
        "strategy_count": len(rows),
        "status_counts": status_counts,
        "automatic_reusable_count": sum(item["automatic"] for item in rows),
        "strategies": rows,
    }


def system_health(data: dict, review_dir: Path = REVIEW_DIR) -> dict:
    """Audit durable learning without creating a second progress ledger."""
    reviews, invalid = [], []
    for path in sorted(review_dir.glob("*.json")):
        try:
            review = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            invalid.append({"path": str(path), "error": str(error)})
            continue
        reviews.append(review)

    latest: dict[str, dict] = {}
    for review in reviews:
        trainer = review.get("trainer_key")
        if trainer and str(review.get("created_at", "")) >= str(latest.get(trainer, {}).get("created_at", "")):
            latest[trainer] = review
    latest_blockers = [
        {
            "trainer_key": trainer,
            "review_id": review.get("review_id"),
            "agent_review_complete": agent_review_complete(review),
            "unresolved_findings": len(unresolved_attached_findings(review)),
        }
        for trainer, review in sorted(latest.items())
        if not agent_review_complete(review) or unresolved_attached_findings(review)
    ]
    findings = [
        finding
        for review in reviews
        for finding in (review.get("agent_review") or {}).get("findings", [])
        if isinstance(finding, dict)
    ]
    by_target: dict[str, int] = {}
    for finding in findings:
        target = str(finding.get("knowledge_target") or "legacy_unclassified")
        by_target[target] = by_target.get(target, 0) + 1

    strategies = summarize(data)
    promotion_drift = [
        row["id"] for row in strategies["strategies"]
        if row["status"] != row["eligible_status"] and row["status"] != "retired"
    ]
    cross_trainer_gaps = [
        row["id"] for row in strategies["strategies"]
        if row["status"] == "tested" and row["trainers"] < 2
    ]
    priorities = []
    if invalid:
        priorities.append({"severity": "critical", "kind": "invalid_review_json", "count": len(invalid)})
    if latest_blockers:
        priorities.append({"severity": "high", "kind": "latest_review_gate_blocked", "count": len(latest_blockers)})
    if promotion_drift:
        priorities.append({"severity": "high", "kind": "strategy_promotion_drift", "strategy_ids": promotion_drift})
    if cross_trainer_gaps:
        priorities.append({"severity": "medium", "kind": "cross_trainer_evidence_gap", "strategy_ids": cross_trainer_gaps})
    if not strategies["automatic_reusable_count"]:
        priorities.append({"severity": "info", "kind": "no_automatic_reusable_strategy"})
    return {
        "healthy_to_continue": not invalid and not latest_blockers and not promotion_drift,
        "reviews": {
            "total": len(reviews),
            "agent_complete": sum(agent_review_complete(review) for review in reviews),
            "legacy_or_incomplete": sum(not agent_review_complete(review) for review in reviews),
            "latest_trainer_gates": len(latest),
            "latest_blockers": latest_blockers,
            "invalid": invalid,
            "finding_targets": dict(sorted(by_target.items())),
        },
        "strategies": strategies,
        "priorities": priorities,
    }


def save(data: dict) -> None:
    errors = validate(data)
    if errors:
        raise ValueError("; ".join(errors))
    PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def record_review(data: dict, review: dict, strategy_ids: list[str]) -> None:
    """Merge one concise episode review without rewriting executable policy."""
    selected = set(review.get("influential_strategy_ids", review.get("matched_strategy_ids", strategy_ids)))
    for strategy in data["strategies"]:
        if strategy.get("id") not in selected:
            continue
        evidence = strategy.setdefault("evidence", {})
        for field in ("reproductions", "exhaustive_searches", "distinct_state_hashes", "trainer_keys", "counterexamples"):
            evidence.setdefault(field, [])
        agent = review.get("agent_review") or {}
        clean = (
            review.get("terminal") == "win"
            and review.get("status") == "clean"
            and agent.get("status") == "complete"
            and agent.get("author") == "agent"
        )
        reproduction = {
            "review_id": review.get("review_id"),
            "source": review.get("source", "cartridge_clone"),
            "pre_state_hash": review.get("opening_state_hash"),
            "terminal_win": True,
            "clean_review": True,
            "behavior_hash": review.get("behavior_hash"),
            "certified_actions": review.get("certified_actions"),
            "trainer_key": review.get("trainer_key"),
        }
        if clean and not any(item.get("review_id") == review.get("review_id") for item in evidence["reproductions"]):
            evidence["reproductions"].append(reproduction)
        findings = (review.get("agent_review") or {}).get("findings", review.get("findings", []))
        for finding in findings:
            if (
                finding.get("strategy_id") != strategy.get("id")
                or finding.get("requirements_matched") is not True
                or finding.get("action_changing") is not True
            ):
                continue
            counterexample = {
                "review_id": review.get("review_id"),
                "kind": finding.get("kind"),
                "summary": finding.get("summary"),
                "state_hash": finding.get("state_hash"),
                "trainer_key": review.get("trainer_key"),
                "behavior_hash": review.get("behavior_hash"),
                "requirements_matched": True,
                "resolved": finding.get("resolved", False),
            }
            existing = next((item for item in evidence["counterexamples"] if
                item.get("review_id") == counterexample["review_id"]
                and item.get("kind") == counterexample["kind"]
            ), None)
            if existing is None:
                evidence["counterexamples"].append(counterexample)
            else:
                existing.update(counterexample)
        if clean and review.get("opening_state_hash") and review["opening_state_hash"] not in evidence["distinct_state_hashes"]:
            evidence["distinct_state_hashes"].append(review["opening_state_hash"])
        if clean and review.get("trainer_key") and review["trainer_key"] not in evidence["trainer_keys"]:
            evidence["trainer_keys"].append(review["trainer_key"])
        if strategy.get("status") != "retired":
            strategy["status"] = eligible_status(strategy)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    sub.add_parser("summary")
    sub.add_parser("health")
    query = sub.add_parser("query")
    query.add_argument("--tag")
    query.add_argument("--status", choices=STATUSES)
    match = sub.add_parser("match")
    match.add_argument("context", type=Path)
    match.add_argument("--mode", choices=("clone_trial", "blind_live"))
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
    if args.command == "summary":
        print(json.dumps(summarize(data), indent=2, ensure_ascii=False))
        return 0
    if args.command == "health":
        health = system_health(data)
        print(json.dumps(health, indent=2, ensure_ascii=False))
        return 0 if health["healthy_to_continue"] else 2
    if args.command == "query":
        rows = [
            item for item in data["strategies"]
            if (not args.tag or args.tag in item["tags"])
            and (not args.status or args.status == item["status"])
        ]
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    if args.command == "match":
        context = json.loads(args.context.read_text(encoding="utf-8"))
        if args.mode:
            rows = [item for item in data["strategies"] if prebattle_strategy_applicable(item, context)]
            if args.mode == "blind_live":
                rows = [item for item in rows if item.get("status") in {"reusable_tested", "exact_proven", "reusable_proven"}]
            print(json.dumps({
                "mode": args.mode,
                "selected": [item["id"] for item in rows],
                "soft": [item["id"] for item in rows if item.get("status") == "reusable_tested"],
                "full": [item["id"] for item in rows if item.get("status") in {"exact_proven", "reusable_proven"}],
            }, indent=2, ensure_ascii=False))
        else:
            rows = [item for item in data["strategies"] if strategy_applicable(item, context)]
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
