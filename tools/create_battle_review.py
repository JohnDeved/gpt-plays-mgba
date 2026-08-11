#!/usr/bin/env python3
"""Create one compact agent-authored battle review JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from games.run_and_bun.battle_review import (
    DEFAULT_KNOWLEDGE_TARGET,
    KNOWLEDGE_TARGETS,
    normalize_finding_kind,
    write_review_artifact,
)

ACTION_CHANGING_KINDS = {
    "tooling_mismatch",
    "prediction_gap",
    "policy_gap",
    "tactical_error",
    "preparation_failure",
    "terminal_loss",
    "suboptimal_action",
    "strategy_conflict",
    "safety_risk",
}
FIX_LAYER_BY_TARGET = {
    "tooling": "shared_observation_execution_tooling",
    "mechanics": "shared_battle_mechanics",
    "scorer": "generic_tactical_scorer",
    "strategy": "reusable_executable_strategy",
    "profile": "trainer_profile",
    "preparation": "hard_fight_preparation",
    "review": "battle_policy_review",
}


def _review_id(review: dict[str, Any]) -> str:
    payload = json.dumps(review, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def build_review(
    *,
    terminal: str,
    source: str,
    opening_state_hash: str,
    policy_id: str,
    certified_actions: int,
    findings: list[dict[str, Any]],
    correct_choices: list[str],
    next_test: str,
    behavior_hash: str | None = None,
    trainer_key: str | None = None,
) -> dict[str, Any]:
    if terminal not in {"win", "loss"}:
        raise ValueError("terminal must be win or loss")
    if certified_actions < 0:
        raise ValueError("certified_actions must be non-negative")
    if not next_test.strip():
        raise ValueError("next_test must be non-empty")
    findings = [_normalize_finding(item, next_test) for item in findings]
    clean = terminal == "win" and not any(item.get("action_changing") for item in findings)
    review: dict[str, Any] = {
        "schema_version": 3,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "terminal": terminal,
        "source": source,
        "opening_state_hash": opening_state_hash,
        "behavior_hash": behavior_hash,
        "policy_id": policy_id,
        "trainer_key": trainer_key,
        "matched_strategy_ids": [],
        "certified_actions": certified_actions,
        "status": "clean" if clean else "needs_improvement",
        "classification": "verified_win" if clean else (findings[0].get("kind") if findings else "terminal_loss"),
        "agent_review": {
            "schema_version": 3,
            "status": "complete",
            "author": "agent",
            "reviewed_actions": certified_actions,
            "findings": findings,
            "correct_choices": correct_choices,
            "next_test": next_test,
        },
    }
    review["review_id"] = _review_id(review)
    return review


def complete_review(
    review: dict[str, Any],
    *,
    findings: list[dict[str, Any]],
    correct_choices: list[str],
    next_test: str,
) -> dict[str, Any]:
    """Complete the compact draft emitted by the battle runner."""
    if not next_test.strip():
        raise ValueError("next_test must be non-empty")
    findings = [_normalize_finding(item, next_test) for item in findings]
    review["agent_review"] = {
        "schema_version": 3,
        "status": "complete",
        "author": "agent",
        "reviewed_actions": review.get("certified_actions"),
        "findings": findings,
        "correct_choices": correct_choices,
        "next_test": next_test,
    }
    clean = review.get("terminal") == "win" and not any(item.get("action_changing") for item in findings)
    review["status"] = "clean" if clean else "needs_improvement"
    review["classification"] = "verified_win" if clean else (findings[0].get("kind") if findings else "terminal_loss")
    return review


def resolve_findings(review: dict[str, Any], resolutions: list[list[str]]) -> dict[str, Any]:
    """Attach verified replay evidence to uniquely named unresolved findings."""
    findings = (review.get("agent_review") or {}).get("findings", [])
    for kind, post_state_hash, observed in resolutions:
        matches = [item for item in findings if item.get("kind") == kind and item.get("resolved") is not True]
        if len(matches) != 1:
            raise ValueError(f"--resolve {kind!r} matched {len(matches)} unresolved findings")
        if not matches[0].get("state"):
            raise ValueError(f"--resolve {kind!r} requires an attached replay state")
        matches[0]["resolved"] = True
        matches[0]["resolution"] = {
            "verified": True,
            "post_state_hash": post_state_hash,
            "observed": observed,
        }
    return review


def _normalize_finding(item: dict[str, Any], next_test: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("each finding must be a JSON object")
    finding = dict(item)
    finding["kind"] = normalize_finding_kind(finding.get("kind"), strict=True)
    summary = str(finding.get("summary", "")).strip()
    if not summary:
        raise ValueError("each finding needs a summary")
    finding.setdefault("turns", [finding.get("action_id", "whole_fight")])
    finding.setdefault("observed_fact", summary)
    finding.setdefault("strategic_judgment", finding.get("why_bad") or finding.get("improvement") or summary)
    finding.setdefault("confidence", "heuristic")
    finding.setdefault("knowledge_target", DEFAULT_KNOWLEDGE_TARGET[finding["kind"]])
    if finding["knowledge_target"] not in KNOWLEDGE_TARGETS:
        raise ValueError(f"unsupported knowledge_target: {finding['knowledge_target']}")
    finding.setdefault("fix_layer", finding.get("root_layer", FIX_LAYER_BY_TARGET[finding["knowledge_target"]]))
    finding.setdefault("next_test", next_test)
    if finding["knowledge_target"] == "strategy" and not (finding.get("strategy_id") or finding.get("candidate_strategy_id")):
        raise ValueError("strategy findings need strategy_id or candidate_strategy_id")
    if finding["knowledge_target"] == "profile" and not str(finding.get("matchup_specific_reason", "")).strip():
        raise ValueError("profile findings need matchup_specific_reason")
    if finding.get("severity") in {"critical", "high"} and not (finding.get("state") or str(finding.get("state_unavailable_reason", "")).strip()):
        raise ValueError("critical/high findings need a replay state or state_unavailable_reason")
    return finding


def _finding(values: list[str]) -> dict[str, Any]:
    severity, kind, action_changing, summary = values
    kind = normalize_finding_kind(kind, strict=True)
    if severity not in {"critical", "high", "medium", "low", "info"}:
        raise ValueError(f"invalid finding severity: {severity}")
    if action_changing.casefold() not in {"true", "false"}:
        raise ValueError("finding action_changing must be true or false")
    item = {
        "kind": kind,
        "severity": severity,
        "summary": summary,
        "action_changing": action_changing.casefold() == "true",
    }
    if kind in ACTION_CHANGING_KINDS and not item["action_changing"]:
        raise ValueError(f"{kind} findings must be marked action-changing")
    return item


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path, help="create one standalone review JSON")
    destination.add_argument("--update", type=Path, help="complete an existing runner review JSON")
    parser.add_argument("--terminal", choices=("win", "loss"))
    parser.add_argument("--source", choices=("live", "cartridge_clone"), default="live")
    parser.add_argument("--opening-state-hash")
    parser.add_argument("--policy-id")
    parser.add_argument("--trainer-key")
    parser.add_argument("--behavior-hash")
    parser.add_argument("--certified-actions", type=int)
    parser.add_argument(
        "--finding", nargs=4, action="append", metavar=("SEVERITY", "KIND", "ACTION_CHANGING", "SUMMARY"),
        default=[], help="repeat: severity kind true|false summary",
    )
    parser.add_argument("--finding-json", action="append", default=[], help="repeat: exact finding JSON object")
    parser.add_argument("--correct-choice", action="append", default=[])
    parser.add_argument("--next-test")
    parser.add_argument(
        "--resolve", nargs=3, action="append", default=[],
        metavar=("KIND", "POST_STATE_HASH", "OBSERVED"),
        help="resolve one uniquely named state-backed finding with verified replay evidence",
    )
    parser.add_argument("--no-record-strategies", action="store_true")
    args = parser.parse_args(argv)

    if args.output and args.output.exists():
        parser.error(f"refusing to overwrite existing review: {args.output}")
    try:
        if args.resolve:
            if not args.update or args.finding or args.finding_json or args.correct_choice:
                raise ValueError("--resolve is update-only and cannot replace review findings")
            review = resolve_findings(json.loads(args.update.read_text(encoding="utf-8")), args.resolve)
            output = args.update
        elif not args.next_test:
            raise ValueError("--next-test is required when creating or completing a review")
        else:
            findings = [_finding(values) for values in args.finding]
            findings.extend(json.loads(value) for value in args.finding_json)
            if args.update:
                review = complete_review(
                    json.loads(args.update.read_text(encoding="utf-8")),
                    findings=findings,
                    correct_choices=args.correct_choice,
                    next_test=args.next_test,
                )
                output = args.update
            else:
                missing = [name for name in ("terminal", "opening_state_hash", "policy_id", "certified_actions") if getattr(args, name) is None]
                if missing:
                    raise ValueError(f"new reviews require: {', '.join(missing)}")
                review = build_review(
                    terminal=args.terminal,
                    source=args.source,
                    opening_state_hash=args.opening_state_hash,
                    policy_id=args.policy_id,
                    trainer_key=args.trainer_key,
                    behavior_hash=args.behavior_hash,
                    certified_actions=args.certified_actions,
                    findings=findings,
                    correct_choices=args.correct_choice,
                    next_test=args.next_test,
                )
                output = args.output
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))

    # Reuse the repository's compacting and state-resolution behavior while
    # allowing the operator to choose a readable output filename.
    with tempfile.TemporaryDirectory(prefix="runbun-review-") as directory:
        compact = write_review_artifact(review, directory)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(compact, output)
    if review.get("matched_strategy_ids") and not args.no_record_strategies:
        subprocess.run([
            sys.executable,
            str(ROOT / ".agents/skills/develop-runbun-strategies/scripts/strategy_db.py"),
            "record-review",
            str(output),
        ], cwd=ROOT, check=True)
    print(json.dumps({"path": str(output.resolve()), "review_id": review["review_id"], "status": review["status"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
