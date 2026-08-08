# Pokémon Run & Bun v1.07 — verified runtime notes

These addresses and layouts were verified directly against the supplied `Pokemon Run & Bun (v1.07).gba` while running mGBA development build `0.11-9122-afd6f14ea`.

The rule for this file is simple: vanilla Emerald / pokeemerald source is useful as a hypothesis, but an address is only listed here after a live experiment confirms it in this ROM.

## Save pointers

| Symbol | Address | Verified value / meaning |
| --- | ---: | --- |
| `gSaveBlock1Ptr` | `0x03005D9C` | Dynamic pointer to SaveBlock1 |
| `gSaveBlock2Ptr` | `0x03005DA0` | Dynamic pointer to SaveBlock2 |
| Pokémon storage pointer | `0x03005DA4` | Dynamic pointer to PC storage |

For the current save, SaveBlock1 begins with the standard positional fields:

- `+0x00`: player X, signed 16-bit
- `+0x02`: player Y, signed 16-bit
- `+0x04`: map group
- `+0x05`: map number
- `+0x06`: warp ID

SaveBlock2 begins with the player name and gender. The current player is `Ac`, male.

## UI cursors

| UI | Address | Values |
| --- | ---: | --- |
| New Game / Option title menu | `0x02023006` | `0` New Game, `1` Option |
| Generic Yes/No menu | `0x0203C3C2` | `0` Yes, `1` No |
| Battle command menu | `0x02023A1C` | `0` Fight, `1` Bag, `2` Pokémon, `3` Run |
| Battle move grid | `0x02023A20` | `0..3` move slot |

Each cursor address was isolated with same-savestate, same-final-frame atomic experiments where the only difference between branches was a D-pad input.

## Live map grid

The loaded map collision buffer is available without framebuffer probing:

- `0x03005DD0` stores a runtime map header: width `u32`, height `u32`, grid pointer `u32` at `+0x08`.
- In the local run the grid pointer was `0x020318DC`; each cell is a `u16` word.
- The active map begins after a seven-cell border. Collision bits are `10..11`; elevation bits are `12..15`.
- A cell is treated as statically walkable when collision is `0`; elevation is
  retained as a routing preference because bridges and connected-map tiles use
  elevations `0`, `1`, and `4` while still accepting movement.

The grid is read in one `memory.read_range` call and solved locally. This is
the preferred navigation source for maps that are loaded in the emulator,
because it reflects Run & Bun's modified geometry rather than vanilla map data.

## Live object events

The active overworld object table was verified at `0x02036914` for this ROM.
It contains 16 records with a `0x24`-byte stride. The layout follows the
Emerald object-event structure closely enough for the following fields to be
decoded directly:

- `+0x00`: flags (`active` bit 0, `isPlayer` bit 16, `invisible` bit 13)
- `+0x04..0x0A`: sprite, graphics, movement, trainer, local ID, map number/group
- `+0x0C`: initial X/Y, `+0x10`: current X/Y, `+0x14`: previous X/Y
- `+0x18`: facing and movement direction nibbles
- `+0x1A..0x23`: movement range and runtime movement/behavior fields

Runtime coordinates include the seven-tile camera origin, so the adapter
subtracts seven to expose map-local positions matching SaveBlock1. The
object-event seeker uses local ID, graphics ID, or slot as identity, blocks
currently occupied tiles, and re-plans after short movement chunks so a
wandering NPC is followed instead of treated as a fixed coordinate.

## Player party

- `gPlayerPartyCount`: `0x02023A95`
- `gPlayerParty`: `0x02023A98`
- Pokémon stride: `0x64` bytes

The party structure uses Gen III's encrypted 48-byte secure region. Decryption with `personality ^ otId`, the standard `personality % 24` substructure permutation, and the stored checksum has been verified against the live starter.

The local playthrough's current party after training for the first rival battle:

- Chimchar, Lv10, Scratch / Leer / Ember
- 29 / 29 HP at the Mom recovery checkpoint

The earlier remote handoff recorded this persistent party state after the Birch rescue:

- Turtwig, Lv5
- 22 / 22 HP
- Bite / Growl / Absorb
- 25 / 10 / 25 PP
- Attack 12, Defense 11, Speed 8, Sp. Atk 10, Sp. Def 13
- Friendship 70
- EXP 157 after the first fight
- decrypted checksum matches the stored checksum

## Battle structs

- `gBattleMons`: `0x020233FC`
- battler stride in this ROM: `0x5C`

