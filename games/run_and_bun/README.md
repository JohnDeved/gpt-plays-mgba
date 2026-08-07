# Run & Bun adapter

ROM-specific control and observation for **Pokémon Run & Bun v1.07**. The mGBA Lua/RPC layer remains generic.

## Modules

- `state.py` — SaveBlocks, party decryption, battle structs, menus, movement/dialogue helpers.
- `visual.py` — framebuffer state classification.
- `battle_driver.py` — acknowledged turn/switch/faint state machine.
- `navigation.py` — transition-aware stepping plus live-map-grid route proposals.
- `symbols.json` — addresses/layouts verified against this exact ROM.

## Battle state lessons

The battle driver now treats the following as first-class states rather than animation noise:

- teal **battle intro text before HUDs exist**;
- move selection accepted vs move actually executed;
- tactical opponent changes vs faint replacements;
- forced replacement after our active Pokémon faints;
- trapping/switch rejection (observed with Bind);
- mutable persistent party order during battle.

Because switching can move the active Pokémon to party index 0, semantic switching now targets **species/current identity**, then resolves its current party index when the Party screen opens. Stale slot numbers are not stable battle identities.

## Live map grid

Run & Bun's current `gBackupMapLayout` was located at `0x03005DD0`.

For Route 104 the live struct reported a padded grid of 55×94 for a 40×80 layout, with the backing map in EWRAM. Each u16 grid entry packs metatile/collision/elevation exactly as the Gen III engine expects.

`LiveMapGrid` snapshots that running buffer and exposes:

```python
grid = LiveMapGrid(game)
cell = grid.cell(x, y)
path = grid.collision_path(game.player().position, targets={(11, 38)})
```

This is intentionally a **proposal** path. `Navigator.step_or_event()` remains authoritative for object events, trainers, scripts, wild encounters, directional metatile behavior, ledges and map changes.

The result is much stronger than using vanilla Emerald map data as truth: the planner observes the hack's actual live metatile edits, while still learning dynamic blocked/event edges from the emulator.

## Visual classifier

Current explicit modes include:

- normal battle HUD / battle textbox
- HUD-less battle intro textbox
- battle command / move menus
- Bag
- Start menu, including its dimmed transition
- Party
- Poké Mart
- overworld dialogue

False-positive fixes discovered during play include exact Party-panel colors (grass shared the broad olive palette) and right-localized gray for the dimmed Start menu (whole-screen fades can also become neutral gray).

## Current playthrough milestone

Latest stable checkpoint: **Route 104 after defeating Triathlete Mikey**.

Mikey verified:

- Krabby Lv9 — Aqua Jet / Stomp / Mud Shot
- Yanma Lv9 — Acrobatics / Sonic Boom
- Clobbopus Lv9 — Rock Smash / Bind / Detect

The fight also demonstrated why a third party member matters: Venipede resisted Clobbopus's Fighting attacks, poisoned it, and forced the driver to handle trapping and mutable party order correctly.

Current next action is to heal after Mikey and resume the live-grid route toward Petalburg Woods.

## Version rule

Every raw address/layout here is evidence for **Run & Bun v1.07** only. Validate ROM identity and live behavior before using it with another build.
