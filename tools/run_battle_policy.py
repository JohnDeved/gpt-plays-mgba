#!/usr/bin/env python3
"""Run one bounded policy attempt, review it, and update clone qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from client.mgba_clone import disposable_clone
from client.mgba_rpc import MGBA
from games.runbun import RunBunAdapter
from games.run_and_bun.battle_policy import BattleHistory, BattlePolicy, PolicyError, load_profile, load_strategies
from games.run_and_bun.battle_review import REVIEW_STATE_DIR, QualificationLedger, clone_review_gate, live_qualification_gate, persist_review, review_episode
from games.run_and_bun.capabilities import (
    CapabilityError,
    _battle_certificate,
    _battle_step,
    _branch_terminal,
    _stable_battle_observation,
)


def _emit_progress(record: dict) -> None:
    """Emit one machine-readable progress card without buffering."""
    print(json.dumps(record, separators=(",", ":")), flush=True)


def _review_progress(review: dict) -> dict:
    """Keep terminal output small; the complete compact review lives on disk."""
    return {
        key: review.get(key)
        for key in ("review_id", "terminal", "status", "classification", "certified_actions")
    }


def _terminal_from_result(result: dict) -> str | None:
    feedback = str(((result.get("actual") or {}).get("resolution") or {}).get("feedback", "")).casefold()
    if "whited out" in feedback or "out of usable pok" in feedback:
        return "loss"
    if "for winning" in feedback or "defeated" in feedback:
        return "win"
    observation = result.get("observation") or {}
    if not observation.get("battle", {}).get("active"):
        party = observation.get("party", [])
        if party and not any(int(mon.get("hp", mon.get("current_hp", 0)) or 0) > 0 for mon in party):
            return "loss"
    return None


def _tool_error_transition(certificate: dict, decision: dict, action: dict, error: Exception, pre_state_path: Path | None = None) -> dict:
    result = {
        "action_id": None,
        "verified": False,
        "certificate_id": certificate.get("certificate_id"),
        "certificate": {
            "state": certificate.get("state"),
            "compact_state": certificate.get("compact_state"),
            "boundary": certificate.get("boundary"),
        },
        "pre_state_hash": certificate.get("state_hash"),
        "action": action,
        "policy_decision": decision,
        "actual": {"allied_action_outcome": "tool_error", "error": str(error)},
        "discrepancies": [f"{type(error).__name__}: {error}"],
    }
    if pre_state_path:
        result["pre_state_path"] = str(pre_state_path.resolve())
    return result


def _stable(adapter: RunBunAdapter, gba: MGBA) -> tuple[dict, dict] | None:
    for _ in range(5):
        try:
            return _stable_battle_observation(adapter)
        except CapabilityError as error:
            if error.code != "UNSTABLE_STATE":
                raise
            gba.wait_frames(24)
    return None


def _certificate_for_policy(adapter: RunBunAdapter, observation: dict, policy: BattlePolicy) -> dict:
    return _battle_certificate(adapter, observation, fresh_entry=policy.history.fresh_entry)


def _advance(adapter: RunBunAdapter, gba: MGBA) -> tuple[dict, dict] | None:
    adapter.advance_battle_until_menu(max_frames=1800, visual_fallback=False)
    return _stable(adapter, gba)


def _clone_start_error(observation: dict, trainer_local_id: int | None) -> str | None:
    if observation.get("battle", {}).get("active") or trainer_local_id is not None:
        return None
    return "clone state is outside battle; pass --trainer-local-id for an atomic precontact launch"


def _engage_trainer(
    adapter: RunBunAdapter,
    local_id: int,
    trainer_key: str | None,
    *,
    require_ready: bool,
) -> None:
    """Start a named trainer after the caller's clone/live gates have passed."""
    match = re.fullmatch(r"map:(\d+):(\d+)/local:(\d+)", trainer_key or "")
    expected_map = (int(match.group(1)), int(match.group(2))) if match else None
    if match and int(match.group(3)) != local_id:
        raise RuntimeError("clone trainer local_id does not match profile trainer_key")
    result = adapter.follow_live_path_to_npc(
        local_id=local_id,
        expected_map=expected_map,
        interact=True,
        require_trainer_ready=require_ready,
        avoid_trainer_sight_lines=False,
    )
    if result.get("reason") != "interacted":
        raise RuntimeError(f"trainer interaction failed: {result.get('reason')}")
    pages = adapter.advance_dialogue()
    final = adapter.observe()
    remaining_frames = 120
    while not final.get("battle", {}).get("active") and remaining_frames > 0:
        if final.get("mode") == "dialogue":
            pages.extend(adapter.advance_dialogue(max_pages=32 - len(pages)))
        else:
            adapter.gba.wait_frames(12)
            remaining_frames -= 12
        final = adapter.observe()
    if not final.get("battle", {}).get("active"):
        raise RuntimeError(json.dumps({
            "error": "trainer interaction did not start a battle",
            "interaction_mode": (result.get("state") or {}).get("mode"),
            "interaction_text": ((result.get("state") or {}).get("text") or {}).get("current"),
            "pages": pages,
            "final_mode": final.get("mode"),
            "final_text": (final.get("text") or {}).get("current"),
        }, default=str, separators=(",", ":")))


