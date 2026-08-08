# Roadmap

The project has two parallel goals:

1. Complete Pokémon Run & Bun through normal game inputs.
2. Continuously improve the emulator interface so each future decision requires less fragile low-level work.

## Completed milestone: RPC v0.1

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

## Current milestone: RPC v0.2 observation and event engine

Implemented:

- Compact `memory.read_range` and `memory.read_range_batch` operations
- Named memory snapshots and grouped byte-level diffs
- Named scalar and byte-range watches
- Frame-polled watch change events with cursors
- Asynchronous frame-based conditional waits with deterministic emulator-frame deadlines
- Python helpers for all of the above

The first reverse-engineering loop is now expressible as: snapshot a region, perform one controlled action, diff it, then install watches for addresses that repeatedly correlate with the transition.

## Next: observation and event engine hardening

- Push events from Lua rather than polling `events.poll` (requires an async-capable Python reader)
- Snapshot retention limits and explicit snapshot removal
- More compact diff encoding for very large changed regions
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
- Offline Route 101 collision/elevation pathfinding with batched live execution
- Runtime collision/elevation grid pathfinding for loaded hack-specific maps, with one range read and one compressed input macro
- Live object-event decoding and identity-based NPC seeking with re-planning around moving actors

The first Route 103 rival battle is complete in the local Chimchar run. The
battle controller reads battler HP, move cursors, field-message modes,
transient battle-printer text, and uses the framebuffer only to disambiguate
the command menu when the ROM leaves the field mode ambiguous.

The default controller is now RAM-only. Framebuffer classification is opt-in
and retained solely for discovering a missing RAM signal.

The object layer exposes active object-event slots (local ID, graphics ID,
map-local coordinates, movement direction, and occupancy). NPC navigation can
target an object identity and approach it from a walkable tile; the target and
occupied tiles are re-read between short bridge macros.

## Session layer

- JSONL action/observation log
- Frame number on every record
- Screenshot references for important states
- Named recovery checkpoints
- Record interface discoveries and verified addresses as they are found

Runtime artifacts (ROMs, saves, screenshots, savestates, extracted AppImages) remain outside Git.

## Design rule

Prefer one structured observation followed by one reasoned action over many screenshot/button round trips. Screenshots remain an important perception channel, but exact state should move to decoded memory/event signals whenever we can verify them.
