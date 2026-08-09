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
elif [[ -x "$PROJECT_ROOT/runtime/mGBA.app/Contents/MacOS/mGBA" ]]; then
  EMULATOR="$PROJECT_ROOT/runtime/mGBA.app/Contents/MacOS/mGBA"
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

RUNTIME_DIR="${MGBA_RUNTIME_DIR:-$PROJECT_ROOT/runtime/session}"
mkdir -p "$RUNTIME_DIR"
export MGBA_RUNTIME_DIR="$RUNTIME_DIR"
READY_FILE="${MGBA_RPC_READY_FILE:-$RUNTIME_DIR/mgba_rpc_ready.txt}"
UI_READY_FILE="${MGBA_UI_READY_FILE:-$RUNTIME_DIR/mgba_ui_ready.txt}"
rm -f -- "$READY_FILE" "$UI_READY_FILE"
RPC_PORT="${MGBA_RPC_PORT:-8765}"
if [[ "$RPC_PORT" != <-> ]] || (( RPC_PORT < 1 || RPC_PORT > 65535 )); then
  print -u2 "MGBA_RPC_PORT must be an integer from 1 to 65535; got: $RPC_PORT"
  exit 2
fi
export MGBA_RPC_PORT="$RPC_PORT"
FPS_TARGET="${MGBA_FPS_TARGET:-59.7275}"
START_STATE="${MGBA_START_STATE:-}"
UNCAPPED="${MGBA_UNCAPPED:-0}"
MUTE="${MGBA_MUTE:-1}"
if [[ "$MUTE" != "0" && "$MUTE" != "1" ]]; then
  print -u2 "MGBA_MUTE must be 0 or 1; got: $MUTE"
  exit 2
fi
FOREGROUND="${MGBA_FOREGROUND:-1}"
if [[ "$FOREGROUND" != "0" && "$FOREGROUND" != "1" ]]; then
  print -u2 "MGBA_FOREGROUND must be 0 or 1; got: $FOREGROUND"
  exit 2
fi
# An uncapped automation process must not render/emulate thousands of frames
# per second between controller requests. The Lua bridge stops mGBA whenever
# no frame-based input/experiment/wait is active; the Python client resumes it
# immediately before its next request. Ordinary capped/interactive launches
# remain unchanged unless the caller opts in explicitly.
IDLE_STOP="${MGBA_IDLE_STOP:-$UNCAPPED}"
export MGBA_IDLE_STOP="$IDLE_STOP"
# zsh keeps the same PID across exec, so Lua can stop the exact emulator
# process without broad process-name matching.
export MGBA_PROCESS_PID="$$"

ARGS=(
  -C "mute=$MUTE"
  -C "fpsTarget=$FPS_TARGET"
  -C "scriptingOpen=0"
  --script "$PROJECT_ROOT/scripts/mgba_rpc.lua"
)
if [[ "$UNCAPPED" == "1" ]]; then
  # The recovered Route 109 controller ran reliably with frontend audio/video
  # synchronization disabled. Input remains frame-synchronized by the Lua
  # bridge; only host-side pacing is removed.
  ARGS=(
    -C "audioSync=0"
    -C "videoSync=0"
    "${ARGS[@]}"
  )
fi
if [[ -n "$START_STATE" ]]; then
  if [[ ! -f "$START_STATE" ]]; then
    print -u2 "savestate not found: $START_STATE"
    exit 1
  fi
  ARGS+=( -t "$START_STATE" )
fi

if [[ "$FOREGROUND" == "1" ]]; then
  # mGBA's Qt frontend unconditionally shows a top-level Scripting window
  # before loading a --script file. The ready file proves that window and the
  # Lua bridge both exist; close that specific helper, raise gameplay, and
  # only then publish UI readiness to clients that may pause the process.
  (
    for _ in {1..250}; do
      [[ -s "$READY_FILE" ]] && break
      sleep 0.02
    done
    if [[ -s "$READY_FILE" ]]; then
      /usr/bin/osascript - "$MGBA_PROCESS_PID" >/dev/null 2>&1 <<'APPLESCRIPT'
on run argv
  set targetPid to (item 1 of argv) as integer
  tell application "System Events"
    tell first application process whose unix id is targetPid
      repeat with helperWindow in (every window whose name is "Scripting")
        perform action "AXPress" of (first button of helperWindow whose subrole is "AXCloseButton")
      end repeat
      perform action "AXRaise" of (first window whose name starts with "mGBA")
      set frontmost to true
    end tell
  end tell
end run
APPLESCRIPT
      # Qt animates/destroys the helper asynchronously. Clients must not
      # SIGSTOP mGBA until that event has completed.
      sleep 0.75
      touch "$UI_READY_FILE"
    fi
  ) &!
fi

exec "$EMULATOR" "${ARGS[@]}" "$ROM_PATH"
