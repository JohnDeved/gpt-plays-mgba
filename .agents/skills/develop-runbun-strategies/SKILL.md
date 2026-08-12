---
name: develop-runbun-strategies
description: Develop, test, retrieve, and promote reusable tactical strategies while continuously improving the whole Pokémon Run & Bun battle system. Use before a known hard fight, after every battle review, after a strategic loss or tooling mismatch, at major battle milestones, when a matchup offers a setup or sweep window, or when a proven line may transfer. Keep candidates separate from proven strategies and attach every promotion to authoritative RAM/ROM or deterministic clone evidence.
---

# Develop Run & Bun Strategies

Build a small evidence-backed toolbelt, not a list of generic Pokémon tips. Query it before designing a hard-fight plan and update it after verified battles.

## Continuous Improvement Contract

- Treat this skill as the single owner of battle-system learning; do not create a parallel improvement skill or ledger.
- After completing each agent-authored battle review, run `python3 .agents/skills/develop-runbun-strategies/scripts/strategy_db.py health`. Run it again after hard-fight qualification and major progression milestones.
- Work the highest-severity health priority first. Fix at the broadest valid layer: shared observation/execution, mechanics, generic scorer, reusable strategy, preparation, then trainer exception.
- Leave one regression test for every tooling, mechanics, scorer, or executable-strategy behavior change. Resolve attached critical/high states with an exact replay before continuation.
- Do not mutate behavior merely because a fight completed. A clean review adds confidence; only verified findings justify code or strategy changes.
- Keep trainer profiles disposable. Durable learning belongs in shared code, strategy records, reviews, and regression fixtures.
- Improve breadth as well as win rate: prioritize compatible second-trainer reproductions while `health` reports a cross-trainer evidence gap.

## Workflow

### Classifier-owned live loop

For each important battle decision, keep this exact flow:

1. Pause the emulator and call `game_battle_evaluate --brief` with the stable `battle_id` and current RAM `state_hash`.
2. Read the complete compact candidate set. If the recommendation is unsafe or strategically poor, do not overwrite it by hand and do not input anything.
3. Identify the broadest reusable cause, fix the shared mechanics/scorer/strategy layer, add the smallest regression reproducing that decision, and rerun it.
4. Re-evaluate the unchanged RAM state. Execute only when the corrected classifier itself selects the justified action; pass the same `battle_id`, fresh `state_hash`, and `certificate_id` to `game_battle_step --brief`.
5. Verify actual HP, PP, status, enemy action, switch, and terminal state against the prediction. Add every mismatch or strategic weakness to the post-fight review and feed verified lessons back into the classifier.

This is classifier training, not manual policy replacement. Never hide a bad recommendation with an operator-selected action. A stale-certificate refusal is evidence that evaluation and execution context differ; first verify both calls used the same `battle_id` and history.

Keep this cadence moving. Perform one bounded observe/evaluate/act/verify cycle at a time, then immediately continue when verification matches and no action-changing uncertainty remains. Batch safe navigation or menu inputs only through an interface that verifies every boundary; never batch across a battle decision, encounter, warp, or unexpected state. Persist a lesson only when it changes reusable knowledge, a regression, or the review; do not repeatedly restate the run.

### Output budget

- Use `tools/runbun_call.py --brief` for routine observations, evaluations, steps, navigation, and NPC operations. Brief output must retain the state/certificate hashes, legal candidates, ranking evidence, material uncertainty, selected action, verified deltas, discrepancies, and terminal state.
- Treat full payloads as diagnostic artifacts. Write them with `--output PATH`, inspect their shape with `jq -c 'keys'`, and print only the exact field that blocks the next decision. Expand one field at a time; do not dump raw logs, move tables, full transaction history, or unchanged party/battle state.
- Prefer one capability response that already includes the authoritative post-state over a second observation call. Prefer one compact `jq -c` projection over several discovery calls.
- Remove null/empty fields and deduplicate repeated evidence in shared `--brief` formatting. A routine output above roughly 4 KB, or the same noisy projection used twice, is a tooling defect: measure it with `wc -c`, tighten the shared formatter, leave one regression, then reuse it.
- Keep bulky evidence in runtime JSON/JSONL and retrieve only the relevant turn, trainer, strategy, or finding. Never paste an entire artifact into live context when hashes, counts, selected fields, and a path suffice.

