"""Postmortems, bounded counterfactual queues, and clone qualification."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
REVIEW_DIR = ROOT / "runtime" / "session" / "battle_reviews"
REVIEW_STATE_DIR = REVIEW_DIR / "states"
QUALIFICATION_PATH = ROOT / "runtime" / "session" / "policy_qualification.json"
MAX_COUNTERFACTUALS = 3
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
RESOLUTION_SEVERITIES = {"critical", "high"}
KNOWLEDGE_TARGETS = {"tooling", "mechanics", "scorer", "strategy", "profile", "preparation", "review"}
CANONICAL_FINDING_KINDS = {
    "terminal_loss", "preparation_failure", "tooling_mismatch", "prediction_gap",
    "policy_gap", "tactical_error", "suboptimal_action", "reserve_misuse",
    "resource_loss", "tempo_cost", "verified_variance", "strategy_conflict", "safety_risk",
}
FINDING_KIND_ALIASES = {
    "preparation_team_failure": "preparation_failure",
    "unexpected_action_outcome": "prediction_gap",
    "action_changing_uncertainty": "prediction_gap",
    "observation_gap": "prediction_gap",
    "damage_model_uncertainty": "prediction_gap",
    "mechanics_uncertainty": "prediction_gap",
    "mechanics_gap": "prediction_gap",
    "unmodeled_ability_interaction": "prediction_gap",
    "strategy_profile_conflict": "strategy_conflict",
    "invalid_strategy_requirement": "strategy_conflict",
    "switch_spam": "tempo_cost",
    "switch_tempo": "tempo_cost",
    "switch_tempo_observation": "tempo_cost",
    "switch_tempo_review": "tempo_cost",
    "tempo": "tempo_cost",
    "tempo_observation": "tempo_cost",
    "tempo_improvement": "tempo_cost",
    "line_length": "tempo_cost",
    "long_line_observation": "tempo_cost",
    "resource_tempo": "resource_loss",
    "resource_tempo_summary": "resource_loss",
    "resource_tempo_observation": "resource_loss",
    "resource_attrition": "resource_loss",
    "reserve_attrition": "reserve_misuse",
    "status_and_reserve_misuse": "reserve_misuse",
    "critical_variance": "verified_variance",
    "status_variance": "verified_variance",
    "unsafe_switch": "safety_risk",
    "avoidable_attrition": "safety_risk",
}
DEFAULT_KNOWLEDGE_TARGET = {
    "terminal_loss": "review",
    "preparation_failure": "preparation",
    "tooling_mismatch": "tooling",
    "prediction_gap": "mechanics",
    "policy_gap": "strategy",
    "tactical_error": "scorer",
    "suboptimal_action": "scorer",
    "reserve_misuse": "scorer",
    "resource_loss": "scorer",
    "tempo_cost": "scorer",
    "verified_variance": "mechanics",
    "strategy_conflict": "strategy",
    "safety_risk": "scorer",
}
DEFAULT_SEVERITY = {
    "terminal_loss": "critical",
    "preparation_team_failure": "critical",
    "tooling_mismatch": "high",
    "unexpected_action_outcome": "high",
    "policy_gap": "high",
    "tactical_error": "high",
    "action_changing_uncertainty": "high",
    "suboptimal_action": "medium",
}


def normalize_finding_kind(kind: Any, *, strict: bool = False) -> str:
    value = str(kind or "").strip()
    normalized = FINDING_KIND_ALIASES.get(value, value)
    if normalized in CANONICAL_FINDING_KINDS:
        return normalized
    if strict:
        raise ValueError(f"unsupported finding kind: {value or '<missing>'}")
    return normalized or "unclassified_legacy"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _review_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def agent_review_complete(review: dict[str, Any]) -> bool:
    agent = review.get("agent_review") or {}
    basic = (
        agent.get("status") == "complete"
        and agent.get("author") == "agent"
        and agent.get("reviewed_actions") == review.get("certified_actions")
        and isinstance(agent.get("findings"), list)
        and isinstance(agent.get("correct_choices"), list)
        and bool(str(agent.get("next_test", "")).strip())
    )
    if not basic or int(agent.get("schema_version", 1)) < 2:
        return basic
    required = {
        "kind", "severity", "summary", "turns", "observed_fact",
        "strategic_judgment", "confidence", "fix_layer", "next_test",
    }
    for finding in agent["findings"]:
        if not isinstance(finding, dict) or required - finding.keys():
            return False
        if finding["severity"] not in SEVERITY_ORDER or finding["confidence"] not in {"verified", "bounded", "heuristic"}:
            return False
        if int(agent.get("schema_version", 1)) >= 3:
            if finding.get("kind") not in CANONICAL_FINDING_KINDS or finding.get("knowledge_target") not in KNOWLEDGE_TARGETS:
                return False
            if finding.get("knowledge_target") == "strategy" and not (finding.get("strategy_id") or finding.get("candidate_strategy_id")):
                return False
            if finding.get("knowledge_target") == "profile" and not str(finding.get("matchup_specific_reason", "")).strip():
                return False
        if finding["severity"] in RESOLUTION_SEVERITIES and _attached_state_path(finding) is None and not str(finding.get("state_unavailable_reason", "")).strip():
            return False
    return True


def _attached_state_path(finding: dict[str, Any]) -> Path | None:
    state = finding.get("state") or finding.get("state_path")
    if isinstance(state, dict):
        state = state.get("path")
    if not isinstance(state, str) or not state.strip():
        return None
    path = Path(state)
    return path if path.is_absolute() else ROOT / path


def _all_review_findings(review: dict[str, Any]) -> Iterable[dict[str, Any]]:
    yield from (finding for finding in review.get("findings") or [] if isinstance(finding, dict))
    yield from (finding for finding in (review.get("agent_review") or {}).get("findings") or [] if isinstance(finding, dict))


def attach_resolution_fields(review: dict[str, Any]) -> bool:
    """Add the default unresolved marker whenever an agent finding has a state."""
    changed = False
    for finding in _all_review_findings(review):
        if _attached_state_path(finding) is not None and "resolved" not in finding:
            finding["resolved"] = False
            changed = True
    return changed


def unresolved_attached_findings(review: dict[str, Any]) -> list[dict[str, Any]]:
    unresolved: list[dict[str, Any]] = []
    for finding in _all_review_findings(review):
        if finding.get("severity") not in RESOLUTION_SEVERITIES:
            continue
        state_path = _attached_state_path(finding)
        if state_path is None:
            continue
        if not state_path.is_file():
            unresolved.append({"finding": finding, "reason": "attached_state_missing", "state": str(state_path)})
        elif finding.get("resolved") is not True:
            unresolved.append({"finding": finding, "reason": "attached_state_not_resolved", "state": str(state_path)})
        elif not isinstance(finding.get("resolution"), dict) or finding["resolution"].get("verified") is not True or not str(finding["resolution"].get("observed", "")).strip():
            unresolved.append({"finding": finding, "reason": "resolution_evidence_required", "state": str(state_path)})
    return unresolved


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
            if candidate.get("forbidden_by"):
                continue
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
    result = {"kind": kind, "severity": extra.pop("severity", DEFAULT_SEVERITY.get(kind, "medium")), "summary": summary, **extra}
    if transition:
        result["state_hash"] = transition.get("pre_state_hash")
        result["action_id"] = transition.get("action_id")
        if transition.get("pre_state_path"):
            result["state"] = transition["pre_state_path"]
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


def _fight_observations(transitions: list[dict[str, Any]], terminal: str) -> list[dict[str, Any]]:
    """List non-blocking facts that still deserve review after every fight."""
    observations: list[dict[str, Any]] = []
    switches = sum(transition.get("action", {}).get("kind") == "switch" for transition in transitions)
    moves = sum(transition.get("action", {}).get("kind") == "move" for transition in transitions)
    pp_spent = sum(
        len((transition.get("actual") or {}).get("allied_pp_deltas") or [])
        for transition in transitions
    )
    prevented = sum(
        (transition.get("actual") or {}).get("allied_action_outcome") == "prevented_by_status"
        for transition in transitions
    )
    seen_faints: set[str] = set()
    for transition in transitions:
        feedback = str(((transition.get("actual") or {}).get("resolution") or {}).get("feedback", ""))
        for faint in re.finditer(r"(?P<actor>[A-Za-z][A-Za-z0-9'’.-]*)\s+fainted!", feedback, re.IGNORECASE):
            # Battle text can report an enemy faint and an allied faint in the
            # same transaction. Only the actor immediately preceded by "Foe"
            # is enemy-side; do not turn that normal event into a false issue.
            if re.search(r"\bFoe\s+$", feedback[:faint.start()], re.IGNORECASE):
                continue
            summary = " ".join(feedback.split())[-240:]
            key = f"{faint.group('actor').casefold()}:{summary}"
            if key not in seen_faints:
                seen_faints.add(key)
                observations.append({
                    "kind": "faint_observed",
                    "severity": "high",
                    "summary": f"allied faint observed ({faint.group('actor')}); determine whether the earliest causal action was avoidable: {summary}",
                    "action_changing": False,
                    "requires_bounded_replay": True,
                })
    if switches >= 8:
        observations.append({
            "kind": "switch_tempo_observation",
            "severity": "medium",
            "summary": f"{switches} switches occurred across {len(transitions)} verified actions; inspect whether each pivot preserved a reserve or only extended the line",
            "action_changing": False,
        })
    if len(transitions) > 25:
        observations.append({
            "kind": "long_line_observation",
            "severity": "low",
            "summary": f"the fight required {len(transitions)} verified actions; seek a verified shorter line only if it changes the lexicographic result",
            "action_changing": False,
        })
    observations.append({
        "kind": "resource_tempo_summary",
        "severity": "info",
        "summary": f"verified usage: {moves} move actions, {pp_spent} allied PP decrements, {switches} switches, {prevented} status-prevented actions; terminal={terminal}",
        "action_changing": False,
    })
    return observations


def _postmortem(
    transitions: list[dict[str, Any]],
    *,
    terminal: str,
    findings: list[dict[str, Any]],
    observations: list[dict[str, Any]],
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
            "severity": finding.get("severity", "medium"),
            "summary": finding.get("summary"),
            "layer": finding.get("root_layer") or _improvement_layer(str(finding.get("kind"))),
            "state_hash": finding.get("state_hash"),
            "action_id": finding.get("action_id"),
            "action_changing": finding.get("action_changing", False),
        }
        for finding in findings
    ]
    improvements.extend({
        "kind": observation.get("kind"),
        "severity": observation.get("severity", "info"),
        "summary": observation.get("summary"),
        "layer": _improvement_layer(str(observation.get("kind"))),
        "action_changing": observation.get("action_changing", False),
    } for observation in observations)
    if counterfactuals:
        improvements.append({
            "kind": "bounded_counterfactual_review",
            "severity": "high",
            "summary": f"replay {len(counterfactuals)} materially plausible alternative action(s) once before retry",
            "layer": "bounded_counterfactual_or_scorer",
            "action_changing": True,
        })
    if not findings and not counterfactuals and terminal == "win" and status == "clean":
        improvements.append({
            "kind": "no_action_changing_defect",
            "severity": "info",
            "summary": "no verified action-changing problem; retain the unchanged bundle for qualification",
            "layer": "none",
            "action_changing": False,
        })
    return {
        "terminal_authoritative": terminal in {"win", "loss"},
        "what_went_wrong": findings,
        "what_could_improve": improvements,
        "prioritized_items": sorted(
            [*findings, *observations],
            key=lambda item: SEVERITY_ORDER.get(str(item.get("severity", "medium")), 2),
        ),
        "prediction_comparisons": comparisons,
        "next_step": "apply every action-changing improvement before retry" if any(item.get("action_changing") for item in improvements) else "qualify unchanged bundle or monitor non-blocking observations",
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
            if not candidate.get("forbidden_by")
            and candidate.get("action") != selected.get("action")
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
        if actual.get("allied_action_outcome") not in {None, "queued", "executed", "interrupted_before_execution", "prevented_by_status"}:
            findings.append(_finding("unexpected_action_outcome", str(actual.get("allied_action_outcome")), transition, action_changing=True))

    if terminal == "policy_gap":
        findings.append(_finding("policy_gap", "the declarative profile had no safe legal action", action_changing=True))
    if terminal == "step_mismatch" and not any(item["kind"] == "tooling_mismatch" for item in findings):
        findings.append(_finding("tooling_mismatch", "battle step verification failed", action_changing=True))
    if terminal == "loss" and not findings:
        kind = "tactical_error" if any(item.get("policy_decision") for item in transitions) else "preparation_team_failure"
        findings.append(_finding(kind, "the verified party and policy did not produce a terminal win", transitions[-1] if transitions else None, action_changing=True))
    if terminal == "loss":
        findings.append(_finding("terminal_loss", "authoritative battle state ended in a loss/whiteout", transitions[-1] if transitions else None, action_changing=True))
    if terminal == "action_limit":
        findings.append(_finding("incomplete_fight", "the bounded runner reached its action limit before terminal verification", transitions[-1] if transitions else None, severity="high", action_changing=True))

    counterfactuals = bounded_counterfactuals(transitions)
    observations = _fight_observations(transitions, terminal)
    blocking = [item for item in findings if item.get("action_changing")]
    status = "clean" if terminal == "win" and not blocking and not counterfactuals else "needs_improvement"
    if terminal == "loss" and findings:
        classification = findings[0]["kind"]
    elif terminal == "win" and blocking:
        classification = blocking[0]["kind"]
    else:
        classification = "verified_win" if terminal == "win" else "unresolved"
    applied_ids = {
        str(item).split("strategy:", 1)[1].split("/rule:", 1)[0]
        for transition in transitions
        for item in (transition.get("policy_decision") or {}).get("applied_strategy_ids", [])
        if str(item).startswith("strategy:")
    }
    matched_strategy_ids = applied_ids
    influential_strategy_ids = {
        str(strategy_id)
        for transition in transitions
        for strategy_id in (transition.get("policy_decision") or {}).get("influential_strategy_ids", [])
    } & applied_ids
    postmortem = _postmortem(
        transitions,
        terminal=terminal,
        findings=findings,
        observations=observations,
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
        "influential_strategy_ids": sorted(influential_strategy_ids),
        "certified_actions": len(transitions),
        "findings": findings,
        "observations": observations,
        "postmortem": postmortem,
        "counterfactuals": counterfactuals,
        "status": status,
        "classification": classification,
    }
    payload["review_id"] = _review_id(payload)
    return payload


def persist_review(
    review: dict[str, Any],
    review_dir: str | Path = REVIEW_DIR,
) -> Path:
    """Write exactly one compact durable JSON artifact per review."""
    if review.get("terminal") == "loss" and review.get("trainer_key"):
        from games.run_and_bun.trainer_database import mark_hard_fight

        mark_hard_fight(str(review["trainer_key"]), review_id=review.get("review_id"))
    return write_review_artifact(review, review_dir)


def _compact_review(review: dict[str, Any]) -> dict[str, Any]:
    """Keep only the AI finding list and fields required by the gates."""
    keys = (
        "kind", "severity", "summary", "state", "state_hash", "action_id",
        "action_changing", "resolved", "resolution", "root_layer", "alternative",
        "why_bad", "improvement", "observed_fact", "strategic_judgment",
        "confidence", "fix_layer", "turns", "next_test", "strategy_id",
        "requirements_matched", "state_unavailable_reason",
        "knowledge_target", "candidate_strategy_id", "matchup_specific_reason",
    )

    def finding(item: dict[str, Any]) -> dict[str, Any]:
        return {key: item[key] for key in keys if key in item}

    stored = {
        key: review[key]
        for key in (
            "schema_version", "created_at", "review_id", "terminal", "source",
            "opening_state_hash", "behavior_hash", "policy_id", "trainer_key",
            "matched_strategy_ids", "influential_strategy_ids", "certified_actions", "status", "classification",
        )
        if key in review
    }
    agent = review.get("agent_review")
    if isinstance(agent, dict):
        stored["agent_review"] = {
            "schema_version": agent.get("schema_version", 1),
            "status": agent.get("status"),
            "author": agent.get("author"),
            "reviewed_actions": agent.get("reviewed_actions"),
            "findings": [finding(item) for item in agent.get("findings", []) if isinstance(item, dict)],
            "correct_choices": list(agent.get("correct_choices", [])),
            "next_test": agent.get("next_test", ""),
        }
    return stored


def write_review_artifact(review: dict[str, Any], review_dir: str | Path = REVIEW_DIR) -> Path:
    """Write one compact standalone review without changing another store."""
    attach_resolution_fields(review)
    stored = _compact_review(review)
    artifact = Path(review_dir) / f"{stored.get('review_id') or _review_id(stored)}.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(stored, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return artifact


def clone_review_gate(
    review_dir: str | Path = REVIEW_DIR,
    qualification_path: str | Path = QUALIFICATION_PATH,
    trainer_key: str | None = None,
    *,
    record_qualification: bool = True,
) -> dict[str, Any]:
    """Allow the first clone, then require the newest review artifact."""
    def review_order(item: Path) -> tuple[str, str]:
        try:
            created = str(json.loads(item.read_text(encoding="utf-8")).get("created_at", ""))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            created = ""
        return created, item.name

    artifacts = sorted(Path(review_dir).glob("*.json"), key=review_order)
    if trainer_key is not None:
        artifacts = [
            item for item in artifacts
            if _review_trainer_key(item) == trainer_key
        ]
    if not artifacts:
        return {"allowed": True, "reason": "no_prior_review"}
    try:
        artifact = artifacts[-1]
        stored = json.loads(artifact.read_text(encoding="utf-8"))
        review_id = str(stored["review_id"])
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        return {"allowed": False, "reason": "latest_review_artifact_missing_or_invalid", "error": str(error)}
    if stored.get("review_id") != review_id:
        return {"allowed": False, "reason": "latest_review_artifact_id_mismatch", "review_id": review_id}
    if attach_resolution_fields(stored):
        write_review_artifact(stored, review_dir)
    if not agent_review_complete(stored):
        return {"allowed": False, "reason": "agent_battle_review_required", "review_id": review_id, "path": str(artifact)}
    unresolved = unresolved_attached_findings(stored)
    if unresolved:
        return {"allowed": False, "reason": "high_severity_review_resolution_required", "review_id": review_id, "path": str(artifact), "unresolved": unresolved}
    qualification = None
    if record_qualification and stored.get("source") == "cartridge_clone":
        qualification = QualificationLedger(qualification_path, fight_key=trainer_key).record(stored)
    return {
        "allowed": True,
        "reason": "latest_agent_review_verified",
        "review_id": review_id,
        "path": str(artifact),
        "qualification": qualification,
    }


def battle_continuation_gate(review_dir: str | Path = REVIEW_DIR) -> dict[str, Any]:
    """Require the most recently finished fight to receive its agent review."""
    return clone_review_gate(review_dir, trainer_key=None, record_qualification=False)


def _review_trainer_key(path: Path) -> str | None:
    """Read only the identity needed to keep unrelated fight reviews separate."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    key = value.get("trainer_key")
    return str(key) if key is not None else None


