#!/usr/bin/env python3
"""Call one Run & Bun capability without hand-writing JSON-RPC plumbing."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="capability name, for example game_observe")
    parser.add_argument("--args", default="{}", help="JSON object of capability arguments")
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
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "runbun_mcp.py")],
        input=json.dumps(request) + "\n",
        text=True,
        capture_output=True,
        cwd=ROOT,
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
    print(json.dumps(result_body.get("structuredContent", {}), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
