# Run & Bun adapter

ROM-specific control and observation for **Pokémon Run & Bun v1.07**. The mGBA Lua/RPC layer remains generic.

## Modules

- `state.py` — SaveBlocks, party decryption, battle structs, menus, movement/dialogue helpers.
- `visual.py` — framebuffer state classification.
- `battle_driver.py` — acknowledged turn/switch/faint state machine.
- `navigation.py` — transition-aware stepping plus live-map-grid route proposals.
- `symbols.json` — addresses/layouts verified against this exact ROM.

## Battle state lessons

The battle driver treats these as first-class states rather than animation noise:

- teal battle-intro text before HUDs exist;
- move selection accepted vs move actually executed;
- tactical opponent changes vs faint replacements;
- forced replacement after our active Pokémon faints;
- trapping/switch rejection (observed with Bind);
- mutable persistent party order during battle.

Because switching can move the active Pokémon to party index 0, semantic switching targets **species/current identity**, then resolves its current party index when the Party screen opens. Stale slot numbers are not stable battle identities.

## Live map grid

Run & Bun's current `gBackupMapLayout` is verified at `0x03005DD0`.

For Route 104 the live struct reported a padded grid of 55×94 for a 40×80 layout, with the backing map in EWRAM. Each `u16` entry packs metatile/collision/elevation information.

`LiveMapGrid` snapshots that running buffer:

```python
grid = LiveMapGrid(game)
cell = grid.cell(x, y)
path = grid.collision_path(game.player().position, targets={(11, 38)})
```

This is intentionally a **proposal** path. `Navigator.step_or_event()` remains authoritative for object events, trainers, scripts, wild encounters, directional metatile behavior, ledges and map changes.

The same live-grid stack was successfully used after a complete emulator/sandbox recovery to route the restored healed state from Petalburg Pokémon Center back to Route 104.

## Recovery contract

A stable gameplay milestone should have two representations:

1. GitHub records the logical state, interface discoveries and next objective.
2. Google Drive stores the physical savestate/runtime backup.

The recovered post-Mikey checkpoint was verified live as:

- Petalburg Pokémon Center `(8,4)`, position `(7,4)`;
- Turtwig Lv12 38/38;
- Starly Lv12 31/31;
- Venipede Lv5 19/19;
- all healthy, full PP.

After verification, the navigator exited Petalburg and re-entered Route 104 at `(39,63)`. This confirms the recovery path is reproducible rather than merely documented.

## Visual classifier

Current explicit modes include normal/HUD-less battle text, command/move menus, Bag, Start including dimmed transitions, Party, Poké Mart, and overworld dialogue.

False-positive fixes discovered during play include exact Party-panel colors (grass shared the broad olive palette) and right-localized gray for the dimmed Start menu (whole-screen fades can also become neutral gray).

## Current playthrough milestone

Latest stable logical state: **Route 104 `(39,63)` after defeating Triathlete Mikey and fully healing in Petalburg**.

Party:

- Turtwig Lv12 — 38/38 — Bite / Growl / Absorb / Confide
- Starly Lv12 — 31/31 — Tackle / Growl / Quick Attack / Aerial Ace
- Venipede Lv5 — 19/19 — Rollout / Poison Sting

Mikey verified:

- Krabby Lv9 — Aqua Jet / Stomp / Mud Shot
- Yanma Lv9 — Acrobatics / Sonic Boom
- Clobbopus Lv9 — Rock Smash / Bind / Detect

Next objective: use the **live Route 104 grid** to reach Petalburg Woods, with every proposed edge validated by the emulator and any trainer/script/wild-battle transition treated as an interrupt.

## Version rule

Every raw address/layout here is evidence for **Run & Bun v1.07** only. Validate ROM identity and live behavior before using it with another build.
