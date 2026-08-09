# Roadmap

The project has two parallel goals:

1. Complete Pokémon Run & Bun through normal game inputs.
2. Continuously improve the emulator interface so each future decision requires less fragile low-level work.

## Current gameplay interface plan

Verified and available:

- Muted, foreground gameplay launch with the Scripting window closed before UI readiness
- OS-stop while idle and bounded resume for RPC work
- RAM map identity, transition graph, direct-edge/event-warp/scripted-ferry travel, and identity-based NPC seeking
- Trainer database keyed to overworld map/local NPC identity
- ROM wild-encounter lookup with visited-map filtering
- PC storage snapshot plus verified deposit, withdrawal, and swap
- Six-entry Pokécenter utility-NPC catalog; `apply_status` is the first promoted mutation
- Hard-fight preparation, tooling-incident, progress-tracking, automation, and reusable-strategy skills

The active backlog merges the three exact-oracle plans retained in the Codex
session log at lines `26317`, `26483`, and `26523` of
`/Users/johann/.codex/sessions/2026/08/08/rollout-2026-08-08T18-09-34-019fe223-0720-7821-9dee-5020985af310.jsonl`.

### Exact battle oracle

- [x] Decode the verified 20-byte ROM move table and corrected 19×19 type chart.
- [ ] Finish cartridge-accurate mechanics for criticals, accuracy, abilities, held items, berries, fixed/multi-hit/drain/residual effects; retain known-matchup regression ranges. Verified expanded species records and level-up learnsets are decoded.
- [x] Route important single-battle mutations through certified `game_battle_step` with fresh hashes, legal actions, PP attribution, and predicted/actual evidence.
- [x] Tighten `game_battle_step` to require two stable canonical observations and automatically open an incident on any mismatch.
- [x] Replace context-free tactical samples with experience schema v2; preserve raw history but quarantine contradictory or incomplete v1 bounds.
- [x] Add isolated `game_battle_branch_search` with real-cartridge successor savestates, unique-port disposable clones, exact-state transposition cache, lexicographic scoring, bounded proof labels, replay support, and live-hash preservation.
- [ ] Re-enable `game_battle_branch_search` only after semantic-cycle pruning, losing-terminal classification, and a strict wall-clock budget have objective regressions; use bounded persisted-policy replays until then.
- [ ] Obtain two terminal winning reproductions for the prepared Gavi build; fixed-checkpoint search still explicitly excludes input-delay RNG variants.
- [ ] Add canonical legal-action evaluation and certified execution for true double battles.
- [x] Add a reusable hybrid battle-policy engine with declarative profiles, fixed predicates/directives, transaction-derived history, and behavior-bundle hashing.
- [x] Add mandatory postmortems and bounded one-replay counterfactual queues after every policy clone attempt; reset qualification on losses or action-affecting changes.

### Complete team preparation

- [x] Add direct capability execution and a compact authoritative progression snapshot.
- [x] Decode all PC boxes and provide verified deposit, withdrawal, and swap.
- [x] Upgrade PC operations to require a fresh storage hash and stable personality/OT references; expose nature, ability slot, friendship, exact box location, and explicit boxed-status unavailability.
- [x] Decode verified species growth IDs so boxed experience converts to an authoritative level.
- [x] Expand `game_pokemon_build_options` with stable party references, legal ROM relearn moves, nature/IV/status/build facts, costs, and current Heart Scale availability.
- [x] Extend identity-based NPC seeking with loaded-map ROM `script_address`, including the utility NPC at `0x082A741E`.
- [x] Add verified personality-based party reordering and Pokécenter healing (full HP, cleared status, exact ROM-derived PP).
- [ ] Expose globally available field moves as navigation capabilities—especially Fly—even when no party Pokémon has learned the corresponding battle move; decode destination legality and verify every arrival map from RAM.
- [ ] Add held-item give, take, and swap interfaces with stable Pokémon/item references.
- [ ] Promote the remaining utility-NPC mutations: remember move, forget move, maximize one IV, and change nature; status and nickname application are verified.
- [ ] Add atomic `game_team_prepare(plan)` to coordinate healing, PC transfers, candy/move resolution, utility services, held items, and exact party order with a final build hash.
- [ ] Add `game_counter_plan(trainer_key)` using party, PC, reachable ROM encounters, legal builds, and the exact branch oracle before recommending a capture.
- [x] Expose certified `game_capture_target`: require a fitting nickname, reach the lowest crit-safe HP, add verified sleep/paralysis when available, throw with L only after catch-factor preparation, and audit the inserted RAM record.
- [x] Add a bounded RAM/ROM-driven wild target hunter that flees non-targets and stops at a fresh target command state.
- [ ] Add verified hold-L plus D-pad ball-type selection when multiple ball types are available.
- [ ] Resolve unknown overworld trainers directly from event script and ROM trainer tables, then upsert the trainer database before contact.

### Proven strategy toolbelt and enforcement

- [x] Add the repository strategy skill, schema/database tooling, proof states, counterexample retention, and the `setup-window-sweep` candidate.
- [ ] Add `game_strategy_query` to match scoped strategies against trainer, party, PC, obtainable counters, and legal build requirements.
- [ ] Add `game_strategy_validate` to instantiate a strategy through branch search, retain replay hashes, and promote/demote only at the recorded proof threshold.
- [ ] Add hard preflight enforcement for complete roster/mechanics, exact party build/order hash, zero action-changing uncertainty, two terminal clone wins, and fewer than two failures for the same proof tuple.
- [x] Enforce three clean clone wins for the same opening and action-affecting behavior bundle before live policy execution; preserve legacy schema-v1 plans while schema-v2 profiles migrate.
- [ ] Prove every mutating interface in disposable muted clones, with protected checkpoint hashes and full regressions, before live use.

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
