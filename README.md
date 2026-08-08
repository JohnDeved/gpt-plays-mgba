# gpt-plays-mgba

A control, observation, reverse-engineering, and gameplay layer for playing **Pokémon Run & Bun v1.07** through a development build of mGBA.

The project has two equal goals: complete the game with normal emulator inputs, and continuously improve the interface so later gameplay becomes structured, deterministic, state-aware, and cheaper in emulator round trips.

## Current status

- mGBA development build: `0.11-9122-afd6f14ea`
- RPC bridge: **v0.3**
- player: **GPT**
- May Route 103: defeated
- Pokédex / Running Shoes / Wally tutorial: complete
- Route 102 crossed; Calvin, Rick, Tiana and other early trainers defeated
- Petalburg reached
- Route 104 in progress
- **Triathlete Mikey defeated**
- party: Turtwig Lv12 / Starly Lv12 / Venipede Lv5, currently fully healed
- current position: **Route 104 `(39,63)`**, re-entered from Petalburg after healing
- current logical checkpoint: `route104_post_mikey_reentry.ss0`

Machine-readable progress lives in `data/session_progress.json`. Runtime artifacts (ROM, savestates, screenshots, debug captures, full workspace archives) live outside Git in the Google Drive workspace.

## Recovery is part of the interface

The sandbox process can be recycled, so project recovery is now treated as a first-class capability rather than an exceptional manual procedure.

The post-Mikey healed savestate recovered from Google Drive was loaded and verified against the GitHub logical checkpoint: Petalburg Pokémon Center `(8,4)` at `(7,4)`, Turtwig 38/38, Starly 31/31, Venipede 19/19, all healthy with full PP. From that restored state the live-map navigator successfully exited Petalburg and re-entered Route 104 at `(39,63)`.

This gives the project a tested recovery chain:

`GitHub logical state -> Drive savestate -> mGBA RPC verification -> acknowledged navigation -> new stable checkpoint`.

## Architecture

```text
mGBA
  └─ scripts/mgba_rpc.lua
       └─ client/mgba_rpc.py
            └─ games/run_and_bun/
                 ├─ state.py
                 ├─ visual.py
                 ├─ battle_driver.py
                 ├─ navigation.py
                 └─ symbols.json
```

The Lua bridge stays game-agnostic. Python turns emulator primitives into Run & Bun state/actions.

## RPC v0.3

The bridge provides NDJSON request IDs, frame-synchronized input, memory reads/writes/ranges, snapshots/diffs, watches, frame waits, exact-frame savestate experiments, mGBA-native screenshots, save/load states and reset.

## Observation model

The Run & Bun layer combines:

1. **RAM** for exact facts such as coordinates, HP, PP, party data, battle stats and stat stages.
2. **Framebuffer regions** for UI mode such as dialogue, battle menus, Bag, Start, Party and Shop.
3. **Action acknowledgement** from PP/HP/EXP/species/coordinate changes.

No single channel is trusted universally. Battle intros can show a teal message box before either HUD exists; transitions can temporarily remove HUDs; field fades can resemble dimmed menus; coordinates remain populated while menus are open.

## Live-map navigation

The current navigation backend reads **Run & Bun's actual live map grid from EWRAM** rather than treating vanilla Emerald `map.bin` as authoritative.

Verified v1.07 symbol:

```text
gBackupMapLayout = 0x03005DD0
```

The structure contains padded map width/height and a live `u16 *map` pointer. Entries encode a 10-bit metatile ID, 2-bit collision value, and 4-bit elevation. SaveBlock player coordinates are layout-space; the engine adds a 7-tile map-buffer offset internally.

`LiveMapGrid.collision_path()` uses the running hack's collision data as a shortest-path proposal graph. Every edge is still executed through `Navigator.step_or_event()`, so NPCs, trainers, scripts, directional metatile behavior, ledges, map transitions and wild battles override the static proposal.

The same mechanism has now been validated across a sandbox recovery: it routed the restored healed state through Petalburg to the Route 104 connection without using hard-coded button timing.

## Battle driver

The acknowledged battle state machine supports:

- HUD-less trainer/wild intro text
- command/move synchronization
- PP-acknowledged move submission
- separation of selection accepted vs move executed
- enemy move inference from opponent PP deltas
- voluntary switching by current Pokémon identity
- forced faint replacement
- switch failure/trapping detection
- HP/PP/species transition logs
- post-battle confirmation

A key Route 104 discovery is that **party order is mutable during battle**: after switching, the active Pokémon can occupy party slot 0. Therefore switching code targets species/current identity instead of stale slot numbers.

## Verified Route 104 trainer data

Triathlete Mikey exposed several useful cases:

- Krabby Lv9 — Aqua Jet / Stomp / Mud Shot
- Yanma Lv9 — Acrobatics / Sonic Boom
- Clobbopus Lv9 — Rock Smash / Bind / Detect
- Bind can prevent a voluntary switch
- tactical opponent changes must not automatically be classified as KOs

## Development rule

Prefer **one structured observation -> one reasoned decision -> one acknowledged action**.

Whenever gameplay exposes a recurring weakness, improve the interface and sync the proven change to GitHub at the next stable checkpoint rather than repeatedly working around it.
