# Strategy record schema

The database root has `schema_version`, `rom_sha256`, and `strategies`.

Each strategy requires:

- `id`, `title`, `status`, `tags`, and `intent`.
- `scope`: the battle format and matchup limits under which the claim applies.
- `requirements`: verified team, resource, speed, setup-window, and state predicates.
- `line`: ordered tactical actions and role assignments.
- `blockers`: opposing moves, abilities, items, field states, or reserve patterns that invalidate the line.
- `abort_rules`: observable RAM conditions that force a stop or replan.
- `evidence`: `reproductions`, `exhaustive_searches`, `distinct_state_hashes`, `trainer_keys`, and `counterexamples`.

A reproduction contains a pre-state hash, terminal result, action certificate IDs, and source (`clone` or `live`). An exhaustive-search entry records its root hash, search/tool version, legal-action coverage, RNG/AI assumptions, and terminal result. Trainer keys use `map_group:map_number:local_id`.

Promotion is mechanical:

- tested: at least one successful reproduction.
- exact_proven: two successful reproductions and one exhaustive entry with `terminal_win`, `legal_actions_complete`, and `rng_ai_complete` true.
- reusable_proven: exact-proven, three distinct state hashes, and two trainer keys.

Any unresolved counterexample blocks promotion. Preserve retired records and their failure evidence.
