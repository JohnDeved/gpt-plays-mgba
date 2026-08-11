#!/usr/bin/env python3
"""Run the retired Gavi policy as a bounded disposable-clone regression oracle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from client.mgba_clone import disposable_clone
from games.runbun import RunBunAdapter
from games.run_and_bun.capabilities import (
    CapabilityError,
    _battle_certificate,
    _battle_step,
    _branch_terminal,
    _stable_battle_observation,
)
from games.run_and_bun.gavi_policy import GaviPolicy


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--max-actions", type=int, default=40)
    parser.add_argument("--save-on-stop", type=Path)
    args = parser.parse_args()

    with disposable_clone(args.state) as gba:
        adapter, policy, action_ids, stop = RunBunAdapter(gba, enforce_live_trainer_gate=False), GaviPolicy(), [], None
        for turn in range(1, args.max_actions + 1):
            for _ in range(5):
                try:
                    observation, compact = _stable_battle_observation(adapter)
                    break
                except CapabilityError as error:
                    if error.code != "UNSTABLE_STATE":
                        raise
                    gba.wait_frames(24)
            else:
                terminal = "unstable_state"
                stop = {"error": "battle did not stabilize after five no-input samples"}
                break
            terminal = _branch_terminal(observation)
            if terminal:
                break
            if not observation.get("battle", {}).get("active"):
                adapter.advance_battle_until_menu(max_frames=1800, visual_fallback=False)
                observation, compact = _stable_battle_observation(adapter)
                terminal = _branch_terminal(observation)
                if terminal:
                    break
                if not observation.get("battle", {}).get("active"):
                    terminal = "unknown_inactive"
                    stop = {"error": "inactive battle did not settle to a decision or authoritative terminal"}
                    break
            if observation.get("battle", {}).get("menu", {}).get("state") not in {"command_menu", "move_menu", "party_switch"}:
                adapter.advance_battle_until_menu(max_frames=1800, visual_fallback=False)
                observation, compact = _stable_battle_observation(adapter)
                terminal = _branch_terminal(observation)
                if terminal:
                    break
            certificate = _battle_certificate(adapter, observation)
            try:
                action = policy.choose(certificate)
            except RuntimeError as error:
                terminal = "policy_gap"
                stop = {"error": str(error), "certificate": certificate}
                break
            result = _battle_step(
                {"state_hash": compact["state_hash"], "certificate_id": certificate["certificate_id"], "action": action, "max_frames": 1800},
                adapter=adapter,
                persist=False,
            )
            if not result["verified"]:
                terminal = "step_mismatch"
                stop = {"discrepancies": result["discrepancies"], "certificate": certificate}
                break
            action_ids.append(result["action_id"])
            policy.record_verified(certificate, action)
            print(json.dumps({
                "turn": turn,
                "certificate": certificate["certificate_id"],
                "player": certificate["state"]["player"]["species"],
                "opponent": certificate["state"]["opponent"]["species"],
                "hp": [certificate["state"]["player"]["hp"], certificate["state"]["opponent"]["hp"]],
                "action": {key: action[key] for key in ("kind", "slot", "move_id", "species") if key in action},
                "verified": True,
            }), flush=True)
        else:
            terminal = "action_limit"
        final = adapter.observe()
        if args.save_on_stop:
            gba.save_state(args.save_on_stop.resolve())
        print(json.dumps({
            "terminal": terminal,
            "certified_actions": len(action_ids),
            "action_ids": action_ids,
            "mode": final.get("mode"),
            "battle_active": final.get("battle", {}).get("active"),
            "text": [item.get("text", "").strip() for item in final.get("text", {}).get("battle_printers", [])],
            "survivors": {
                mon["state"]["species"]: mon["state"]["current_hp"]
                for mon in final.get("party", {}).get("mons", [])
                if mon.get("present") and mon["state"].get("current_hp", 0) > 0
            },
            "stop": stop,
        }, default=str), flush=True)
        return 0 if terminal == "win" else 2


if __name__ == "__main__":
    raise SystemExit(main())
