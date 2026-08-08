"""Seek a live overworld NPC by object-event identity.

Examples:
  python3 tools/seek_npc.py --list
  python3 tools/seek_npc.py --graphics-id 20 --interact
  python3 tools/seek_npc.py --local-id 1 --interact
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from client.mgba_rpc import MGBA
from games.runbun import RunBunAdapter


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slot", type=int)
    parser.add_argument("--local-id", type=int)
    parser.add_argument("--graphics-id", type=int)
    parser.add_argument("--interact", action="store_true")
    parser.add_argument("--grass-penalty", type=int, default=100)
    parser.add_argument("--list", action="store_true", help="print discovered active objects and exit")
    args = parser.parse_args()

    with MGBA(timeout=15) as gba:
        adapter = RunBunAdapter(gba)
        state = adapter.observe()
        print("MAP", state.get("map"), "MODE", state.get("mode"))
        print("OBJECTS")
        for obj in state.get("objects", []):
            print(
                {
                    key: obj[key]
                    for key in ("slot", "is_player", "local_id", "graphics_id", "movement_type", "position", "facing_direction")
                }
            )
        if args.list:
            return
        if not any(value is not None for value in (args.slot, args.local_id, args.graphics_id)):
            parser.error("one of --slot, --local-id, or --graphics-id is required unless --list is used")
        result = adapter.follow_live_path_to_npc(
            slot=args.slot,
            local_id=args.local_id,
            graphics_id=args.graphics_id,
            interact=args.interact,
            grass_penalty=args.grass_penalty,
        )
        target = result.get("target") or {}
        print(
            "RESULT",
            result.get("reason"),
            "target",
            {key: target.get(key) for key in ("slot", "local_id", "graphics_id", "position")},
            "player",
            result.get("state", {}).get("map"),
        )
        text = (result.get("state", {}).get("text") or {}).get("current") or {}
        if text.get("text"):
            print("TEXT", text["text"])


if __name__ == "__main__":
    main()
