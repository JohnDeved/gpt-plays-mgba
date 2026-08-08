# Incident Evidence Schema

Use the JSON emitted by `scripts/incident.py init`. Keep arrays empty until evidence exists; never fill unknown fields with guesses.

## Classifications

- `domain_behavior`: the game/system behaved correctly and durable knowledge was missing.
- `prediction_model`: the state was observed correctly but the tactical or numeric prediction was wrong.
- `observation_decoder`: RAM/API state was decoded, classified, or canonicalized incorrectly.
- `actuator_input`: an input was omitted, duplicated, reordered, or applied in the wrong state.
- `timing_protocol`: request/acknowledgement boundaries or asynchronous ownership were wrong.
- `environment`: dependency, process, filesystem, build, or configuration failure.
- `missing_capability`: no safe interface exists for the required operation.
- `unknown`: classification remains evidence-limited.

## Status Gates

- `observed`: live state and predicted/actual difference are preserved.
- `reproduced`: the minimal fixture, command, steps, oracle, and at least two identical runs are recorded.
- `diagnosed`: a falsified-alternatives trail supports a verified root cause.
- `verified`: a root-cause fix passes targeted checks, broader regressions, deterministic replay, artifact checksum checks, and live-state restoration.
- `unresolved`: no root cause is asserted; at least one safe discriminating experiment is queued.

## Evidence Strength

Prefer evidence in this order:

1. Static invariant or ROM/source proof.
2. Deterministic replay from a hashed fixture with authoritative state.
3. Repeated integration result with fixed versions and inputs.
4. Unit/synthetic fixture.
5. Logs or screenshot diagnostics.
6. Intuition or temporal correlation.

Lower evidence may generate a hypothesis but cannot verify a root cause when stronger evidence is available.

## Verification Check Entry

Each `targeted_checks` and `regression_checks` item uses:

```json
{
  "name": "descriptive check",
  "command": "exact command or API call",
  "passed": true,
  "evidence": "compact result or artifact path"
}
```

`replay` uses `passed`, `runs`, `expected`, `actual`, and `extra_state_deltas`. A verified replay has at least two runs and an empty `extra_state_deltas` list.

## Unresolved Experiment Entry

Each `next_experiments` item uses:

```json
{
  "hypothesis": "falsifiable claim",
  "action": "single-variable experiment",
  "expected_discriminator": "result that separates alternatives",
  "risk": "bounded failure impact",
  "cost": "frames, time, calls, or tokens",
  "rollback": "how the preserved live state is restored"
}
```

Order experiments by information gained, then lower risk and cost.
