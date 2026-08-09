---
name: resolve-unexpected-tooling-issues
description: Deterministically investigate and resolve unexpected outcomes, script bugs, emulator/API/interface failures, stale or contradictory observations, accidental extra inputs, prediction mismatches, flaky timing, missing capabilities, and problems with no known solution yet. Use immediately when actual state differs from a prediction, a tool mutates more or less state than requested, repeated retries are tempting, screenshots are compensating for RAM/API gaps, or new tooling must be proven safe before promotion. Preserve live state, build a minimal replay and authoritative oracle, test falsifiable hypotheses, implement the smallest root-cause fix, run objective regressions, restore live work, and retain unresolved cases as structured experiment queues.
---

# Resolve Unexpected Tooling Issues

Turn every surprise into verified knowledge, a regression case, or a tooling fix. Never normalize an unexplained mismatch by retrying until it disappears.

## Workflow

1. Contain the event.
   - Stop further input or mutation immediately. OS-stop an idle emulator or process.
   - Preserve the furthest live state before probing: save a checkpoint, hash it, and capture compact canonical state, command/action ID, frame count, logs, versions, and relevant resource counters.
   - Save a separate replay fixture. Never experiment on the only live checkpoint.
   - If an action overran, record every unintended state delta; a favorable outcome is still a defect.
   - A save-state correction is allowed only when the bad state came from an accidental input/navigation mistake or a tooling/interface failure outside the controller's chosen strategy. Load the newest verified forward checkpoint, never an older historical state, and verify ROM/map/party/progression RAM plus the checkpoint hash after loading. Do not rewind a deliberate strategic loss; preserve it as the live learning state.

2. Open a structured incident.
   - Run `python3 .agents/skills/resolve-unexpected-tooling-issues/scripts/incident.py init INCIDENT.json --summary ... --component ... --symptom ... --predicted ... --actual ...`.
   - Add `--live-artifact PATH` when preserving a savestate or other binary artifact; the script records its absolute path and SHA-256.
   - Keep raw traces external. Put only decision-relevant facts and artifact paths in the incident.

3. Define the oracle before changing code.
   - State the exact expected behavior and the authoritative evidence that decides pass/fail.
   - Prefer RAM, ROM, protocol acknowledgements, file hashes, deterministic APIs, and invariant checks. Use screenshots only when no authoritative source exists; treat repeated visual dependence as an observation defect.
   - Write the smallest predicted-versus-actual diff. Separate game/domain behavior, prediction/model error, observation/decoder error, actuator/input error, timing/protocol race, environment failure, and missing capability.

4. Reproduce safely.
   - Restore the replay fixture, execute the minimum action sequence, and measure the same oracle fields.
   - Reproduce twice with identical pre-state and outcome before calling a failure deterministic, unless a static proof identifies the violated path without execution.
   - Wrap replay in cleanup that restores the live checkpoint even if the probe fails. Verify its hash or canonical state after restoration.
   - Reduce nondeterminism: fixed checkpoint, exact ROM/build hashes, bounded frames, explicit inputs, no focus-dependent UI, and no network/external critic dependency.

5. Test falsifiable hypotheses.
   - Rank hypotheses by information gained per risk, time, and mutation. Test one changed variable at a time.
   - For each test, record claim, predicted discriminator, evidence for, evidence against, and result. Do not patch from temporal correlation alone.
   - Instrument the narrowest boundary needed: intent, request, acknowledgement, authoritative state delta, and postcondition.

6. Fix the owning layer.
   - Change the smallest component that violated its contract. Do not hide the symptom with blind waits, repeated inputs, weakened assertions, broad exception handling, or screenshot fallbacks.
   - Add a regression that fails on the replay or a minimal synthetic fixture before accepting the fix.
   - For missing capabilities, first define input/output schema, authority source, legal-state preconditions, acknowledgement, postcondition, and failure codes. Implement the smallest end-to-end vertical slice.

7. Prove promotion.
   - Re-run the exact replay from the same fixture and show the expected delta with no extra actions, frames, resource use, or hidden state changes.
   - Run focused unit/integration tests, then the relevant broader suite. Compare reliability, observation quality, tool-call count, latency, frames, and token cost when performance motivated the change.
   - Restore and verify the furthest live state. Resume gameplay only from that state.
   - Mark the incident `verified` only when root cause, fix, replay, regressions, and restoration all pass. Run `python3 .agents/skills/resolve-unexpected-tooling-issues/scripts/incident.py validate INCIDENT.json`.

8. Persist the lesson.
   - Store at least one of: new domain knowledge, a regression fixture/test, or a documented tooling defect and contract.
   - Link the incident from the relevant strategy, trainer, battle-experience, or tool documentation without copying raw logs.
   - Reconcile `$track-runbun-goals-progress` after restoring live state so incident work returns immediately to the active gameplay milestone.

## Problems Without a Known Solution

Do not invent a root cause. Mark the incident `unresolved`, preserve the safe live state, list action-changing unknowns, and create an ordered `next_experiments` queue. Each experiment must state its hypothesis, action, expected discriminator, risk, cost, and rollback. Continue with a workaround only when authoritative checks prove it cannot corrupt progression or invalidate decisions; otherwise keep the affected operation blocked while unrelated safe work continues.

Read [references/incident-schema.md](references/incident-schema.md) when editing an incident manually or deciding whether evidence is sufficient. The validator is the promotion gate, not a substitute for replay evidence.

## Non-Negotiable Rules

- Preserve before probing; replay from a copy; restore after probing.
- One requested action must produce one acknowledged action and only its expected state delta.
- Treat unexpected success exactly like unexpected failure.
- Never call a fix deterministic from one lucky run.
- Never promote a workaround as a root-cause fix.
- Never continue an action-sensitive workflow with an unexplained state mismatch.
