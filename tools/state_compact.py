"""Emit one compact RAM-backed gameplay summary for agent probes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from client.mgba_rpc import MGBA
from games.runbun import RunBunAdapter
from games.run_and_bun.inventory import VERIFIED_ITEM_NAMES
from games.run_and_bun.state import VERIFIED_MOVE_NAMES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--objects", action="store_true")
    parser.add_argument("--inventory", action="store_true")
    args = parser.parse_args()
    with MGBA(timeout=15) as gba:
        adapter = RunBunAdapter(gba)
        state = adapter.observe()
        result = {
            "map": state.get("map"),
            "mode": state.get("mode"),
            "battle": {
                "active": state["battle"].get("active"),
                "kind": state["battle"].get("kind"),
                "mons": [
                    {
                        "slot": mon["slot"],
                        "species": mon["state"]["species"],
                        "level": mon["state"]["level"],
                        "hp": mon["state"]["current_hp"],
                        "max_hp": mon["state"]["max_hp"],
                        "types": mon["state"]["types"],
                        "moves": mon["state"]["moves"],
                        "move_names": mon["state"].get("move_names") or [
                            VERIFIED_MOVE_NAMES.get(move_id, f"Move#{move_id}")
                            for move_id in mon["state"]["moves"]
                            if move_id
                        ],
                        "pp": mon["state"]["pp"],
                        "stages": mon["state"]["stat_stages"],
                        "status": mon["state"]["status"],
                    }
                    for mon in state["battle"].get("mons", [])
                    if mon.get("present")
                ],
                "ui": state["ui"],
            },
            "party": [
                {
                    "slot": mon["slot"],
                    "name": mon["state"]["nickname"],
                    "species": mon["state"]["species"],
                    "level": mon["state"]["level"],
                    "hp": mon["state"]["current_hp"],
                    "max_hp": mon["state"]["max_hp"],
                }
                for mon in state.get("party", {}).get("mons", [])
                if mon.get("present")
            ],
        }
        if state.get("text", {}).get("current"):
            result["text"] = state["text"]["current"].get("text")
        if args.objects:
            result["objects"] = [
                {
                    "slot": obj["slot"],
                    "local_id": obj["local_id"],
                    "graphics": obj["graphics_id"],
                    "position": obj["position"],
                }
                for obj in state.get("objects", [])
                if not obj.get("is_player")
            ]
        if args.inventory:
            inventory = adapter.inventory()
            result["money"] = inventory["money"]
            result["items"] = {
                pocket: [(item["item_id"], item["quantity"]) for item in entries]
                for pocket, entries in inventory["pockets"].items()
                if entries and not pocket.startswith("ui_")
            }
            result["usable"] = {
                pocket[3:]: [(item["item_id"], item["quantity"]) for item in inventory["pockets"][pocket]]
                for pocket in ("ui_items", "ui_poke_balls", "ui_berries", "ui_medicine")
                if inventory["pockets"].get(pocket)
            }
            names = {
                str(item_id): VERIFIED_ITEM_NAMES[item_id]
                for entries in inventory["pockets"].values()
                for item_id in (item["item_id"] for item in entries)
                if item_id in VERIFIED_ITEM_NAMES
            }
            if names:
                result["item_names"] = names
        print(json.dumps(result, separators=(",", ":"), ensure_ascii=False))


if __name__ == "__main__":
    main()
