#!/usr/bin/env python3
"""Search and inspect the compact native Run & Bun capability catalog."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from games.run_and_bun.capabilities import default_registry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--search", metavar="QUERY")
    group.add_argument("--inspect", metavar="NAME")
    group.add_argument("--authorize", nargs=2, metavar=("INTENT", "PROPOSED_TOOL"))
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    registry = default_registry()
    if args.search is not None:
        result = registry.search(args.search, limit=args.limit)
    elif args.inspect is not None:
        result = registry.inspect(args.inspect)
    elif args.authorize is not None:
        result = registry.authorize_fallback(args.authorize[0], args.authorize[1])
    else:
        result = [registry.inspect(name) for name in registry.names()]
    print(json.dumps(result, indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":"), ensure_ascii=False))


if __name__ == "__main__":
    main()
