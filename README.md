# gpt-plays-mgba

A control, observation, reverse-engineering, and gameplay layer for playing **Pokémon Run & Bun v1.07** through a development build of mGBA.

This project has two equal goals:

1. Complete the game using normal emulator inputs.
2. Continuously improve the interface so later gameplay becomes more structured, deterministic, and state-aware.

The current sandbox-tested emulator is mGBA development build `0.11-9122-afd6f14ea`. The live bridge is **RPC v0.3**.

## Current playthrough

At the latest source sync:

- player: **GPT**
- May's first Route 103 rival battle: **won**
- Pokédex: received
- Running Shoes: received
- current area: **Route 102**
- Youngster Calvin: **defeated**
- Bug Catcher Rick: **defeated**
- party:
  - **Turtwig Lv11** — Bite / Growl / Absorb / Confide
  - **Starly Lv11** — Tackle / Growl / Quick Attack / Aerial Ace

Machine-readable progress lives in `data/session_progress.json`.

## Architecture

```text
mGBA
  └─ scripts/mgba_rpc.lua          frame-synchronized emulator RPC
       └─ client/mgba_rpc.py       generic Python client
            └─ games/run_and_bun/
                 ├─ state.py       ROM-specific RAM/state decoder + actions
                 ├─ visual.py      framebuffer UI-state classifier
                 ├─ battle_driver.py adaptive battle state machine
                 └─ symbols.json   verified v1.07 addresses/layout notes
```

Navigation experiments remain under `tools/`. Runtime artifacts such as ROMs, savestates, screenshots, debug captures, and full workspace archives are intentionally excluded from Git and stored separately in the Google Drive workspace.

## RPC v0.3

The Lua bridge provides:

- NDJSON request IDs and capability negotiation
- frame-synchronized queued input
- 8/16/32-bit reads and writes
- range reads
- snapshots and diffs
- named memory watches
- emulator-frame conditional waits
- exact-frame savestate experiments
- mGBA-native screenshots
- savestate save/load
- reset

The bridge is deliberately game-agnostic. Strategy and ROM-specific interpretation stay in Python.

## Run & Bun state adapter

`games/run_and_bun/state.py` currently understands:

- dynamic SaveBlock1 / SaveBlock2 / Pokémon storage pointers
- player name, gender, map ID and exact coordinates
- persistent Gen III encrypted party structures with checksum verification
- status conditions
- live battle battlers, stats, HP, types, abilities, moves and PP
- battle stat stages including accuracy/evasion
- battle action and move cursors
- generic menu cursor used by Yes/No and Start-menu selection
- semantic Start-menu selection
- coordinate-conditioned one-tile movement (`step_tile`)
- dialogue readiness and sustained free-overworld detection

All addresses in `symbols.json` are explicitly scoped to Run & Bun **v1.07**.

## Battle driver

`games/run_and_bun/battle_driver.py` was built from actual failed and successful fights rather than from a planned abstraction alone.

It currently supports:

- trainer dialogue -> battle transition
- command/move-menu synchronization
- PP-acknowledged move submission
- distinction between **selection accepted** and **move executed**
- enemy move inference from opponent PP deltas
- HP, PP, EXP and species transition logging
- KO replacement vs tactical-switch classification using EXP gain
- party switching with RAM acknowledgement
- safe post-battle return to overworld

This machinery was used to beat Calvin and Rick and to diagnose the original May loss.

## Visual state machine

`visual.py` is intentionally not OCR-first. It recognizes stable UI classes from framebuffer regions and palette structure, while RAM supplies exact game facts.

Currently recognized states include:

- battle HUD
- battle message box
- battle command menu
- move menu
- Bag
- Start menu
- **dimmed Start-menu transition**
- party screen
- Poké Mart screens
- standard overworld dialogue

The rule is simple: decoded coordinates alone never prove that the player is free to move, because coordinates remain valid while menus and scripts are active.

## Navigation

The navigation model combines:

1. static Emerald map/collision data as a proposal graph;
2. live Run & Bun coordinates as the authority;
3. directed edges for ledges and asymmetric movement;
4. battle/menu/script detection as interrupts.

`step_tile(direction)` uses short input pulses and stops immediately when decoded coordinates/map change. This avoids the old bug where one tap merely turned the avatar or a long hold accidentally walked multiple tiles.

## Gameplay discoveries that affect the interface

- Run & Bun's Bag has multiple pockets; seeing an empty pocket does **not** mean the inventory is empty.
- The hack provides an **Endless Candy** key item, useful for controlled level preparation without wild grinding.
- A reusable **Repellent** key item exists for encounter management.
- First-time catches pass through Pokédex registration and nickname UI before party count changes.
- Trainer AI can switch healthy Pokémon, so enemy team order is state-dependent.
- Some battle animations temporarily hide HUD regions; battle exit must be sustained/confirmed rather than inferred from one frame.

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

Python:

```python
from client.mgba_rpc import MGBA
from games.run_and_bun import RunBun, BattleDriver

with MGBA() as gba:
    game = RunBun(gba)
    print(game.observe(screenshot=True))
    game.step_tile("LEFT")
```

## Development rule

Prefer **one structured observation -> one reasoned decision -> one acknowledged action**.

Whenever gameplay exposes a recurring weakness, improve the interface instead of repeatedly working around it.
