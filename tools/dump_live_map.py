"""Dump the currently loaded map layout from RAM and optionally query a path."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from client.mgba_rpc import MGBA
from games.run_and_bun.live_map import read_live_map
from games.runbun import RunBunAdapter


def parse_position(value: str) -> tuple[int, int]:
    try:
        x, y = (int(part, 0) for part in value.split(",", 1))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("position must be X,Y") from exc
    return x, y


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=parse_position, help="also calculate a RAM-only path to X,Y")
    parser.add_argument("--no-tiles", action="store_true", help="omit the expanded per-tile JSON matrix")
    parser.add_argument("--no-ascii", action="store_true", help="omit the compact ASCII layout")
    parser.add_argument("--json", action="store_true", help="emit one JSON document")
    args = parser.parse_args()

    with MGBA(timeout=15) as gba:
        adapter = RunBunAdapter(gba)
        state = adapter.observe()
        live = read_live_map(gba)
        current = (state["map"]["x"], state["map"]["y"])
        result = {
            "map": state["map"],
            "mode": state["mode"],
            "layout": live.layout(
                include_tiles=not args.no_tiles,
                include_ascii=not args.no_ascii,
            ),
        }
        if args.target is not None:
            result["path"] = {
                "start": current,
                "target": args.target,
                "directions": live.path_to(current, args.target, allow_nonwalkable_start=True),
            }
            if not args.no_ascii:
                result["layout"]["ascii"] = live.ascii(start=current, target=args.target)
        if args.json:
            print(json.dumps(result, separators=(",", ":")))
        else:
            print("MAP", result["map"], "MODE", result["mode"])
            print("LAYOUT", result["layout"]["width"], "x", result["layout"]["height"], "GRID", hex(result["layout"]["grid_ptr"]))
            if not args.no_ascii:
                print(result["layout"]["ascii"])
            if args.target is not None:
                print("PATH", result["path"])


if __name__ == "__main__":
    main()
