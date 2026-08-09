"""Postmortems, bounded counterfactual queues, and clone qualification."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
REVIEW_PATH = ROOT / "runtime" / "session" / "battle_reviews.jsonl"
QUALIFICATION_PATH = ROOT / "runtime" / "session" / "policy_qualification.json"
MAX_COUNTERFACTUALS = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _review_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def bounded_counterfactuals(
    transitions: Iterable[dict[str, Any]],
    *,
    limit: int = MAX_COUNTERFACTUALS,
) -> list[dict[str, Any]]:
    """Return at most one replay task per state/action pair.

    This is intentionally a queue, not a search tree. The runner can replay
    each task once from its saved pre-action state when the evidence says the
    alternative could change the decision.
    """
    tasks: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for transition in transitions:
        decision = transition.get("policy_decision") or {}
        uncertainties = ((decision.get("uncertainties") or {}).get("action_changing") or [])
        candidates = decision.get("candidates") or []
        selected = decision.get("action") or (decision.get("selected") or {}).get("action")
        if not uncertainties or not selected:
            continue
        for candidate in candidates:
            action = candidate.get("action") or {}
            if action == selected:
                continue
            state_hash = transition.get("pre_state_hash")
            action_key = json.dumps(action, sort_keys=True, separators=(",", ":"))
            marker = (str(state_hash), action_key)
            if marker in seen:
                continue
            seen.add(marker)
            tasks.append({
                "state_hash": state_hash,
                "state_path": transition.get("pre_state_path"),
                "action": action,
                "reason": uncertainties[0],
                "max_replays": 1,
            })
            if len(tasks) >= limit:
                return tasks
    return tasks


def _finding(kind: str, summary: str, transition: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    result = {"kind": kind, "summary": summary, **extra}
    if transition:
        result["state_hash"] = transition.get("pre_state_hash")
        result["action_id"] = transition.get("action_id")
    return result


def _improvement_layer(kind: str) -> str:
    return {
        "tooling_mismatch": "shared_observation_execution_tooling",
        "unexpected_action_outcome": "shared_observation_execution_tooling",
        "suboptimal_action": "generic_tactical_scorer",
        "action_changing_uncertainty": "bounded_counterfactual_or_scorer",
        "policy_gap": "reusable_executable_strategy",
        "tactical_error": "reusable_executable_strategy",
        "preparation_team_failure": "hard_fight_preparation",
    }.get(kind, "battle_policy_review")


def _postmortem(
    transitions: list[dict[str, Any]],
    *,
    terminal: str,
    findings: list[dict[str, Any]],
    counterfactuals: list[dict[str, Any]],
    status: str,
) -> dict[str, Any]:
    """Produce the compact human/audit list required before another clone."""
    comparisons = []
    for transition in transitions:
        actual = transition.get("actual") or {}
        decision = transition.get("policy_decision") or {}
        comparisons.append({
            "action_id": transition.get("action_id"),
            "predicted_action": decision.get("action"),
            "verified_action": transition.get("action"),
            "verified": transition.get("verified") is True,
            "actual_outcome": actual.get("allied_action_outcome"),
            "enemy_move_id": actual.get("enemy_move_id"),
            "allied_pp_deltas": actual.get("allied_pp_deltas", []),
            "opponent_pp_deltas": actual.get("opponent_pp_deltas", []),
            "discrepancies": transition.get("discrepancies", []),
        })
    improvements = [
        {
            "kind": finding.get("kind"),
            "summary": finding.get("summary"),
            "layer": _improvement_layer(str(finding.get("kind"))),
            "state_hash": finding.get("state_hash"),
            "action_id": finding.get("action_id"),
        }
        for finding in findings
    ]
    if counterfactuals:
        improvements.append({
            "kind": "bounded_counterfactual_review",
            "summary": f"replay {len(counterfactuals)} materially plausible alternative action(s) once before retry",
            "layer": "bounded_counterfactual_or_scorer",
        })
    if not improvements and terminal == "win" and status == "clean":
        improvements.append({
            "kind": "no_action_changing_defect",
            "summary": "no verified action-changing problem; retain the unchanged bundle for qualification",
            "layer": "none",
        })
    return {
        "terminal_authoritative": terminal in {"win", "loss"},
        "what_went_wrong": findings,
        "what_could_improve": improvements,
        "prediction_comparisons": comparisons,
        "next_step": "apply every action-changing improvement before retry" if improvements and not (len(improvements) == 1 and improvements[0]["kind"] == "no_action_changing_defect") else "qualify unchanged bundle",
    }


def review_episode(
    transitions: list[dict[str, Any]],
    *,
    terminal: str,
    source: str,
    opening_state_hash: str | None,
    behavior_hash: str | None,
    policy_id: str | None,
    trainer_key: str | None = None,
    profile_strategy_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Compare every verified transition with its prediction before retry."""
    findings: list[dict[str, Any]] = []
    for transition in transitions:
        discrepancies = transition.get("discrepancies") or []
        if discrepancies:
            findings.append(_finding(
                "tooling_mismatch",
                "; ".join(str(item) for item in discrepancies),
                transition,
                action_changing=True,
            ))
        decision = transition.get("policy_decision") or {}
        uncertainties = ((decision.get("uncertainties") or {}).get("action_changing") or [])
        if uncertainties:
            findings.append(_finding(
                "action_changing_uncertainty",
                "; ".join(str(item) for item in uncertainties),
                transition,
                action_changing=True,
            ))
        candidates = decision.get("candidates") or []
        selected = decision.get("selected") or {}
        selected_score = selected.get("score")
        dominated = [
            candidate for candidate in candidates
            if candidate.get("action") != selected.get("action")
            and isinstance(candidate.get("score"), list)
            and isinstance(selected_score, list)
            and candidate["score"] > selected_score
        ]
        if dominated:
            findings.append(_finding(
                "suboptimal_action",
                "a legal alternative has a verified higher lexicographic score",
                transition,
                action_changing=True,
                alternative=dominated[0].get("action"),
                dominance="certificate_rank",
            ))
        actual = transition.get("actual") or {}
        if actual.get("allied_action_outcome") not in {None, "executed", "interrupted_before_execution", "prevented_by_status"}:
            findings.append(_finding("unexpected_action_outcome", str(actual.get("allied_action_outcome")), transition, action_changing=True))

    if terminal == "policy_gap":
        findings.append(_finding("policy_gap", "the declarative profile had no safe legal action", action_changing=True))
    if terminal == "step_mismatch" and not any(item["kind"] == "tooling_mismatch" for item in findings):
        findings.append(_finding("tooling_mismatch", "battle step verification failed", action_changing=True))
    if terminal == "loss" and not findings:
        kind = "tactical_error" if any(item.get("policy_decision") for item in transitions) else "preparation_team_failure"
        findings.append(_finding(kind, "the verified party and policy did not produce a terminal win", transitions[-1] if transitions else None, action_changing=True))

    counterfactuals = bounded_counterfactuals(transitions)
    blocking = [item for item in findings if item.get("action_changing")]
    status = "clean" if terminal == "win" and not blocking and not counterfactuals else "needs_improvement"
    if terminal == "loss" and findings:
        classification = findings[0]["kind"]
    elif terminal == "win" and blocking:
        classification = blocking[0]["kind"]
    else:
        classification = "verified_win" if terminal == "win" else "unresolved"
    matched_strategy_ids = set(profile_strategy_ids or [])
    applied_ids = {
        str(item).split("strategy:", 1)[1]
        for transition in transitions
        for item in (transition.get("policy_decision") or {}).get("applied_strategy_ids", [])
        if str(item).startswith("strategy:")
    }
    if profile_strategy_ids is not None:
        matched_strategy_ids = matched_strategy_ids & applied_ids
    postmortem = _postmortem(
        transitions,
        terminal=terminal,
        findings=findings,
        counterfactuals=counterfactuals,
        status=status,
    )
    payload = {
        "schema_version": 2,
        "created_at": _now(),
        "terminal": terminal,
        "source": source,
        "opening_state_hash": opening_state_hash,
        "behavior_hash": behavior_hash,
        "policy_id": policy_id,
        "trainer_key": trainer_key,
        "matched_strategy_ids": sorted(matched_strategy_ids),
        "certified_actions": len(transitions),
        "findings": findings,
        "postmortem": postmortem,
        "counterfactuals": counterfactuals,
        "status": status,
        "classification": classification,
    }
    payload["review_id"] = _review_id(payload)
    return payload


