---
name: automate-time-consuming-work
description: Detect when manual execution is becoming slow, repetitive, fragile, or token-expensive, then find or build the smallest tested function, command, or interface that makes the work faster and more reliable. Use during gameplay tooling, repository tasks, debugging, data inspection, navigation, repeated emulator actions, or any workflow where the same hand-operated sequence is being performed more than once or is taking materially too long.
---

# Automate Time-Consuming Work

Convert repeated manual work into a narrow, reusable interface while preserving correctness and user control. Prefer an existing helper when it already satisfies the contract; add new automation only when the measured time, error, latency, or token cost justifies it.

## Trigger and decision rule

Trigger this skill when any of these occur:

- the same manual action or observation is needed twice;
- a hand-driven sequence is consuming disproportionate time, frames, tool calls, or context;
- manual repetition risks an accidental input, stale observation, lost state, or inconsistent result;
- the task needs a capability that should be reusable in future runs; or
- a one-off workaround is becoming the normal path.

Do not automate merely to avoid a single short action. Do not add speculative abstractions, broad frameworks, or hidden state mutation. A helper must have a smaller total cost than continuing manually and must keep authoritative postconditions observable.

## Workflow

1. **Measure the pain.** State what is being repeated, its current cost, and the failure modes. Use a small sample rather than relying on a vague impression.
2. **Search before building.** Use `rg`, `rg --files`, module exports, CLI help, and existing tests to find a helper, API, macro, or interface. Read its contract and verify that it is safe for the current state.
3. **Define the contract.** Specify inputs, outputs, legal preconditions, acknowledgement, authoritative postcondition, failure modes, and whether the operation may mutate external state. For gameplay, require a canonical RAM snapshot before and after input.
4. **Choose the narrowest interface.** Prefer a pure local function for computation, a small library method for a repeated in-process action, or a focused CLI for a repeatable operator workflow. Keep parameters explicit and return structured results. Avoid magic coordinates, unbounded loops, blind waits, and screenshot parsing when RAM/API data exists.
5. **Implement the smallest vertical slice.** Add only what removes the measured repetition. Preserve existing user changes and avoid unrelated refactors. Use repository conventions and `apply_patch` for edits.
6. **Add a regression first when practical.** Encode the old failure, no-op, stale-state, or wrong-target case. Test both success and safe refusal when preconditions are false.
7. **Benchmark and promote.** Run focused tests, relevant broader tests, and a before/after measurement of reliability, tool calls, latency, frames, tokens, or human steps. Promote the helper only if correctness is preserved and at least one objective cost or quality metric improves.
8. **Use it immediately and document the lesson.** Replace the manual path in the current task, record the interface and postcondition where future agents will find it, and log unexpected results as a regression, knowledge, or tooling defect.

## Gameplay-specific guardrails

- Keep the emulator muted, render-blocked, and OS-stopped while idle; resume only for the bounded action or observation.
- Batch safe observations and input where the interface can still verify each requested postcondition. Do not batch across a battle decision or map transition if that hides an unexpected state.
- Never let an automation helper choose a battle action without the canonical-state and legal-action checks required by the gameplay controller.
- Preserve the furthest live state before probing. Reproduce experiments in `client.mgba_clone.disposable_clone`; historical states are useful fixtures for reaching a relevant UI quickly, but must run in a separate muted/idle-stopped emulator and must never be loaded into or promoted over the live process. Verify protected-state hashes after every clone.
- A helper that hides mismatches, retries blindly, or relies on screenshots for RAM-available facts is a defect, not an optimization.

## Completion checklist

- Existing capability searched and either reused or explicitly ruled out.
- Contract and authoritative postcondition written down.
- Minimal implementation and focused regression added.
- Success, refusal, and unexpected-outcome paths verified.
- Objective before/after improvement recorded.
- Current work switched to the helper and any durable repo documentation updated.
