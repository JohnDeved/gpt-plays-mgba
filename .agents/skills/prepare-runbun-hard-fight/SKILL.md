---
name: prepare-runbun-hard-fight
description: Prepare Pokémon Run & Bun for a known or likely hard trainer, rival, gym, boss, or required double battle. Use before entering a trainer sight line or battle script, after a loss or repeated retry, when an inherited checkpoint is already mid-fight or understrength, or when deciding whether to heal, catch counters, use Endless Candy, change moves, or reorder the team. Build an authoritative enemy roster from RAM, ROM, or emulator APIs; preserve progression with a verified checkpoint; create an auditable team and turn plan; and block re-engagement until deterministic readiness validation passes.
---

# Prepare a Run & Bun Hard Fight

Treat preparation as part of the battle. Do not repeatedly branch-search a fight that the available party is structurally unfit to win.

Keep the fight tied to the active milestone in `$track-runbun-goals-progress`. Preparation may become the current bounded goal, but it never replaces the global game-completion objective.

## Workflow

1. Stop outside the trigger.
   - Keep mGBA muted and OS-stopped whenever no request or input is active.
   - Save a recoverable pre-fight checkpoint and calculate its SHA-256.
   - Prefer a checkpoint after all prior wins. Never rewind completed progression merely because an older state is healed.
   - A save-state load is permitted only to correct an accidental or tooling-caused mistake that was not caused by the controller's strategic choice (for example, an unintended input, navigation overshoot, or interface failure). Load only the newest verified forward checkpoint, re-read RAM, and reconcile its hash before continuing. Never use a load to erase a deliberate strategic loss or to return to an older progression state.
   - If no pre-trigger frame exists, perform one reconnaissance encounter or accept one clean loss, then prepare from the resulting healed state.

2. Establish the complete fight from authoritative state.
   - Identify map, local object/script or trainer ID, battle format, roster count, send-out order, species, levels, abilities, held items, moves, PP, and relevant stats.
   - Prefer ROM tables, battle RAM, emulator APIs, and deterministic savestate probes. Use screenshots only to diagnose a UI/tooling gap.
   - Mark `roster_complete` false while any reserve or action-changing detail is unknown. A partial roster is not a ready plan.
   - Query `.agents/skills/prepare-runbun-hard-fight/references/trainers.json` with `python3 .agents/skills/prepare-runbun-hard-fight/scripts/trainer_db.py find --map-group G --map-number N --local-id ID` as soon as a trainer NPC is observed.
   - Treat `(map_group, map_number, local_id)` as the stable overworld key. Confirm graphics ID, script pointer, and trainer ID when available; never key by runtime object slot or current coordinates alone.
   - If no record exists, save a pre-contact checkpoint, use one bounded reconnaissance encounter, then add the complete roster with `trainer_db.py upsert RECORD.json`.

3. Establish legal preparation constraints.
   - Verify the current level cap from game state or a trusted ROM-derived source.
   - Read the party, storage access, balls, medicine, berries, TMs, held items, and Endless Candy from game state.
   - Identify reachable encounters and legal moves before recommending catches. Do not assume a counter is obtainable.
   - Query `game_wild_encounter_lookup` by needed species/type and with `visited_only=true`. Compare current-area encounters with prior visited maps and backtrack when the ROM index exposes a materially safer counter. Record map ID, method, level range, and slot percentage as preparation evidence.

4. Diagnose the matchup before choosing a team.
   - Compare speed order, deterministic damage bounds, priority, accuracy, immunities, resistances, status pressure, setup, target selection, reserve order, and shared weaknesses.
   - For doubles, analyze both enemy actions and both allied actions together. Include Fake Out, Helping Hand, spread moves, redirection, Protect cadence, and replacement timing when present.
   - Separate verified facts from model estimates. Label choices forced, minimax, expected-best, or heuristic; never call a line provably best without complete proof.

5. Prepare the team.
   - Heal HP, status, and PP fully.
   - Use Endless Candy only to the verified legal cap.
   - Catch or retrieve counters when the current roster lacks sufficient survivability, damage, speed control, immunity, or party depth.
   - Fill available party slots when a required six-Pokémon opponent materially outnumbers the current party, unless the plan provides verified evidence that fewer slots are safer.
   - Set moves, held items, party order, and doubles leads for explicit roles.

6. Write the plan before contact.
   - Record target priority, opening actions, expected ranges, reserve responses, switch rules, status contingencies, sacrifice rules, and loss conditions.
   - List every action-changing uncertainty. Resolve it by ROM/RAM inspection or a bounded savestate probe before engagement.
   - After two failures with the same preparation and plan, block a third identical retry. Perform a postmortem and revise the team or plan.

7. Validate readiness.
   - Generate a skeleton with `python3 .agents/skills/prepare-runbun-hard-fight/scripts/validate_plan.py --emit-template`.
   - Store the populated JSON with the run's durable strategy artifacts.
   - Run `python3 .agents/skills/prepare-runbun-hard-fight/scripts/validate_plan.py PLAN.json`.
   - Do not trigger the trainer unless validation returns `"valid": true`.

8. Execute and learn.
   - Continue using canonical RAM state, legal-action enumeration, a decision certificate, and post-action verification for every important turn.
   - On any predicted/actual mismatch, unintended extra input, stale state, decoder gap, or unexplained behavior, invoke `$resolve-unexpected-tooling-issues`. Preserve the furthest live savestate, reproduce from a copy, fix the owning layer, replay objectively, and restore live progress before continuing.
   - If the controller itself made the strategic choice that led to a loss, keep the live post-loss state and learn from it. If the loss or regression was caused by an accidental/tooling error outside that choice, the newest verified forward checkpoint may be loaded after the incident is preserved and the restored RAM/hash is verified.
   - Convert every unexpected outcome into roster knowledge, battle experience, a regression case, or a verified tooling defect. Never retry an unexplained action-sensitive failure ad hoc.
   - Beat every required fight after correcting reusable causes; preparation is not permission to avoid it.

## Trainer Database

Use `references/trainers.json` as the durable, versioned source for known trainers. Store:

- Stable overworld identity: map group/number, local ID, graphics ID, event-template slot and coordinates, script address, flag ID, and decoded trainer ID.
- Battle identity: format, required status, complete roster count, and source evidence.
- Every Pokémon: send-out position, species, level, ability, held item, moves, and relevant observed stats.
- Experience: outcomes, reusable failure causes, and preparation requirements.

Run `python3 scripts/trainer_db.py validate` after every update. Record `null` plus an explicit uncertainty when a name or field is unknown; do not convert a decoder omission into “none.” Query the database before moving into any known trainer's sight line and include the matched database key in the readiness plan.

Use the project ROM encounter index instead of screenshots or external encounter guides. A hard-fight plan is incomplete when it recommends catching a Pokémon without a verified encounter location, or when it ignores a stronger reachable counter on a previously visited map.

## Readiness Standard

The validator requires a verified checkpoint, a complete sourced enemy roster, a sourced level cap, a full-health/PP legal party with roles and valid leads, an opening action for each lead, target priorities, contingencies, no action-changing uncertainty, and fewer than two failed attempts with the unchanged plan.

Warnings are not automatic blockers, but explain them in the decision certificate. In particular, justify entering a hard six-Pokémon fight with fewer than six healthy party members.