def persist_review(review: dict[str, Any], path: str | Path = REVIEW_PATH) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(review, separators=(",", ":"), ensure_ascii=False) + "\n")


class QualificationLedger:
    """Persist the exact three-clean-clone streak and reset reasons."""

    def __init__(self, path: str | Path = QUALIFICATION_PATH) -> None:
        self.path = Path(path)
        self.state = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"streak": 0, "ready": False, "attempts": []}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"streak": 0, "ready": False, "attempts": []}
        except json.JSONDecodeError:
            return {"streak": 0, "ready": False, "attempts": []}

    def record(self, review: dict[str, Any]) -> dict[str, Any]:
        previous = self.state
        same_bundle = previous.get("behavior_hash") == review.get("behavior_hash")
        same_opening = previous.get("opening_state_hash") == review.get("opening_state_hash")
        clean = review.get("terminal") == "win" and review.get("status") == "clean" and review.get("source") == "cartridge_clone"
        if clean and same_bundle and same_opening:
            streak = int(previous.get("streak", 0)) + 1
        elif clean:
            streak = 1
        else:
            streak = 0
        self.state = {
            "schema_version": 1,
            "streak": streak,
            "ready": streak >= 3,
            "behavior_hash": review.get("behavior_hash"),
            "opening_state_hash": review.get("opening_state_hash"),
            "last_review_id": review.get("review_id"),
            "last_terminal": review.get("terminal"),
            "last_status": review.get("status"),
            "reset_reason": None if clean else review.get("classification"),
            "attempts": [*(previous.get("attempts") or [])[-9:], {
                "review_id": review.get("review_id"),
                "terminal": review.get("terminal"),
                "status": review.get("status"),
                "behavior_hash": review.get("behavior_hash"),
                "opening_state_hash": review.get("opening_state_hash"),
                "streak": streak,
            }],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return dict(self.state)
