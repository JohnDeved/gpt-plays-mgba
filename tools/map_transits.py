"""Discover or execute ROM-identified scripted map transit actors."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from client.mgba_rpc import MGBA
from games.runbun import RunBunAdapter


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true")
    group.add_argument("--local-id", type=int)
    group.add_argument("--graphics-id", type=int)
    parser.add_argument("--expected-destination", nargs=2, type=int, metavar=("GROUP", "NUMBER"))
    args = parser.parse_args()

    with MGBA(timeout=20) as gba:
        adapter = RunBunAdapter(gba)
        if args.list:
            print(json.dumps(adapter.live_transit_options(), separators=(",", ":")))
            return
        result = adapter.travel_live_transit(
            local_id=args.local_id,
            graphics_id=args.graphics_id,
            expected_destination=tuple(args.expected_destination) if args.expected_destination else None,
        )
        print(json.dumps({
            "verified": result["verified"],
            "source_map": result["source_map"],
            "selected": result["selected"],
            "dialogue": result["dialogue"],
            "destination": result["destination"],
            "state": result["state"].get("map"),
        }, separators=(",", ":")))


if __name__ == "__main__":
    main()
