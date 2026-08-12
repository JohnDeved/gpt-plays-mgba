#!/usr/bin/env python3
"""Call one Run & Bun capability without hand-writing JSON-RPC plumbing."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _pick(value: dict, *keys: str) -> dict:
    return {key: value[key] for key in keys if key in value}


def _brief_action(value: dict, uncertainty_ids: dict[str, int] | None = None) -> dict:
    result = _pick(
        value,
        "kind", "slot", "actor", "target", "move_id", "move", "move_type", "category",
        "damage_range", "critical_damage_max", "accuracy", "hit_probability", "priority",
        "acts_first", "ko_before_hit", "ko_in", "guaranteed_ko_in", "uncertainties", "reason",
        "species", "hp", "status", "personality",
    )
    if uncertainty_ids is not None and result.get("uncertainties"):
        result["uncertainty_ids"] = [uncertainty_ids[item] for item in result.pop("uncertainties")]
    return result


def _brief_policy_decision(value: dict, uncertainty_ids: dict[str, int]) -> dict:
    def candidate(item: dict) -> dict:
        action = item.get("action") or {}
        return _without_empty({
            "action": _pick(action, "kind", "slot", "actor", "target", "move_id", "species"),
            **_pick(item, "score", "safe", "survival_margin", "evidence", "forbidden_by"),
            "damage": [item.get("damage_min"), item.get("damage_est")],
            "uncertainty_ids": [uncertainty_ids[x] for x in item.get("uncertainties", [])],
            "guaranteed_ko": True if item.get("guaranteed_ko") else None,
            "progress_before_faint": True if item.get("progress_before_faint") else None,
            "next_action_reachable": True if (item.get("continuation") or {}).get("next_action_reachable") else None,
        })

    uncertainties = value.get("uncertainties") or {}
    return _without_empty({
        **_pick(value, "policy_id", "classification"),
        "action": _pick(value.get("action") or {}, "kind", "slot", "actor", "target", "move_id", "species"),
        "candidates": [candidate(item) for item in value.get("candidates", [])],
        **_pick(value, "applied_strategy_ids", "influential_strategy_ids", "vetoes", "reservations", "strategy_effects"),
        "uncertainties": {
            key: [uncertainty_ids[item] for item in items]
            for key, items in uncertainties.items() if items
        },
    })


def _brief_observation(value: dict, *, post_action: bool = False) -> dict:
    battle = value.get("battle") or {}
    party_keys = (
        "slot", "species", "level", "hp", "max_hp", "status", "types", "ability",
        "held_item", "moves", "pp", "stat_stages",
    )
    if post_action:
        party_keys = ("slot", "species", "hp", "max_hp", "status")
    battle_keys = (*party_keys, "stat_stages")
    active_battle = bool(battle.get("active"))
    return {
        **_pick(value, "state_hash", "mode", "map"),
        "party": [_pick(mon, *party_keys) for mon in value.get("party", [])],
        "battle": {
            **_pick(battle, "active"),
            **(_pick(battle, "kind", "party_switch_required", "menu") if active_battle else {}),
            "mons": [_pick(mon, *battle_keys) for mon in battle.get("mons", [])] if active_battle else [],
        },
        "ui": {
            key: item for key, item in (value.get("ui") or {}).items()
            if (active_battle or not key.startswith("battle_"))
            and item not in (None, False, 0, "", "none")
            and not (isinstance(item, list) and not any(item))
        },
    }


def _without_empty(value):
    if isinstance(value, dict):
        return {key: compact for key, item in value.items() if (compact := _without_empty(item)) not in (None, {}, [])}
    if isinstance(value, list):
        return [compact for item in value if (compact := _without_empty(item)) not in (None, {}, [])]
    return value


def _brief_result(name: str, value: dict) -> dict:
    """Remove duplicated battle payloads while retaining decision and verification evidence."""
    if name == "game_battle_evaluate":
        incoming = value.get("incoming") or {}
        policy = value.get("policy_decision") or {}
        uncertainty_values = dict.fromkeys(
            item
            for action in [
                *(value.get("legal_actions") or []), *(value.get("alternatives") or []),
                incoming, *(incoming.get("moves") or []), value.get("recommended_action") or {},
                value.get("chosen") or {}, *(item.get("action") or {} for item in policy.get("candidates", [])),
                *(policy.get("candidates") or []),
            ]
            for item in (action.get("uncertainties") or [])
        )
        for item in (value.get("proof") or {}).get("material_uncertainty") or []:
            uncertainty_values.setdefault(item, None)
        for items in (policy.get("uncertainties") or {}).values():
            for item in items:
                uncertainty_values.setdefault(item, None)
        uncertainty_ids = {item: index for index, item in enumerate(uncertainty_values)}
        proof = _pick(value.get("proof") or {}, "level", "claim", "checks", "material_uncertainty", "caveat")
        if proof.get("material_uncertainty"):
            proof["material_uncertainty_ids"] = [uncertainty_ids[item] for item in proof.pop("material_uncertainty")]
        return _without_empty({
            **_pick(value, "state_hash", "certificate_id", "state", "boundary", "fresh_entry"),
            "uncertainties": list(uncertainty_ids),
            "legal_actions": None if policy else [_brief_action(item, uncertainty_ids) for item in value.get("legal_actions", [])],
            "decision": value.get("decision"),
            "policy_decision": _brief_policy_decision(policy, uncertainty_ids),
            "behavior_hash": value.get("behavior_hash"),
            "recommended_action": None if policy else _brief_action(value.get("recommended_action") or {}, uncertainty_ids),
            "chosen": _brief_action(value.get("chosen") or {}, uncertainty_ids),
            "alternatives": None if policy else [_brief_action(item, uncertainty_ids) for item in value.get("alternatives", [])],
            "incoming": {
                **_pick(incoming, "max_damage_est", "critical_max_damage_est"),
                "moves": [
                    _without_empty({
                        **_pick(item, "move_id", "move", "damage_range", "critical_damage_max", "accuracy", "priority", "acts_first"),
                        "uncertainty_ids": [uncertainty_ids[x] for x in item.get("uncertainties", [])],
                    })
                    for item in incoming.get("moves", [])
                ],
            },
            "proof": proof,
        })
    if name == "game_battle_step":
        observation = value.get("observation") or {}
        battle = observation.get("battle") or {}
        actual = dict(value.get("actual") or {})
        resolution = dict(actual.get("resolution") or {})
        if "feedback" in resolution:
            resolution["feedback"] = resolution["feedback"][-320:]
        actual["resolution"] = resolution
        return _without_empty({
            **_pick(value, "action_id", "verified", "source", "certificate_id", "pre_state_hash", "post_state_hash"),
            "action": _brief_action(value.get("action") or {}),
            "executed": _pick(value.get("executed") or {}, "slot", "move", "before_pp", "after_pp", "ack"),
            "predicted": {
                "decision": (value.get("predicted") or {}).get("decision"),
                "chosen": _brief_action((value.get("predicted") or {}).get("chosen") or {}),
                "incoming": _pick((value.get("predicted") or {}).get("incoming") or {}, "max_damage_est", "critical_max_damage_est"),
                "proof": _pick((value.get("predicted") or {}).get("proof") or {}, "level", "claim", "material_uncertainty"),
            },
            "actual": actual,
            "discrepancies": value.get("discrepancies", []),
            "post_state": _brief_observation(observation, post_action=True),
        })
    if name == "game_observe":
        return _without_empty(_brief_observation(value))
    if "observation" in value:
        return _without_empty(value | {"observation": _brief_observation(value["observation"] or {})})
    return _without_empty(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="capability name, for example game_observe")
    parser.add_argument("--args", default="{}", help="JSON object of capability arguments")
    parser.add_argument("--clone-state", type=Path, help="run against a disposable muted clone")
    parser.add_argument("--output", type=Path, help="atomically persist structured output instead of printing it")
    parser.add_argument("--brief", action="store_true", help="print a compact decision-bearing view")
    args = parser.parse_args(argv)
    try:
        arguments = json.loads(args.args)
        if not isinstance(arguments, dict):
            raise ValueError("--args must decode to a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))

    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": args.name, "arguments": arguments},
    }
    session = nullcontext(None)
    if args.clone_state:
        from client.mgba_clone import disposable_clone

        session = disposable_clone(args.clone_state)
    with session as gba:
        runtime = tempfile.TemporaryDirectory(prefix="runbun-call-") if gba is not None else nullcontext(None)
        with runtime as clone_runtime:
            env = os.environ.copy()
            if gba is not None:
                env["MGBA_RPC_PORT"] = str(gba.port)
                env["MGBA_RUNTIME_DIR"] = clone_runtime
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "runbun_mcp.py")],
                input=json.dumps(request) + "\n",
                text=True,
                capture_output=True,
                cwd=ROOT,
                env=env,
                check=False,
            )
    if result.returncode:
        sys.stderr.write(result.stderr)
        return result.returncode
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"invalid runbun response: {exc}\n{result.stdout}")
        return 1
    if "error" in response:
        print(json.dumps(response, separators=(",", ":")))
        return 1
    result_body = response.get("result", {})
    if result_body.get("isError"):
        print(json.dumps(result_body.get("structuredContent", {}), separators=(",", ":")))
        return 1
    structured = result_body.get("structuredContent", {})
    payload = json.dumps(structured, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=output.parent, delete=False, encoding="utf-8") as handle:
            handle.write(payload)
            temporary = handle.name
        os.replace(temporary, output)
        print(json.dumps({"output": str(output)}, separators=(",", ":")))
    else:
        print(json.dumps(_brief_result(args.name, structured) if args.brief else structured, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
