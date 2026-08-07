# Run & Bun adapter

This directory contains the ROM-specific layer for **Pokémon Run & Bun v1.07**. The generic mGBA socket/RPC code intentionally does not know anything about Pokémon.

## Files

- `state.py` — verified RAM structures, party decryption, player/map state, menu cursors, one-tile movement, dialogue/free-overworld helpers.
- `visual.py` — framebuffer classifier for dialogue, battles, Bag, Start, Party, Shop and transition states.
- `battle_driver.py` — battle state machine built from live fights.
- `symbols.json` — addresses and struct notes verified against this exact ROM version.

## Observation model

The adapter combines three channels:

1. **RAM** for exact facts: coordinates, HP, PP, species, stats, stages, party data.
2. **Framebuffer state** for UI mode: battle text, command menu, Bag, Start, Party, Shop, dialogue.
3. **Action acknowledgement** for causality: PP drops, HP changes, EXP changes, species changes and coordinate changes.

No single channel is trusted for every decision. For example, player coordinates remain valid while a menu is open, and some battle animations temporarily hide HUD elements.

## Persistent party

`gPlayerParty` uses normal Gen III encrypted substructures. The adapter:

- decrypts the four secure substructures using `personality ^ otId`;
- reorders them from `personality % 24`;
- decodes species, held item, EXP, moves, PP and friendship;
- reads the unencrypted battle stats/status tail;
- validates the stored checksum.

This is the authoritative party state outside battle.

## Battle state

The live battle structure exposes:

- species and level
- HP/max HP
- offensive/defensive stats
- three type bytes
- ability ID
- moves and PP
- status
- eight stat stages, including accuracy and evasion

The battle driver uses opponent PP deltas to infer which enemy move executed. It treats player move selection and player move execution as separate events: a faster opponent can KO before the selected move spends PP.

### Turn-result semantics

A `TurnResult` records:

- selected move
- whether selection was accepted
- whether the move actually executed
- player HP before/after
- opponent HP/species before/after
- inferred opponent move
- PP before/after
- EXP before/after
- whether an opponent change represented a KO replacement

This data came directly from the May, Calvin and Rick fights.

## Menu state

The Start menu uses the generic menu cursor at `0x0203C3C2`. Selection is semantic rather than based on a remembered number of Down presses.

The visual classifier also recognizes the dimmed Start-menu transition that appears while entering/leaving submenus. This matters because moving while that frame is incorrectly classified as overworld can corrupt an automation sequence.

The Bag has multiple pockets in Run & Bun. A visible empty pocket never proves that inventory is empty.

## Movement

`step_tile(direction)` is the default primitive. It sends short direction pulses and checks decoded coordinates after each pulse. It stops as soon as exactly one tile/map transition occurs.

This handles the Gen III behavior where the first input may only turn the avatar.

For route planning, static `pokeemerald` map data can propose collision paths, but every edge is verified by the running ROM. Directed edges are required for ledges.

## Current party/playthrough

At the latest sync:

- Turtwig Lv11 — Bite / Growl / Absorb / Confide
- Starly Lv11 — Tackle / Growl / Quick Attack / Aerial Ace
- May first rival battle won
- Calvin defeated
- Rick defeated
- current route: Route 102, heading toward Petalburg

See `../../data/session_progress.json` for the machine-readable snapshot.

## Next interface targets

- semantic Bag-pocket/item selection instead of pocket-relative button scripts
- explicit wild-battle catch state machine, including Pokédex/nickname transitions
- reusable Repellent control for solved-route traversal
- map planner generalized beyond Route 101/102
- richer battle policy using type effectiveness, observed damage ranges, speed and lethal-risk estimation
- structured shop/inventory model

## Version rule

All raw addresses and inferred layouts are **v1.07-specific evidence**. Do not silently reuse them on another Run & Bun build; revalidate against ROM identity and live behavior first.
