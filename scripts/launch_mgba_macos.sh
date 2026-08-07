#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="${0:A:h:h}"
ROM_PATH="${1:-}"

if [[ -z "$ROM_PATH" ]]; then
  print -u2 "usage: $0 /path/to/game.gba"
  exit 2
fi
if [[ ! -f "$ROM_PATH" ]]; then
  print -u2 "ROM not found: $ROM_PATH"
  exit 1
fi

if [[ -n "${MGBA_BIN:-}" ]]; then
  EMULATOR="$MGBA_BIN"
elif [[ -x "$PROJECT_ROOT/../runtime/mGBA.app/Contents/MacOS/mGBA" ]]; then
  EMULATOR="$PROJECT_ROOT/../runtime/mGBA.app/Contents/MacOS/mGBA"
elif [[ -x "/Applications/mGBA.app/Contents/MacOS/mGBA" ]]; then
  EMULATOR="/Applications/mGBA.app/Contents/MacOS/mGBA"
elif (( $+commands[mGBA] )); then
  EMULATOR="${commands[mGBA]}"
else
  print -u2 "mGBA not found. Set MGBA_BIN or install a native macOS development build."
  exit 1
fi

if [[ ! -x "$EMULATOR" ]]; then
  print -u2 "mGBA is not executable: $EMULATOR"
  exit 1
fi

RUNTIME_DIR="${MGBA_RUNTIME_DIR:-$PROJECT_ROOT/../runtime/session}"
mkdir -p "$RUNTIME_DIR"
export MGBA_RUNTIME_DIR="$RUNTIME_DIR"

exec "$EMULATOR" --script "$PROJECT_ROOT/scripts/mgba_rpc.lua" "$ROM_PATH"
