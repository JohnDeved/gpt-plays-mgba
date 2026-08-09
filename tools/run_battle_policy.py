#!/usr/bin/env python3
"""Run one bounded policy attempt, review it, and update clone qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from client.mgba_clone import disposable_clone
from client.mgba_rpc import MGBA
from games.runbun import RunBunAdapter
from games.run_and_bun.battle_policy import BattleHistory, BattlePolicy, PolicyError, load_profile, load_strategies
from games.run_and_bun.battle_review import QualificationLedger, persist_review, review_episode
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


def _stable(adapter: RunBunAdapter, gba: MGBA) -> tuple[dict, dict] | None:
    for _ in range(5):
        try:
            return _stable_battle_observation(adapter)
        except CapabilityError as error:
            if error.code != "UNSTABLE_STATE":
                raise
            gba.wait_frames(24)
    return None


def _advance(adapter: RunBunAdapter, gba: MGBA) -> tuple[dict, dict] | None:
    adapter.advance_battle_until_menu(max_frames=1800, visual_fallback=False)
    return _stable(adapter, gba)


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
    parser.add_argument("--max-actions", type=int, default=50)
    parser.add_argument("--save-on-stop", type=Path)
    parser.add_argument("--review-state-dir", type=Path)
    parser.add_argument("--no-record-strategies", action="store_true")
    args = parser.parse_args()
    if args.live and args.plan is None:
        parser.error("--live requires --plan")
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

    profile = load_profile(args.profile)
    strategies = load_strategies()
    history_path = ROOT / "runtime" / "session" / "battle_transactions.jsonl"
    history = BattleHistory.from_jsonl(history_path, battle_id=args.battle_id) if args.battle_id else BattleHistory()
    policy = BattlePolicy(profile, strategies=strategies, history=history)
    if args.live:
        plan_data = json.loads(args.plan.read_text(encoding="utf-8"))
        evidence = plan_data.get("evidence", {})
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
        review_state_dir = ROOT / "runtime" / "session" / "policy-review-states" / str(time.time_ns())
    if review_state_dir:
        review_state_dir.mkdir(parents=True, exist_ok=True)

    with session as gba:
        adapter = RunBunAdapter(gba)
        for turn in range(1, args.max_actions + 1):
            stable = _stable(adapter, gba)
            if stable is None:
                terminal = "unstable_state"
                stop = {"error": "battle did not stabilize after five no-input samples"}
                break
            observation, compact = stable
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
            certificate = _battle_certificate(adapter, observation)
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
            result = _battle_step({
                "state_hash": compact["state_hash"],
                "certificate_id": certificate["certificate_id"],
                "action": action,
                "policy_decision": decision,
                "battle_id": args.battle_id or profile.get("trainer_key"),
                "max_frames": 1800,
            }, adapter=adapter, persist=True)
            if pre_state_path:
                result["pre_state_path"] = str(pre_state_path.resolve())
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

        review = review_episode(
            transitions,
            terminal=terminal,
            source=source,
            opening_state_hash=opening_state_hash,
            behavior_hash=policy.behavior_hash,
            policy_id=profile["id"],
            trainer_key=profile.get("trainer_key"),
            profile_strategy_ids=profile.get("strategy_ids", []),
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
        persist_review(review)
        qualification = None
        if not args.live:
            qualification = QualificationLedger().record(review)
            if not args.no_record_strategies and profile.get("strategy_ids"):
                handle = tempfile.NamedTemporaryFile(prefix="runbun-review-", suffix=".json", delete=False)
                review_path = Path(handle.name)
                handle.close()
                try:
                    review_path.write_text(json.dumps(review), encoding="utf-8")
                    subprocess.run([
                        sys.executable,
                        str(ROOT / ".agents/skills/develop-runbun-strategies/scripts/strategy_db.py"),
                        "record-review",
                        str(review_path),
                        *sum((["--strategy-id", item] for item in profile["strategy_ids"]), []),
                    ], cwd=ROOT, check=True, capture_output=True, text=True)
                except subprocess.CalledProcessError as error:
                    stop = {**(stop or {}), "strategy_record_error": error.stderr[-500:]}
                finally:
                    review_path.unlink(missing_ok=True)
        _emit_progress({
            "terminal": terminal,
            "review": review,
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
