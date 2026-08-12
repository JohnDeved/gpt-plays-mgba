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
- `executable` (optional): one legacy fixed `when`/`directives` block or a
  `rules` array of those blocks. Directives are only `prefer`, `forbid`, or
  `reserve`, and action selectors contain only legal action fields such as
  `kind`, `move_id`, `species`, `slot`, or a bound party `role`.
- `preconditions` (optional): fixed prebattle predicates over battle format and
  party/enemy move, type, or ability sets. Candidate/tested records whose
  preconditions match are trialed automatically in clones.

Supported predicates are battle format, active role, opponent species,
forced switch, fresh entry, usable move IDs, active/opponent types and
abilities, player/opponent HP and status, speed relation, critical-hit
survival, guaranteed KO, role availability, and verified action count.
Automatic matching must include at least one state discriminator beyond
battle format; empty wildcard matchers are rejected. Arbitrary expressions
and Python callbacks are not valid strategy data.

Action selectors may additionally match canonical candidate facts: safety,
guaranteed KO/hit, turn order, survival margin, priority, move type/category,
and switch-target type/ability. These facts come from the same RAM/ROM battle
certificate used by the scorer. Exact move IDs remain appropriate for
move-specific mechanics such as Fake Out.

The policy bundle validator rejects duplicate rule IDs, unknown roles, and
exact overlapping `prefer`/`forbid` directives before clone launch. Prose
requirements remain explanatory; every condition needed for unattended use
must also be represented by a supported executable predicate.

A reproduction contains a pre-state hash, terminal result, action certificate IDs, and source (`clone` or `live`). An exhaustive-search entry records its root hash, search/tool version, legal-action coverage, RNG/AI assumptions, and terminal result. Trainer keys use `map_group:map_number:local_id`.

Promotion is mechanical:

- tested: at least one successful reproduction.
- exact_proven: two successful reproductions and one exhaustive entry with `terminal_win`, `legal_actions_complete`, and `rng_ai_complete` true.
- reusable_tested: three influential clean reproductions on each of two trainers; blind live activation is limited to soft prefer/reserve directives.
- reusable_proven: exact-proven, three distinct state hashes, and two trainer keys.

Any unresolved counterexample blocks promotion. Preserve retired records and their failure evidence. Executable policy
hashes include only engine/profile/directive data; prose, evidence, and review logs do not change the action bundle hash.