1. Read the current goal and trainer record with `$track-runbun-goals-progress` and `$prepare-runbun-hard-fight`.
   Unclassified or recorded-easy trainers may use the normal health-safe engagement path. The first verified loss classifies that stable trainer key as hard, after which the full preparation and three-clean-clone gate is mandatory.
2. Obtain `game_pokemon_build_options`, `game_storage_snapshot`, `game_pokecenter_service_catalog`, the complete enemy roster, level cap, and exact ROM move/type data.
3. Query matching strategies:
   `python3 .agents/skills/develop-runbun-strategies/scripts/strategy_db.py query --tag TAG`
   Read promotion and cross-trainer reuse health without scanning raw evidence:
   `python3 .agents/skills/develop-runbun-strategies/scripts/strategy_db.py summary`
   For executable matching, write the fixed battle context as JSON and run `strategy_db.py match CONTEXT.json`; never copy a reusable rule into a trainer profile merely to activate it.
   Generate the fight artifact in one read-only call after the party is prepared:
   `python3 tools/runbun_call.py game_battle_profile --args '{"trainer_key":"map:G:N/local:L","state":"CHECKPOINT"}' --output runtime/session/policy_profiles/FIGHT.json`.
   Generated schema-v2 profiles contain current identities, matchup assignments, a strategy manifest, and only genuine matchup exceptions. They are not reusable knowledge; strategies and the generic scorer are.
   Locked multi-turn moves remain mechanically vetoed unless an executable strategy explicitly models and authorizes the full lock, including later opposing send-outs and residual/status risk.
4. Reject candidates whose requirements, safe setup window, resources, speed order, or abort rules do not hold. Treat hidden AI, crits, accuracy, items, abilities, secondary effects, and reserve order as material uncertainty until bounded.
5. Write the concrete line: roles, party order, lead, setup turns, target sequence, switches, sacrifices, stop/abort conditions, expected damage ranges, and fallback.
6. Test in a disposable muted clone on a unique RPC port. Historical savestates are test fixtures only; hash protected live checkpoints before and after and never load them into the authoritative emulator.
7. Execute live only after hard-fight readiness validates. Use a fresh battle certificate and `game_battle_step` for every important action. In doubles, reevaluate after the first allied command is queued before selecting the second.
8. Append evidence only from a completed agent review. A clean reviewed win is a reproduction; a counterexample is attributed only when that strategy's declared requirements matched and the finding names it as action-changing. Run `strategy_db.py promote ID`; let the script enforce proof thresholds. Demote or retire a strategy after a real counterexample.
   Count a reproduction only when the decision certificate lists the strategy in `influential_strategy_ids`; merely matching without changing a selection, veto, or reservation is not evidence.