def _live_preflight(adapter: RunBunAdapter, plan: dict) -> dict:
    """Reject a live battle fixture whose map or prepared party is stale."""
    state = adapter.observe()
    actual_map = state.get("map") or {}
    errors: list[str] = []
    fight_id = str((plan.get("fight") or {}).get("id", ""))
    match = re.search(r"map:(\d+):(\d+)", fight_id)
    if match and (actual_map.get("group"), actual_map.get("number")) != (int(match.group(1)), int(match.group(2))):
        errors.append(f"map mismatch: expected {match.group(1)}:{match.group(2)}, got {actual_map.get('group')}:{actual_map.get('number')}")
    expected_party = sorted(plan.get("party") or [], key=lambda item: item.get("slot", -1))
    actual_party = sorted((state.get("party") or {}).get("mons", []), key=lambda item: item.get("slot", -1))
    if len(expected_party) != len(actual_party):
        errors.append(f"party count mismatch: expected {len(expected_party)}, got {len(actual_party)}")
    strict_fields = {
        "species_id": "species", "personality": "personality", "level": "level",
        "current_hp": "current_hp", "max_hp": "max_hp", "status": "status",
        "moves": "moves", "pp": "pp", "held_item_id": "held_item", "ability_num": "ability_num",
    }
    if int(plan.get("schema_version", 1)) >= 3:
        for expected in expected_party:
            missing = sorted(set(strict_fields) - set(expected))
            if missing:
                errors.append(f"party slot {expected.get('slot')} missing strict fields: {','.join(missing)}")
    for expected, actual in zip(expected_party, actual_party):
        observed = actual.get("state") or {}
        for expected_field, observed_field in strict_fields.items():
            if expected_field not in expected:
                continue
            value = observed.get(observed_field)
            if expected_field in {"moves", "pp"}:
                value = list(value or [])
            if value != expected[expected_field]:
                errors.append(f"party slot {expected.get('slot')} {expected_field} mismatch: expected {expected[expected_field]}, got {value}")
        if (observed.get("checksum") or {}).get("valid") is False:
            errors.append(f"party slot {expected.get('slot')} has an invalid encrypted-record checksum")
    return {
        "allowed": not errors,
        "errors": errors,
        "state_hash": _compact_state_for_preflight(state),
        "map": actual_map,
        "party_species": [((mon.get("state") or {}).get("species")) for mon in actual_party],
    }


def _compact_state_for_preflight(state: dict) -> str:
    """Use the canonical hash without importing the capability module twice."""
    from games.run_and_bun.capabilities import _compact_state

    return _compact_state(state)["state_hash"]