def live_qualification_gate(
    behavior_hash: str,
    opening_state_hash: str | None = None,
    path: str | Path = QUALIFICATION_PATH,
    trainer_key: str | None = None,
) -> dict[str, Any]:
    state = QualificationLedger(path, fight_key=trainer_key).state
    if state.get("ready") is not True or int(state.get("streak", 0)) < 3:
        return {"allowed": False, "reason": "three_clean_clone_wins_required", "streak": state.get("streak", 0)}
    if state.get("behavior_hash") != behavior_hash:
        return {"allowed": False, "reason": "qualification_behavior_hash_mismatch", "streak": state.get("streak", 0)}
    if opening_state_hash is not None and state.get("opening_state_hash") != opening_state_hash:
        return {"allowed": False, "reason": "qualification_opening_hash_mismatch", "streak": state.get("streak", 0)}
    return {"allowed": True, "reason": "three_clean_clone_wins_verified", "streak": state.get("streak", 0)}


class QualificationLedger:
    """Persist the exact three-clean-clone streak and reset reasons."""

    def __init__(self, path: str | Path = QUALIFICATION_PATH, fight_key: str | None = None) -> None:
        self.path = Path(path)
        self.fight_key = fight_key
        self.document = self._load()
        self.state = self._fight_state(self.document)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"streak": 0, "ready": False, "attempts": []}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"streak": 0, "ready": False, "attempts": []}
        except json.JSONDecodeError:
            return {"streak": 0, "ready": False, "attempts": []}

    def _fight_state(self, document: dict[str, Any]) -> dict[str, Any]:
        if self.fight_key is None:
            return document
        fights = document.get("fights") if document.get("schema_version") == 2 else None
        state = fights.get(self.fight_key) if isinstance(fights, dict) else None
        return dict(state) if isinstance(state, dict) else {"streak": 0, "ready": False, "attempts": []}

    def _save(self) -> None:
        if self.fight_key is None:
            document = self.state
        else:
            fights = dict(self.document.get("fights") or {}) if self.document.get("schema_version") == 2 else {}
            fights[self.fight_key] = self.state
            document = {"schema_version": 2, "fights": fights}
            self.document = document
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def record(self, review: dict[str, Any]) -> dict[str, Any]:
        previous = self.state
        unresolved = unresolved_attached_findings(review)
        if not agent_review_complete(review) or unresolved:
            return {**previous, "pending_review": True, "pending_review_id": review.get("review_id"), "unresolved": unresolved}
        if previous.get("last_agent_review_id") == review.get("review_id"):
            return dict(previous)
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
            "last_agent_review_id": review.get("review_id"),
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
        self._save()
        return dict(self.state)
