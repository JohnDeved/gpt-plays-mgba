# Strategy record schema

The database root has `schema_version`, `rom_sha256`, and `strategies`. Schema
version 2 adds an optional executable block; schema version 1 records remain
readable as historical prose-only strategies.

Each strategy requires:

- `id`, `title`, `status`, `tags`, and `intent`.
- `scope`: the battle format and matchup limits under which the claim applies.
- `requirements`: verified team, resource, speed, setup-window, and state predicates.
- `line`: ordered tactical actions and role assignments.
- `blockers`: opposing moves, abilities, items, field states, or reserve patterns that invalidate the line.
- `abort_rules`: observable RAM conditions that force a stop or replan.
- `evidence`: `reproductions`, `exhaustive_searches`, `distinct_state_hashes`, `trainer_keys`, and `counterexamples`.
- `executable` (optional): a fixed `when` predicate object and non-empty
  `directives` array. Directives are only `prefer`, `forbid`, or `reserve`, and
  action selectors contain only legal action fields such as `kind`, `move_id`,
  `species`, `slot`, or a bound party `role`.

Supported predicates are `active_role`, `opponent_species`, `forced_switch`,
`fresh_entry`, player/opponent HP thresholds, `player_status_any`,
`role_available`, and verified `action_count`. Arbitrary expressions and
Python callbacks are not valid strategy data.

A reproduction contains a pre-state hash, terminal result, action certificate IDs, and source (`clone` or `live`). An exhaustive-search entry records its root hash, search/tool version, legal-action coverage, RNG/AI assumptions, and terminal result. Trainer keys use `map_group:map_number:local_id`.

Promotion is mechanical:

- tested: at least one successful reproduction.
- exact_proven: two successful reproductions and one exhaustive entry with `terminal_win`, `legal_actions_complete`, and `rng_ai_complete` true.
- reusable_proven: exact-proven, three distinct state hashes, and two trainer keys.

Any unresolved counterexample blocks promotion. Preserve retired records and their failure evidence. Executable policy
hashes include only engine/profile/directive data; prose, evidence, and review logs do not change the action bundle hash.
