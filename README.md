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
- Pokédex / Running Shoes: received
- Wally catching tutorial / Petalburg Gym intro: complete
- Route 102 trainer wins include Calvin, Rick and Tiana
- current area: **Petalburg City**, after a Route 104 scouting/capture detour
- party:
  - **Turtwig Lv12** — Bite / Growl / Absorb / Confide
  - **Starly Lv12** — Tackle / Growl / Quick Attack / Aerial Ace
  - **Venipede Lv5** — Rollout / Poison Sting
- next objective: heal the three-mon party, then re-enter Route 104 on the progression path to **Petalburg Woods**

Machine-readable progress lives in `data/session_progress.json`.

## Architecture

```text
mGBA
  └─ scripts/mgba_rpc.lua          frame-synchronized emulator RPC
       └─ client/mgba_rpc.py       generic Python client
            └─ games/run_and_bun/
                 ├─ state.py       ROM-specific RAM/state decoder + actions
                 ├─ visual.py      framebuffer UI-state classifier
                 ├─ battle_driver.py acknowledged battle state machine
                 └─ symbols.json   verified v1.07 addresses/layout notes
```

Navigation experiments remain under `tools/`. Runtime artifacts such as ROMs, savestates, screenshots, debug captures, and full workspace archives are intentionally excluded from Git and stored separately in the Google Drive workspace.

## RPC v0.3

The Lua bridge provides NDJSON request IDs, frame-synchronized input, memory reads/writes/ranges, snapshots/diffs, watches, frame waits, exact-frame savestate experiments, mGBA-native screenshots, save/load states and reset.

The bridge stays game-agnostic. Strategy and ROM-specific interpretation stay in Python.

## Run & Bun state adapter

`games/run_and_bun/state.py` currently understands:

- dynamic SaveBlock1 / SaveBlock2 / Pokémon storage pointers
- player name, gender, map ID and exact coordinates
- persistent encrypted party structures with checksum validation
- status conditions
- live battle stats, HP, types, abilities, moves, PP and stat stages
- battle action/move cursors and the generic menu cursor
- semantic Start-menu selection
- coordinate-conditioned one-tile movement (`step_tile`)
- dialogue readiness and sustained free-overworld detection
- verified map IDs through Petalburg City / Route 104
- verified species/moves encountered so far, including Starly and Venipede

All raw addresses in `symbols.json` are scoped to Run & Bun **v1.07**.

## Battle driver

`games/run_and_bun/battle_driver.py` is built from actual fights and failures. It supports:

- trainer dialogue -> battle transition
- command/move-menu synchronization
- PP-acknowledged move submission
- separation of **selection accepted** vs **move executed**
- enemy move inference from opponent PP deltas
- HP/PP/EXP/species transition logging
- KO replacement vs tactical switching using EXP gain
- voluntary party switches
- **forced replacement after the active Pokémon faints**
- active-battler-aware faint handling (never assumes party slot 0 is active)
- safe post-battle return to overworld

## Visual state machine

`visual.py` is intentionally not OCR-first. RAM supplies exact facts; framebuffer regions identify UI mode.

Recognized states include battle HUD/text/command/move menus, Bag, Start menu (including the dimmed transition), Party, Poké Mart and ordinary dialogue.

A Route 104 bug led to an important precision fix: Party-screen detection now keys off the exact dominant Party UI panel colors instead of a broad olive range, because dense grass can share similar hues.

## Navigation

Navigation combines:

1. static Emerald map data as a proposal graph;
2. live Run & Bun coordinates as authority;
3. directed edges for ledges/asymmetric movement;
4. menu/script/battle transitions as interrupts;
5. objective-specific forbidden transitions (for example, town building warps when the goal is to leave town);
6. savestate-backed offline probing for ambiguous route geometry.

`step_tile(direction)` sends short pulses and stops as soon as decoded coordinates/map change, avoiding turn-only taps and multi-tile overshoot.

## Gameplay discoveries that changed the interface

- Run & Bun's Bag has multiple pockets; an empty visible pocket does not mean inventory is empty.
- **Endless Candy** exists for controlled level preparation.
- **Repellent** exists, but it is not treated as a guaranteed encounter-off switch.
- First-time catches pass through Pokédex registration and nickname UI before party count changes.
- Trainer AI can switch healthy Pokémon, so team order is state-dependent.
- Battle transitions and animations can temporarily hide HUDs; battle exit must be sustained/confirmed.
- Field poison fades and dense grass can resemble UI palettes; transient/terrain frames must not be mistaken for menus.
- Route 104 has ledge-separated subregions; offline directed-edge probing is useful before committing a poisoned live party to exploration.

## Sandbox launch

```bash
chmod +x mGBA-build-latest-appimage-x64.appimage
./mGBA-build-latest-appimage-x64.appimage --appimage-extract

Xvfb :99 -screen 0 1024x768x24 &
DISPLAY=:99 ./squashfs-root/AppRun \
  --script scripts/mgba_rpc.lua \
  /path/to/game.gba
```

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

Whenever gameplay exposes a recurring weakness, improve the interface and sync the proven change to GitHub at the next stable checkpoint instead of repeatedly working around it.