9. For executable tactics, add the fixed `executable.when` predicates and `directives` block to the strategy record. Use `tools/run_battle_policy.py` for the clone attempt so the postmortem and bounded counterfactual queue are written before the next attempt. Evidence and prose may change without changing the executable behavior hash.
10. After every fight—win or loss, easy or hard, clone or live—stop and complete the persisted postmortem before another clone, live attempt, or new trainer engagement. Review every verified transaction against its prediction and list:
    - what went wrong: tooling mismatches, terminal/action errors, avoidable faints, reserve misuse, resource loss, and slower-than-needed actions;
    - what was suboptimal: only choices lexicographically dominated by a verified legal alternative or confirmed by a bounded replay;
    - what remains uncertain: only action-changing uncertainty gets a bounded one-replay counterfactual;
    - what could improve: assign each item to shared tooling, generic scorer, reusable executable strategy, or genuine trainer exception.
    List every observed item, not only the first causal failure. Assign `critical`, `high`, `medium`, `low`, or `info` severity, sort the ledger highest first, and work the highest-severity reusable cause before lower-severity polish. A terminal loss/whiteout is critical; tooling or action-changing defects are high; verified dominated choices are medium; unverified tempo/resource observations are low/info until replay proves they change the action.
    Persist one compact review at `runtime/session/battle_reviews/<review_id>.json`; replay states belong under `runtime/session/battle_reviews/states/`. Raw action evidence stays in `runtime/session/battle_transactions.jsonl` and is not duplicated into the review. The automatic report is incomplete until the agent adds `agent_review.status=complete`, `author=agent`, the exact `reviewed_actions` count, severity-ranked `findings`, `correct_choices`, and a concrete `next_test`. Attach a saved state to every new critical/high issue that can be replayed; the system automatically adds `resolved=false` when that state is attached. After fixing the strategy, scorer, or tooling, load that state, verify the corrected result, and set `resolved=true` with `resolution.verified=true` and a concrete `resolution.observed` record. Any attached critical/high issue without verified resolution evidence blocks clone continuation, new trainer engagement, and qualification. Older findings without an attached state are grandfathered. Confirm that the single JSON artifact exists and parses before another attempt. Apply every action-changing improvement before retrying; a clean win records that no action-changing defect was found and may count toward qualification, but a win with an unresolved improvement cannot.
    The runtime script is evidence collection and consistency checking only; its automatic findings are not the strategic postmortem. The agent must personally read the full action sequence and author the strategic list. Each item must include severity, action/turn evidence, why it mattered, the broadest justified fix layer, and the concrete change or test before retry. The list must cover the whole fight, including good-but-risky choices and lower-severity tempo/resource improvements, not just the first causal failure.
    Easy/live wins are learning events, not exemptions: record safe choices, missed opportunities, resource/tempo costs, and reusable strategy lessons even when no gate resets. A live win never bypasses review; it only avoids the hard-fight clone qualification reset when the trainer is not classified hard.
    For a manual review, use the narrow CLI instead of hand-building JSON:
    `python3 tools/create_battle_review.py --output runtime/session/battle_reviews/NAME.json --terminal win|loss --opening-state-hash HASH --policy-id ID --certified-actions N --finding SEVERITY KIND true|false "SUMMARY" --correct-choice "CHOICE" --next-test "TEST"`. Repeat `--finding` and `--correct-choice`; use `--finding-json` when a finding needs an attached replay state or resolution fields. The command refuses overwrites, emits one compact artifact, and reuses the state-resolution gate.
    Resolve one uniquely named state-backed finding only after its replay succeeds: `python3 tools/create_battle_review.py --update REVIEW.json --resolve KIND POST_STATE_HASH "OBSERVED RESULT"`.
    New reviews use only the canonical finding kinds accepted by that CLI: terminal loss, preparation failure, tooling mismatch, prediction gap, policy gap, tactical error, suboptimal action, reserve misuse, resource loss, tempo cost, verified variance, strategy conflict, and safety risk. Every finding records a `knowledge_target` of tooling, mechanics, scorer, strategy, profile, preparation, or review. Strategy findings name an existing or candidate strategy ID; trainer-profile findings explain why the lesson is matchup-specific. Historical review kinds remain grandfathered.

### Agent-authored postmortem format

Write the review in this order before selecting another action bundle:

1. `critical`: terminal loss, whiteout, impossible preparation, or a decision that can immediately end the fight.
2. `high`: avoidable faint, reserve misuse, policy/scorer error, tooling mismatch, or action-changing uncertainty.
3. `medium`: verified dominated action, unnecessary exposure, or a reusable line that works but wastes a tactical resource.
4. `low`/`info`: tempo, PP, switch count, or presentation improvements that do not yet change the safest line.

For every entry write: `severity`, `turns/actions`, `observed fact`, `strategic judgment`, `confidence` (`verified`, `bounded`, or `heuristic`), `fix layer`, and `next test`. Explicitly record what was correct so a good opening or properly used Fake Out is not “fixed” accidentally. Do not call the script’s terminal classification a root cause; the agent must explain the earliest causal divergence and decide whether to change preparation, scorer, executable strategy, or trainer exception.

## Evidence Rules

- `candidate`: plausible line, no success claim.
- `tested`: at least one clean verified reproduction.
- `reusable_tested`: three influential clean wins on each of two trainers; it may contribute only safety-subordinate `prefer`/`reserve` signals to an unqualified live fight.
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
