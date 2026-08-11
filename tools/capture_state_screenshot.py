#!/usr/bin/env python3
"""Capture one screenshot from a disposable savestate clone."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client.mgba_clone import disposable_clone


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("state", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
args.output.parent.mkdir(parents=True, exist_ok=True)
with disposable_clone(args.state) as gba:
    print(gba.screenshot(args.output.resolve()))
