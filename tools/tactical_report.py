#!/usr/bin/env python3
"""Print one compact RAM-backed tactical battle report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from client.mgba_rpc import MGBA
from games.runbun import RunBunAdapter


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pretty", action="store_true", help="indent JSON for humans")
    args = parser.parse_args()
    with MGBA(timeout=15) as gba:
        adapter = RunBunAdapter(gba)
        observation = adapter.observe()
        report = adapter.explain_battle_action(
            observation,
            damage_memory=adapter._damage_memory,
        )
    print(json.dumps(report, separators=(",", ":") if not args.pretty else None, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