def _run_probe(gba: MGBA, adapter: RunBunAdapter, task: dict) -> dict:
    state_path = task.get("state_path")
    if not state_path or not Path(state_path).is_file():
        return {"task": task, "verified": False, "error": "pre-action state was not saved"}
    gba.load_state(Path(state_path).resolve())
    stable = _stable(adapter, gba)
    if stable is None:
        return {"task": task, "verified": False, "error": "probe state did not stabilize"}
    observation, compact = stable
    certificate = _battle_certificate(adapter, observation)
    action = task["action"]
    if not any(
        candidate.get("kind") == action.get("kind") and candidate.get("slot") == action.get("slot")
        for candidate in certificate.get("legal_actions", [])
    ):
        return {"task": task, "verified": False, "error": "counterfactual action was not legal at saved state"}
    result = _battle_step({
        "state_hash": compact["state_hash"],
        "certificate_id": certificate["certificate_id"],
        "action": action,
        "max_frames": 1800,
    }, adapter=adapter, persist=False)
    return {
        "task": task,
        "verified": result.get("verified", False),
        "post_state_hash": result.get("post_state_hash"),
        "actual": result.get("actual"),
        "discrepancies": result.get("discrepancies", []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--state", type=Path)
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--battle-id")
    parser.add_argument("--history-battle-id", help="verified prefix source for --regression-run")
    parser.add_argument("--trainer-local-id", type=int, help="atomically start this precontact trainer after clone/live gates pass")
    parser.add_argument("--decision-only", action="store_true", help="print one clone policy decision without input or review")
    parser.add_argument("--review-gate-only", action="store_true", help="record the latest completed clone review without launching a battle")
    parser.add_argument("--live-preflight-only", action="store_true", help="verify live qualification and RAM without sending battle input")
    parser.add_argument("--regression-action", help="execute one JSON action in a disposable state without advancing qualification")
    parser.add_argument("--regression-run", action="store_true", help="run a bounded policy replay from an attached battle state without advancing qualification")
    parser.add_argument("--max-actions", type=int, default=50)
    parser.add_argument("--save-on-stop", type=Path)
    parser.add_argument("--review-state-dir", type=Path)
    parser.add_argument("--no-record-strategies", action="store_true")
    args = parser.parse_args()
    if args.live and args.plan is None:
        parser.error("--live requires --plan")
    if args.live and args.decision_only:
        parser.error("--decision-only is clone-only")
    if args.live_preflight_only and not args.live:
        parser.error("--live-preflight-only requires --live")
    if args.review_gate_only and (args.live or args.decision_only or args.regression_action or args.regression_run):
        parser.error("--review-gate-only is a standalone clone review operation")
    if args.regression_action and (args.live or args.decision_only):
        parser.error("--regression-action requires --state and cannot combine with --decision-only")
    if args.regression_run and (args.live or args.decision_only or args.regression_action):
        parser.error("--regression-run requires --state and cannot combine with another execution mode")
    if args.history_battle_id and not args.regression_run:
        parser.error("--history-battle-id is only valid with --regression-run")
    profile = load_profile(args.profile)
    strategies = load_strategies()
    if args.live:
        check = subprocess.run(
            [sys.executable, str(ROOT / ".agents/skills/prepare-runbun-hard-fight/scripts/validate_plan.py"), str(args.plan)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if check.returncode:
            print(check.stdout, end="")
            return 2
    elif not args.decision_only and not args.regression_action and not args.regression_run:
        review_gate = clone_review_gate(trainer_key=profile.get("trainer_key"))
        if args.review_gate_only:
            _emit_progress({"terminal": "review_gate_only", "review_gate": review_gate})
            return 0 if review_gate["allowed"] else 2
        if not review_gate["allowed"]:
            _emit_progress({"terminal": "review_gate_blocked", "review_gate": review_gate})
            return 2

    history_path = ROOT / "runtime" / "session" / "battle_transactions.jsonl"
    # Each disposable clone is a fresh battle. Reusing the trainer key here
    # leaks prior clone action counts into the next policy attempt.
    history_battle_id = args.battle_id
    if not args.live and history_battle_id and not args.regression_run:
        history_battle_id = f"{history_battle_id}:attempt:{time.time_ns()}"
    history_source_id = args.history_battle_id or history_battle_id
    history = BattleHistory.from_jsonl(history_path, battle_id=history_source_id) if history_source_id else BattleHistory()
    activation_mode = (
        "qualified_live" if args.live and profile.get("schema_version") == 2
        else "clone_trial" if profile.get("schema_version") == 2
        else "legacy"
    )
    policy = BattlePolicy(profile, strategies=strategies, history=history, activation_mode=activation_mode)
    if args.live:
        plan_data = json.loads(args.plan.read_text(encoding="utf-8"))
        evidence = plan_data.get("evidence", {})
        qualification_gate = live_qualification_gate(
            policy.behavior_hash,
            evidence.get("opening_checkpoint_sha256"),
            trainer_key=profile.get("trainer_key"),
        )
        if not qualification_gate["allowed"]:
            _emit_progress({"terminal": "live_gate_blocked", "qualification": qualification_gate})
            return 2
        profile_rel = str(args.profile.resolve().relative_to(ROOT.resolve()))
        if evidence.get("policy_profile") != profile_rel or evidence.get("behavior_hash") != policy.behavior_hash:
            _emit_progress({
                "error": "live plan does not authorize this exact policy profile and behavior bundle",
                "expected_profile": profile_rel,
                "expected_behavior_hash": policy.behavior_hash,
                "plan_evidence": evidence,
            })
            return 2
    session = MGBA(timeout=15) if args.live else disposable_clone(args.state)
    source = "live" if args.live else "cartridge_clone"
    transitions: list[dict] = []
    action_ids: list[str] = []
    stop: dict | None = None
    terminal = "action_limit"
    opening_state_hash = None
    review_state_dir = args.review_state_dir
    if review_state_dir is None and not args.live:
        review_state_dir = REVIEW_STATE_DIR / str(time.time_ns())
    if review_state_dir:
        review_state_dir.mkdir(parents=True, exist_ok=True)

    with session as gba:
        adapter = RunBunAdapter(gba, enforce_live_trainer_gate=False)
        if args.regression_action:
            try:
                action = json.loads(args.regression_action)
                if not isinstance(action, dict):
                    raise ValueError("action must be a JSON object")
            except (json.JSONDecodeError, ValueError) as error:
                parser.error(str(error))
            result = _run_probe(gba, adapter, {"state_path": str(args.state), "action": action})
            if args.save_on_stop:
                gba.save_state(args.save_on_stop.resolve())
            _emit_progress({"terminal": "regression_replay", **result})
            return 0 if result["verified"] else 2
        if not args.live and (error := _clone_start_error(adapter.observe(), args.trainer_local_id)):
            _emit_progress({"terminal": "invocation_error", "error": error})
            return 2
        if not args.live and args.trainer_local_id is not None and not adapter.observe().get("battle", {}).get("active"):
            _engage_trainer(adapter, args.trainer_local_id, profile.get("trainer_key"), require_ready=False)
        if args.decision_only:
            stable = _stable(adapter, gba)
            if stable is None:
                raise RuntimeError("decision-only state did not stabilize")
            observation, _ = stable
            if observation.get("battle", {}).get("menu", {}).get("state") not in {"command_menu", "move_menu", "party_switch"}:
                stable = _advance(adapter, gba)
                if stable is None:
                    raise RuntimeError("decision-only battle did not reach a legal decision boundary")
                observation, _ = stable
            if args.battle_id:
                history = BattleHistory.from_jsonl(
                    history_path,
                    battle_id=args.battle_id,
                    before_state_hash=_compact_state_for_preflight(observation),
                )
                policy = BattlePolicy(profile, strategies=strategies, history=history, activation_mode=activation_mode)
            certificate = _certificate_for_policy(adapter, observation, policy)
            _emit_progress({"certificate": certificate["certificate_id"], "decision": policy.decide(certificate)})
            return 0
        if args.live:
            live_preflight = _live_preflight(adapter, plan_data)
            if not live_preflight["allowed"]:
                _emit_progress({"terminal": "live_preflight_blocked", "live_preflight": live_preflight})
                return 2
            if args.live_preflight_only:
                _emit_progress({"terminal": "live_preflight_only", "live_preflight": live_preflight})
                return 0
            if args.trainer_local_id is not None and not adapter.observe().get("battle", {}).get("active"):
                _engage_trainer(adapter, args.trainer_local_id, profile.get("trainer_key"), require_ready=True)
        for turn in range(1, args.max_actions + 1):
            stable = _stable(adapter, gba)
            if stable is None:
                terminal = "unstable_state"
                stop = {"error": "battle did not stabilize after five no-input samples"}
                break
            observation, compact = stable
            if args.regression_run and not transitions and args.battle_id:
                history = BattleHistory.from_jsonl(
                    history_path,
                    battle_id=history_source_id,
                    before_state_hash=compact["state_hash"],
                )
                policy = BattlePolicy(profile, strategies=strategies, history=history, activation_mode=activation_mode)
            if opening_state_hash is None:
                opening_state_hash = (
                    hashlib.sha256(args.state.read_bytes()).hexdigest()
                    if not args.live
                    else compact["state_hash"]
                )
            terminal = _branch_terminal(observation)
            if terminal:
                break
            if not observation.get("battle", {}).get("active"):
                stable = _advance(adapter, gba)
                if stable is None:
                    terminal = "unknown_inactive"
                    stop = {"error": "inactive battle did not settle"}
                    break
                observation, compact = stable
                terminal = _branch_terminal(observation)
                if terminal:
                    break
            menu = observation.get("battle", {}).get("menu", {}).get("state")
            if menu not in {"command_menu", "move_menu", "party_switch"}:
                stable = _advance(adapter, gba)
                if stable is None:
                    terminal = "unknown_boundary"
                    stop = {"error": "battle did not settle to a legal decision boundary"}
                    break
                observation, compact = stable
                terminal = _branch_terminal(observation)
                if terminal:
                    break
            certificate = _certificate_for_policy(adapter, observation, policy)
            try:
                decision = policy.decide(certificate)
            except PolicyError as error:
                terminal = "policy_gap"
                stop = {"error": str(error), "certificate": certificate}
                break
            action = decision["action"]
            pre_state_path = None
            if review_state_dir and not args.live:
                pre_state_path = review_state_dir / f"turn-{turn:03d}.state"
                gba.save_state(pre_state_path.resolve())
            try:
                result = _battle_step({
                    "state_hash": compact["state_hash"],
                    "certificate_id": certificate["certificate_id"],
                    "action": action,
                    "policy_decision": decision,
                    "battle_id": history_battle_id or profile.get("trainer_key"),
                    "max_frames": 1800,
                }, adapter=adapter, persist=True)
                if pre_state_path:
                    result["pre_state_path"] = str(pre_state_path.resolve())
            except (CapabilityError, RuntimeError, ValueError) as error:
                result = _tool_error_transition(certificate, decision, action, error, pre_state_path)
                transitions.append(result)
                terminal = "step_mismatch"
                stop = {"error": str(error), "certificate": certificate}
                break
            transitions.append(result)
            if not result["verified"]:
                terminal = "step_mismatch"
                stop = {"discrepancies": result["discrepancies"], "certificate": certificate}
                break
            result_terminal = _terminal_from_result(result)
            if result_terminal:
                action_ids.append(result["action_id"])
                policy.record_verified(certificate, action, result)
                terminal = result_terminal
                break
            action_ids.append(result["action_id"])
            policy.record_verified(certificate, action, result)
            _emit_progress({
                "turn": turn,
                "certificate": certificate["certificate_id"],
                "classification": decision["classification"],
                "player": certificate["state"]["player"]["species"],
                "opponent": certificate["state"]["opponent"]["species"],
                "action": action,
                "behavior_hash": policy.behavior_hash,
                "verified": True,
            })
        else:
            terminal = "action_limit"
        final = adapter.observe()
        if args.save_on_stop:
            gba.save_state(args.save_on_stop.resolve())
        terminal_state_path = None
        if review_state_dir and not args.live:
            terminal_state_path = review_state_dir / "terminal.state"
            gba.save_state(terminal_state_path.resolve())

        if args.regression_run:
            _emit_progress({
                "terminal": f"regression_{terminal}",
                "verified": terminal == "win",
                "behavior_hash": policy.behavior_hash,
                "certified_actions": len(action_ids),
                "action_ids": action_ids,
                "final_state_hash": _compact_state_for_preflight(final),
                "save_state": str(args.save_on_stop.resolve()) if args.save_on_stop else None,
                "stop": stop,
            })
            return 0 if terminal == "win" else 2

        review = review_episode(
            transitions,
            terminal=terminal,
            source=source,
            opening_state_hash=opening_state_hash,
            behavior_hash=policy.behavior_hash,
            policy_id=profile["id"],
            trainer_key=profile.get("trainer_key"),
            profile_strategy_ids=[
                *profile.get("strategy_ids", []),
                *[
                    sid
                    for key in ("clone_trial", "blind_live_soft", "full_live")
                    for sid in (profile.get("strategy_manifest") or {}).get(key, [])
                ],
            ],
        )
        if not args.live:
            probe_results = [_run_probe(gba, adapter, task) for task in review.get("counterfactuals", [])]
            if probe_results:
                review["counterfactual_results"] = probe_results
            if terminal_state_path and terminal_state_path.is_file():
                gba.load_state(terminal_state_path.resolve())
                restored = _stable(adapter, gba)
                if restored is not None:
                    final, _ = restored
        review_artifact = persist_review(review)
        qualification = None
        if not args.live:
            qualification = QualificationLedger(fight_key=profile.get("trainer_key")).record(review)
        _emit_progress({
            "terminal": terminal,
            "review": _review_progress(review),
            "review_file": str(review_artifact),
            "qualification": qualification,
            "certified_actions": len(action_ids),
            "action_ids": action_ids,
            "mode": final.get("mode"),
            "battle_active": final.get("battle", {}).get("active"),
            "survivors": {
                mon["state"]["species"]: mon["state"]["current_hp"]
                for mon in final.get("party", {}).get("mons", [])
                if mon.get("present") and mon["state"].get("current_hp", 0) > 0
            },
            "stop": stop,
        })
    return 0 if terminal == "win" else 2


if __name__ == "__main__":
    raise SystemExit(main())
