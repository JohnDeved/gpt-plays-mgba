# Run Progress Ledger Schema

The ledger is concise durable state, not an event log.

## Required Sections

- `game`: title, version, and 64-character ROM SHA-256.
- `global_goal`: objective, `active` or `complete`, completion oracle, verification flag, and evidence.
- `current_goal`: one active goal with ID, parent, rationale, conditions, and timestamps.
- `goal_history`: achieved or superseded goals; superseded entries require a reason.
- `milestones`: stable IDs, unique nonnegative order, required flag, status, and authoritative evidence.
- `checkpoints`: path, file hash, canonical state hash, map, completed milestone IDs, rank, and verification flag.
- `furthest_checkpoint_sha256`: points to the valid checkpoint with the strongest progression vector.
- `next_actions`: unique priorities, current goal ID, precondition, expected postcondition, and action class.
- `blockers`: scoped unresolved constraints.
- `last_reconciled`: canonical state hash, timestamp, and authoritative sources.

## Evidence Entry

```json
{
  "kind": "ram",
  "source": "decoded badge/event flag address or capability",
  "value": "compact observed value"
}
```

Authoritative kinds are `ram`, `rom`, `emulator_api`, `savestate_decode`, `event_flag`, `trainer_flag`, `badge`, and `key_item`. A screenshot is diagnostic only.

## Checkpoint Rank

`rank` is `[highest_required_order, completed_required_count, completed_total_count]`. A candidate is furthest only when:

1. Its completed required milestones include every required milestone in the current furthest checkpoint; and
2. It has a lexicographically greater rank, or an equal rank with a strict completed-milestone superset.

This prevents a branch that skipped required progression from winning by accumulating optional events. Equal progress keeps the existing checkpoint to avoid churn.

## Goal Condition

```json
{
  "claim": "six legal party members are level 17 and fully healed",
  "oracle": "RAM party decoder and level-cap source",
  "satisfied": false,
  "evidence": []
}
```

An active goal needs at least one unsatisfied condition. When all conditions become true, move it to `goal_history` as `achieved` and create the next active goal in the same edit.

## Milestone Design

Add milestones incrementally as the ROM/save structure proves the required route. Do not prefill a guide-derived story list as fact. Stable IDs should describe the event, such as `defeat-route109-trainer-64`, rather than a transient object slot or coordinate.
