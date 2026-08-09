---
name: develop-runbun-strategies
description: Develop, test, retrieve, and promote reusable tactical strategies for Pokémon Run & Bun. Use before a known hard fight, after a strategic loss, when a matchup offers a setup or sweep window, when selecting party/PC Pokémon and Pokécenter services for explicit roles, or when a proven line from another fight may transfer. Keep candidate ideas separate from tested or exhaustively proven strategies and attach every promotion to authoritative RAM/ROM or deterministic clone evidence.
---

# Develop Run & Bun Strategies

Build a small evidence-backed toolbelt, not a list of generic Pokémon tips. Query it before designing a hard-fight plan and update it after verified battles.

## Workflow

1. Read the current goal and trainer record with `$track-runbun-goals-progress` and `$prepare-runbun-hard-fight`.
2. Obtain `game_pokemon_build_options`, `game_storage_snapshot`, `game_pokecenter_service_catalog`, the complete enemy roster, level cap, and exact ROM move/type data.
3. Query matching strategies:
   `python3 .agents/skills/develop-runbun-strategies/scripts/strategy_db.py query --tag TAG`
4. Reject candidates whose requirements, safe setup window, resources, speed order, or abort rules do not hold. Treat hidden AI, crits, accuracy, items, abilities, secondary effects, and reserve order as material uncertainty until bounded.
5. Write the concrete line: roles, party order, lead, setup turns, target sequence, switches, sacrifices, stop/abort conditions, expected damage ranges, and fallback.
6. Test in a disposable muted clone on a unique RPC port. Historical savestates are test fixtures only; hash protected live checkpoints before and after and never load them into the authoritative emulator.
7. Execute live only after hard-fight readiness validates. Use a fresh battle certificate and `game_battle_step` for every important single-battle action.
8. Append evidence after each verified result. Run `strategy_db.py promote ID`; let the script enforce proof thresholds. Demote or retire a strategy after a counterexample.
9. For executable tactics, add the fixed `executable.when` predicates and `directives` block to the strategy record. Use `tools/run_battle_policy.py` for the clone attempt so the postmortem and bounded counterfactual queue are written before the next attempt. Evidence and prose may change without changing the executable behavior hash.
10. After every fight—win or loss—stop and complete the persisted postmortem before another clone or live attempt. Review every verified transaction against its prediction and list:
    - what went wrong: tooling mismatches, terminal/action errors, avoidable faints, reserve misuse, resource loss, and slower-than-needed actions;
    - what was suboptimal: only choices lexicographically dominated by a verified legal alternative or confirmed by a bounded replay;
    - what remains uncertain: only action-changing uncertainty gets a bounded one-replay counterfactual;
    - what could improve: assign each item to shared tooling, generic scorer, reusable executable strategy, or genuine trainer exception.
    Persist the review, regression state, counterexample, and reusable lesson. Apply every action-changing improvement before retrying; a clean win records that no action-changing defect was found and may count toward qualification, but a win with an unresolved improvement cannot.

## Evidence Rules

- `candidate`: plausible line, no success claim.
- `tested`: at least one clean verified reproduction.
- `exact_proven`: an exhaustive/minimax terminal win plus at least two clean reproductions of the scoped state.
- `reusable_proven`: exact-proven and successful in at least three materially distinct states across at least two trainers.
- `retired`: unsafe, invalidated, or superseded; retain the counterexample.

Never call online inspiration, a screenshot, a single favorable RNG branch, or an unverified savestate replay proof. Label a live recommendation minimax, expected-best, or heuristic unless the scoped state meets `exact_proven`.

## Database

Use `references/strategies.json` and the schema in `references/strategy-schema.md`. Validate after edits:

`python3 .agents/skills/develop-runbun-strategies/scripts/strategy_db.py validate`

Upsert a JSON strategy record rather than editing history ad hoc:

`python3 .agents/skills/develop-runbun-strategies/scripts/strategy_db.py upsert /tmp/strategy.json`

Store exact ROM SHA, scope, requirements, blockers, line, abort rules, evidence hashes, trainer keys, and counterexamples. If an unexpected result can change the line, invoke `$resolve-unexpected-tooling-issues` before promotion.
