# gpt-plays-mgba

A control, observation, and game-state layer for playing Pokémon Run & Bun through a development build of mGBA.

The project has two parallel goals:

1. Complete Pokémon Run & Bun using normal emulator inputs.
2. Continuously improve the interface so later gameplay needs fewer fragile screenshot/button round trips.

The current sandbox-tested environment uses mGBA development build `0.11-9122-afd6f14ea` and Pokémon Run & Bun v1.07.

## Current interface: RPC v0.3

The canonical emulator bridge is `scripts/mgba_rpc.lua`, with the Python client in `client/mgba_rpc.py`.

RPC v0.3 provides:

- NDJSON request IDs and capability negotiation
- frame-synchronized queued input sequences
- 8/16/32-bit memory reads and writes
- bulk `read_range`
- memory snapshots and diffs
- named memory watches
- emulator-frame conditional waits
- atomic savestate experiments with exact-frame captures
- mGBA-native screenshots
- savestate save/load and reset

## Run & Bun adapter

ROM-specific code lives under `games/run_and_bun/`.

It currently decodes and controls:

- dynamic SaveBlock1 / SaveBlock2 / Pokémon storage pointers
- player name, gender, map and coordinates
- persistent Gen III encrypted party data with checksum validation
- battle battler stats, HP, types, moves, PP, action cursor and move cursor
- generic Yes/No selection
- dialogue readiness from framebuffer state
- battle HUD / command-menu / move-menu visual state

Verified addresses are stored in `games/run_and_bun/symbols.json` and are explicitly scoped to Run & Bun v1.07.

## Navigation experiments

`tools/nav_probe.py` is the early savestate-assisted collision explorer.

`tools/nav_route101_live.py` demonstrates the newer navigation model: static Emerald map/collision data proposes a route, while live decoded coordinates verify each directed edge. ROM-specific obstacles, one-way ledges, NPCs and wild battles override the static model.

## Current playthrough snapshot

Machine-readable current progress is stored in `data/session_progress.json`.

Runtime artifacts such as ROMs, saves, screenshots and savestates are intentionally kept out of Git. They live in the separate Google Drive workspace.

## Sandbox launch

The sandbox has no real X server, so mGBA runs under Xvfb. The AppImage is extracted because FUSE is unavailable.

```bash
chmod +x mGBA-build-latest-appimage-x64.appimage
./mGBA-build-latest-appimage-x64.appimage --appimage-extract

Xvfb :99 -screen 0 1024x768x24 &
DISPLAY=:99 ./squashfs-root/AppRun \
  --script scripts/mgba_rpc.lua \
  /path/to/game.gba
```

Then:

```python
from client.mgba_rpc import MGBA

with MGBA() as gba:
    print(gba.info())
    gba.press("A")
    print(gba.read_range(0x02000000, 32).hex())
```

## Design rule

Prefer one structured observation followed by one reasoned action. Whenever gameplay exposes a weakness in the interface, improve the interface rather than repeatedly working around it.