Verified useful offsets inside one battler:

| Offset | Field |
| ---: | --- |
| `0x00` | species ID, u16 |
| `0x02` | Attack, u16 |
| `0x04` | Defense, u16 |
| `0x06` | Speed, u16 |
| `0x08` | Sp. Atk, u16 |
| `0x0A` | Sp. Def, u16 |
| `0x18` | 8 stat-stage bytes; neutral stage is `6` |
| `0x0C` | 4 × move IDs, u16 |
| `0x20` | ability ID, u16 |
| `0x22` | 3 × type IDs, u8 |
| `0x25` | 4 × PP, u8 |
| `0x2A` | current HP, u16 |
| `0x2C` | level, u8 |
| `0x2E` | max HP, u16 |
| `0x30` | held item ID, u16 |
| `0x32` | nickname data |
| `0x48` | experience, u32 |
| `0x4C` | personality, u32 |
| `0x50` | primary status, u32 |

## ROM battle metadata

The live battler structs provide move IDs, current PP, and type bytes. The
immutable metadata reader additionally validates the BPEE header, then caches:

- move names at ROM `0x083A4493`, 13-byte slots indexed from move 1;
- the 20-entry fixed-point effectiveness table at ROM `0x083ADEE0`;
- raw chart values are unsigned Q4.12 (`0x1000 = 1x`, `0x0800 = 0.5x`,
  `0x2000 = 2x`).

The bridge reads these with `memory.read_range`; no screenshot or hardcoded
move-name list is needed. Move types discovered from the battle menu can be
remembered by ID and combined with this cached chart.

## Inventory evidence

The berry-tree script explicitly reports “Bag's Berries Pocket” and writes
item `520` (Oran Berry) with its encrypted quantity at SaveBlock1 `+0x900`.
The vanilla-looking `+0x740` region is therefore not the authoritative
Run & Bun berries pocket.

The expanded Bag UI was isolated by placing the same encrypted slot in
candidate regions: its visible Items pocket begins at `+0x560`, and its
Poké Balls pocket begins at `+0x650`. The UI ignored the legacy shop slot at
`+0x7A4`; compact telemetry now reports both legacy forensics and `usable`
UI-pocket aliases.

The first rescue battle validated this parser against the framebuffer:

- Player: Turtwig Lv5, Shell Armor (ability ID 75), Grass / Grass / Mystery, 22/22 HP initially, Bite/Growl/Absorb.
- Opponent: Zigzagoon Lv2, internal species ID 987, Dark / Normal / Mystery, ability ID 82, 13/13 HP initially, Tackle/Sand-Attack.
- Bite dealt 6 HP per hit. The rescue battle ended with Turtwig at 20/22 HP before Birch healed the party.

The local Route 103 rival battle was then completed against May's Lv5 Mudkip
(species ID 258, 21/21 HP) using the semantic battle controller and Scratch.

Battle structs retain stale data after battle. The adapter therefore does **not** infer battle activity merely because `gBattleMons` contains plausible values. The current runtime uses framebuffer HUD recognition to gate battle observations until a dedicated battle-active RAM signal is verified.

## Wall clock task

During initial clock setup, the active wall-clock task data was found at `0x03005E18`. Read-only feedback from the task fields was used while sending normal D-pad input, allowing the clock to be set exactly to 17:43 without interpreting hand graphics.

## Interface milestones

### RPC v0.1

- NDJSON RPC with request IDs
- frame-synchronized key input
- memory reads/writes and batches
- screenshots and savestates

### RPC v0.2

- memory range reads
- snapshots and diffs
- named watches
- conditional frame/memory waits

### RPC v0.3

- atomic experiments: restore one checkpoint, run an exact input schedule, capture memory on the exact final frame
- used to remove timer/RNG/free-run noise from reverse-engineering experiments

### Run & Bun adapter

The local adapter currently decodes:

- save pointers
- player name/gender/map/coordinates
- persistent party Pokémon, including Gen III encrypted substructures
- live battle Pokémon
- title, Yes/No, battle command, and move cursors
- battle/overworld visual state from the mGBA framebuffer

It also provides verified movement, dialogue advancement, semantic Yes/No selection, semantic battle-menu selection, and Lua-native screenshots.

## Playthrough checkpoint

Current progression:

- Player: GPT
- Starter: Turtwig
- Clock set to 17:43
- Birch rescue battle completed
- starter accepted without nickname
- Birch asked us to go see May
- currently leaving Birch's lab to head north
