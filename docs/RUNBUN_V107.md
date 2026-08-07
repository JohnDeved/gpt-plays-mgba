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

SaveBlock2 begins with the player name and gender. The current player is `GPT`, male.

## UI cursors

| UI | Address | Values |
| --- | ---: | --- |
| New Game / Option title menu | `0x02023006` | `0` New Game, `1` Option |
| Generic Yes/No menu | `0x0203C3C2` | `0` Yes, `1` No |
| Battle command menu | `0x02023A1C` | `0` Fight, `1` Bag, `2` Pokémon, `3` Run |
| Battle move grid | `0x02023A20` | `0..3` move slot |

Each cursor address was isolated with same-savestate, same-final-frame atomic experiments where the only difference between branches was a D-pad input.

## Player party

- `gPlayerPartyCount`: `0x02023A95`
- `gPlayerParty`: `0x02023A98`
- Pokémon stride: `0x64` bytes

The party structure uses Gen III's encrypted 48-byte secure region. Decryption with `personality ^ otId`, the standard `personality % 24` substructure permutation, and the stored checksum has been verified against the live starter.

Current persistent party state after the Birch rescue:

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

The first rescue battle validated this parser against the framebuffer:

- Player: Turtwig Lv5, Shell Armor (ability ID 75), Grass / Grass / Mystery, 22/22 HP initially, Bite/Growl/Absorb.
- Opponent: Zigzagoon Lv2, internal species ID 987, Dark / Normal / Mystery, ability ID 82, 13/13 HP initially, Tackle/Sand-Attack.
- Bite dealt 6 HP per hit. The rescue battle ended with Turtwig at 20/22 HP before Birch healed the party.

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
