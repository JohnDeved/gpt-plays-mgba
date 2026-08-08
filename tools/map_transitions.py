"""Inspect and select verified direct map connections.

Examples:
  python3 tools/map_transitions.py --list
  python3 tools/map_transitions.py --direction south
  python3 tools/map_transitions.py --destination 0 11
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from client.mgba_rpc import MGBA
from games.runbun import RunBunAdapter


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="inspect transitions without moving")
    parser.add_argument("--direction", choices=("north", "south", "east", "west"))
    parser.add_argument("--destination", nargs=2, type=int, metavar=("GROUP", "NUMBER"))
    parser.add_argument("--max-candidates", type=int, default=24)
    args = parser.parse_args()
    if not args.list and args.direction is None and args.destination is None:
        parser.error("use --list or select --direction/--destination")

    with MGBA(timeout=15) as gba:
        adapter = RunBunAdapter(gba)
        if args.list:
            print(json.dumps(adapter.live_map_transitions(), separators=(",", ":")))
            return
        result = adapter.travel_live_transition(
            direction=args.direction,
            destination=tuple(args.destination) if args.destination else None,
            max_candidates=args.max_candidates,
        )
        print(json.dumps(result, separators=(",", ":"), default=str))


if __name__ == "__main__":
    main()
