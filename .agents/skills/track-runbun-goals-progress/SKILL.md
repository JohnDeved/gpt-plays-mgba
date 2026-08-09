---
name: track-runbun-goals-progress
description: Define, reconcile, and track the active Pokémon Run & Bun gameplay goal and authoritative global game progression. Use at run start or resume, after context compaction or checkpoint loading, before choosing the next route or preparation task, after every fight or major milestone, when side work risks derailing gameplay, when comparing savestates to load the furthest state, and before claiming completion. Maintain one active milestone goal, rank checkpoints by verified story state rather than recency or party strength, expose the next action, and keep the player moving toward confirmed game completion.
---

# Track Run & Bun Goals and Progress

Keep the global objective stable, one current milestone explicit, and every action tied to a verified next state. Store the ledger at `runtime/session/run_progress.json`; keep raw events elsewhere.

## Reconcile Before Acting

1. Read the ledger with `python3 .agents/skills/track-runbun-goals-progress/scripts/progress.py summary runtime/session/run_progress.json`.
2. Obtain compact canonical game state from RAM/emulator APIs: ROM hash, save-block identity, map, party, badges, key items, story/event flags, defeated required trainers, and current battle/UI state.
3. Compare live evidence with the ledger. Never trust narrative memory over authoritative state.
4. If state regresses, contradicts the ledger, or an action changed more than predicted, invoke `$resolve-unexpected-tooling-issues` before continuing.
5. Validate after every update with `python3 .agents/skills/track-runbun-goals-progress/scripts/progress.py validate runtime/session/run_progress.json`.

## Goal Hierarchy

Maintain exactly these levels:

- `global_goal`: complete Run & Bun; change only if the user changes the run objective. Mark complete only from an authoritative completion flag, postgame state, or credits state.
- `current_goal`: exactly one active, bounded milestone with explicit completion conditions and sources. Examples: prepare a legal Route 109 team, defeat a known trainer, reach and verify the next town.
- `next_actions`: ordered actions that advance the current goal, preserve the run, or resolve a verified blocker. The first legal safe action is the working focus.

Tool construction, catching, healing, grinding, map research, and incident repair are support work, not new global goals. Record why each support action is necessary and return to the active milestone immediately after its postcondition is verified.

## Update Progress

- Mark a milestone complete only with RAM, ROM, emulator API, savestate hash plus decoded state, trainer/event flag, badge bit, key-item state, or another explicit authoritative source. Screenshots may illustrate but cannot be sole evidence.
- Record completed fights immediately, including stable trainer/NPC identity when known.
- Keep completed milestone IDs monotonic. Never erase progression because an older checkpoint has better health, resources, or party composition.
- After a milestone, set the next bounded goal and at least one next action in the same update so the run never becomes directionless.
- Keep action-changing blockers and uncertainty explicit. A blocker may pause one operation while safe supporting work continues.

## Rank Checkpoints

Run `python3 .agents/skills/track-runbun-goals-progress/scripts/progress.py record-checkpoint LEDGER CHECKPOINT --state-hash HASH --map G,N,X,Y` after meaningful progress or safe preparation.

Rank by the verified completed-milestone set and its highest required order. Use badges, story flags, required trainer flags, key unlocks, and map access only through recorded milestone evidence. Do not rank by file timestamp, current location, party level, HP, inventory value, or convenience. On equal progression, preserve the existing furthest checkpoint unless the new state has a strict evidence-backed progression superset.

Before loading a candidate, validate its file hash and decoded progression vector. After loading, re-read RAM and reconcile; never assume load success from UI appearance.
Loading is an exception-recovery action, not a retry policy: it is allowed for an accidental or tooling-caused mistake outside the controller's strategic choice, using only the newest verified forward checkpoint. Never load an older state to undo a deliberate strategic loss or to trade away verified progression for better health/resources.

## Stay on Track

Before any nontrivial action, answer compactly:

1. Which current-goal condition does this advance?
2. What authoritative postcondition will prove success?
3. Is a safer or shorter action available without reducing run success?

If the action advances no goal, safety invariant, or verified blocker, do not take it. For a hard fight, invoke `$prepare-runbun-hard-fight`; its validated plan remains a child artifact of the current milestone.

Read [references/progress-schema.md](references/progress-schema.md) when creating or repairing a ledger. Use `.agents/skills/track-runbun-goals-progress/scripts/progress.py init` only when no ledger exists; it refuses to overwrite one.

## Completion Gate

Do not mark `global_goal.status` complete from a final boss KO, dialogue, screenshot, or expectation alone. Verify the ROM-specific completion/credits state from RAM or emulator API, store the evidence and final checkpoint hash, validate the ledger, then report completion.
