# Run & Bun adapter

This directory contains the ROM-specific layer for **Pokémon Run & Bun v1.07**. The generic mGBA RPC bridge intentionally knows nothing about Pokémon.

## Files

- `state.py` — verified RAM structures, party decryption, player/map state, menu cursors, movement and dialogue/free-overworld helpers.
- `visual.py` — framebuffer classifier for dialogue, battles, Bag, Start, Party, Shop and transition states.
- `battle_driver.py` — acknowledged battle state machine built from live fights.
- `symbols.json` — addresses and struct notes verified against this exact ROM version.

## Observation model

The adapter combines three channels:

1. **RAM** for exact facts: coordinates, HP, PP, species, stats, stages, party data.
2. **Framebuffer state** for UI mode: battle text, command menu, Bag, Start, Party, Shop, dialogue.
3. **Action acknowledgement** for causality: PP drops, HP changes, EXP changes, species changes and coordinate changes.

No single channel is trusted for every decision. Coordinates remain valid while menus are open; battle transitions can temporarily hide the HUD; field poison briefly dims the framebuffer.

## Persistent party

`gPlayerParty` uses Gen III encrypted substructures. The adapter decrypts/reorders them, reads species/item/EXP/moves/PP/friendship, reads the unencrypted battle-stat/status tail, and validates the stored checksum.

This is authoritative persistent party state outside battle.

## Battle state

The live battle structure exposes species/level, HP, combat stats, three type bytes, ability, moves/PP, status and eight stat stages including accuracy/evasion.

The driver infers enemy move execution from opponent PP deltas and separates selection acceptance from execution. A faster opponent can KO the active mon before the selected move spends PP.

The driver is also **active-battler aware**: after switching, it does not read party slot 0 as though it were still active. Forced faint replacement is a separate Party -> Send Out flow.

## Menu state

The Start menu uses the generic cursor at `0x0203C3C2`, so selection is semantic rather than relative button counting.

The visual classifier recognizes the bright and dimmed Start-menu variants. Party detection deliberately uses the two exact dominant party-panel colors `(206,214,123)` and `(181,181,90)`; a broad olive range false-positived Route 104 grass.

Run & Bun's Bag has multiple pockets. Pocket state must be explicit; an empty visible pocket never proves the inventory is empty.

## Movement / navigation

`step_tile(direction)` pulses one direction and stops as soon as coordinates/map change. This handles Gen III's turn-first behavior without risking a two-tile overshoot.

The higher-level navigation model uses static Emerald data only as a proposal. Every directed edge is verified against the live ROM. The planner can forbid unrelated building warps for a route objective and can use savestate-backed offline probes when ledges, encounters, NPCs or transient UI states make live exploration expensive.

## Verified current playthrough

- GPT / male
- May Route 103: defeated
- Pokédex + Running Shoes: obtained
- Wally tutorial / Petalburg Gym intro: complete
- Route 102: crossed; Calvin, Rick, Tiana and other early trainers defeated
- Petalburg reached
- Route 104 entered
- Starly caught and raised to Lv12
- Venipede caught on Route 104
- current recovery point: back in Petalburg before healing/re-entering Route 104

Party at the latest source sync:

- Turtwig Lv12 — Bite / Growl / Absorb / Confide
- Starly Lv12 — Tackle / Growl / Quick Attack / Aerial Ace
- Venipede Lv5 — Rollout / Poison Sting

See `../../data/session_progress.json` for the machine-readable snapshot.

## Next interface targets

- semantic Bag pocket/item/target selection
- generalized catch/nickname helper instead of one-off keyboard navigation
- robust transient-state classifier (battle-transition and field-poison fades)
- reusable route planner with static-map import + persistent directed-edge cache
- battle policy with explicit type effectiveness, speed and lethal-risk estimates
- structured inventory/shop model

## Version rule

All raw addresses and inferred layouts are **v1.07-specific evidence**. Revalidate them against ROM identity and live behavior before reuse on another Run & Bun build.
