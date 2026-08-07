# Roadmap

The project has two parallel goals:

1. Complete Pokémon Run & Bun through normal game inputs.
2. Continuously improve the emulator interface so each future decision requires less fragile low-level work.

## Current milestone: RPC v0.1

Implemented and tested against mGBA development build `0.11-9122-afd6f14ea` and Pokémon Run & Bun v1.07:

- NDJSON request/response protocol with request IDs
- Capability handshake
- Frame-synchronized queued button input
- Multi-step input sequences
- Action completion/status
- 8/16/32-bit memory reads and writes
- Batched memory reads
- Batched observation with optional screenshot
- Lua-native framebuffer screenshots
- Savestate save/load
- Reset and input clearing

The savestate path was validated by saving a checkpoint, changing an EWRAM byte, loading the checkpoint, and confirming the byte returned to its original value.

## Next: observation and event engine

- Named memory watches
- `read_range` and large batched reads
- Memory snapshots and diffs
- Conditional waits (`wait until address changes`, `wait until keys/menu/battle state matches`)
- Push events from Lua rather than polling action state
- Session IDs and protocol error codes
- Action cancellation and queue inspection
- Deterministic timeout semantics based on emulator frames

## Run & Bun state adapter

Build a ROM-checksummed profile that turns RAM into game concepts:

- Player map ID, position, facing direction
- Overworld vs menu vs dialogue vs battle state
- Party species, level, HP/status, moves and PP
- Opponent state during battle
- Bag/items and money
- Progression flags/badges
- Menu cursor/state where practical

Start from Pokémon Emerald memory-layout knowledge only as a hypothesis; verify every address against this exact ROM build.

## Reverse-engineering tools

- Scan EWRAM/IWRAM for values matching observed changes
- Snapshot memory before/after a controlled action and rank changed addresses
- Repeated-value filters (changed / unchanged / increased / decreased)
- Named watch sets stored with the Run & Bun profile
- Optional savestate-assisted experiments for reproducibility

## Higher-level actions

Once state decoding is reliable:

- `advance_dialogue()`
- `choose_menu(index)`
- `walk(direction, tiles)` with confirmation from position state
- `open_party()` / `select_party_member()`
- `choose_move(slot)` / `switch_pokemon(slot)`
- Wait for battle/menu/transition completion instead of fixed sleeps
- Map graph and pathfinding where useful

## Session layer

- JSONL action/observation log
- Frame number on every record
- Screenshot references for important states
- Named recovery checkpoints
- Record interface discoveries and verified addresses as they are found

Runtime artifacts (ROMs, saves, screenshots, savestates, extracted AppImages) remain outside Git.

## Design rule

Prefer one structured observation followed by one reasoned action over many screenshot/button round trips. Screenshots remain an important perception channel, but exact state should move to decoded memory/event signals whenever we can verify them.
