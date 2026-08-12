"""Reusable, declarative battle policies for Run & Bun.

The policy layer deliberately does not implement battle mechanics.  It ranks
the legal actions and evidence already produced by ``RunBunAdapter`` and adds
small, fixed-shape preferences from a profile or strategy record.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

from .rom_data import ABILITY_TYPE_IMMUNITIES


ROOT = Path(__file__).resolve().parents[2]
STRATEGY_DB = ROOT / ".agents" / "skills" / "develop-runbun-strategies" / "references" / "strategies.json"
POLICY_ENGINE_VERSION = "hybrid-policy-v38"
# Verified Run & Bun ROM move: Infestation prevents voluntary switching while
# its volatile effect is active. Keep this generic so unknown trainers benefit.
VOLATILE_TRAP_MOVE_IDS = frozenset({611})
LOCKED_MULTI_TURN_MOVE_IDS = frozenset({205})  # Rollout, verified five-turn lock.
LOCKED_ENEMY_MOVE_IDS = frozenset({37, 200})  # Thrash and Outrage: next turn is forced after first use.
FLINCH_IMMUNE_ABILITIES = frozenset({19, 39})  # Shield Dust, Inner Focus
FREEZE_STATUS = 0x20
SUPPORTED_PREDICATES = frozenset({
    "battle_format",
    "active_role",
    "opponent_species",
    "forced_switch",
    "fresh_entry",
    "active_move_ids_any",
    "active_move_ids_all",
    "active_types_any",
    "opponent_types_any",
    "active_abilities_any",
    "opponent_abilities_any",
    "player_hp_lte",
    "player_hp_gte",
    "opponent_hp_lte",
    "opponent_hp_gte",
    "player_status_any",
    "opponent_status_any",
    "speed_relation",
    "survives_critical",
    "guaranteed_ko",
    "role_available",
    "action_count",
})
DIRECTIVES = frozenset({"prefer", "forbid", "reserve"})
AUTO_MATCH_DISCRIMINATORS = SUPPORTED_PREDICATES - {"battle_format", "active_role", "role_available", "action_count"}
LIST_PREDICATES = frozenset({
    "active_role", "opponent_species", "active_move_ids_any", "active_move_ids_all",
    "active_types_any", "opponent_types_any", "active_abilities_any", "opponent_abilities_any",
})
ACTION_SELECTOR_FIELDS = frozenset({
    "kind", "actor", "slot", "move_id", "target", "species", "role",
    "safe", "guaranteed_ko", "acts_before_threat", "guaranteed_hit",
    "survival_margin_gte", "priority_gte", "move_type_any", "move_category",
    "switch_target_types_any", "switch_target_abilities_any",
})
ACTIVATION_MODES = frozenset({"legacy", "clone_trial", "blind_live", "qualified_live"})
STRATEGY_STATUSES = frozenset({"candidate", "tested", "reusable_tested", "exact_proven", "reusable_proven", "retired"})
PREBATTLE_PREDICATES = frozenset({
    "battle_format", "party_move_ids_any", "party_move_ids_all", "enemy_move_ids_any",
    "party_types_any", "enemy_types_any", "party_abilities_any", "enemy_abilities_any",
})


class PolicyError(ValueError):
    """A profile cannot safely produce a policy decision."""


def _normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Adapt generated schema-v2 profiles to the small runtime policy shape."""
    if profile.get("schema_version") != 2:
        return profile
    reserves: dict[str, set[int]] = {}
    for assignment in profile.get("matchup_assignments", []):
        species = int((assignment.get("target") or {}).get("species", 0) or 0)
        if not species:
            continue
        for role in [assignment.get("primary")]:
            if role:
                reserves.setdefault(str(role), set()).add(species)
    return {
        **profile,
        "roles": profile.get("party_bindings", {}),
        "strategy_ids": [],
        "reserve_objectives": [
            {"role": role, "for_opponent_species": sorted(species), "switch_bonus": False}
            for role, species in sorted(reserves.items())
        ],
        "constraints": profile.get("exceptions", []),
    }


def _prebattle_context(party: list[dict[str, Any]], trainer: dict[str, Any]) -> dict[str, Any]:
    roster = trainer.get("roster") or []
    return {
        "battle_format": (trainer.get("battle") or {}).get("format", "single"),
        "party_move_ids": sorted({int(move) for mon in party for move in (mon.get("moves") or []) if move}),
        "enemy_move_ids": sorted({int(move) for mon in roster for move in (mon.get("moves") or []) if move}),
        "party_types": sorted({int(value) for mon in party for value in (mon.get("types") or [])}),
        "enemy_types": sorted({int(value) for mon in roster for value in (mon.get("types") or [])}),
        "party_abilities": sorted({int(mon.get("ability", 0) or 0) for mon in party}),
        "enemy_abilities": sorted({int(mon.get("ability_id", 0) or 0) for mon in roster}),
    }


def _prebattle_matches(requirements: dict[str, Any], context: dict[str, Any]) -> bool:
    if set(requirements) - PREBATTLE_PREDICATES:
        return False
    if "battle_format" in requirements and context["battle_format"] not in _set(requirements["battle_format"]):
        return False
    for predicate, field in (
        ("party_move_ids_any", "party_move_ids"), ("enemy_move_ids_any", "enemy_move_ids"),
        ("party_types_any", "party_types"), ("enemy_types_any", "enemy_types"),
        ("party_abilities_any", "party_abilities"), ("enemy_abilities_any", "enemy_abilities"),
    ):
        if predicate in requirements and not (_set(requirements[predicate]) & _set(context[field])):
            return False
    return "party_move_ids_all" not in requirements or _set(requirements["party_move_ids_all"]) <= _set(context["party_move_ids"])


def prebattle_strategy_applicable(strategy: dict[str, Any], context: dict[str, Any]) -> bool:
    return strategy.get("status") != "retired" and _prebattle_matches(strategy.get("preconditions") or {}, context)


def generate_battle_profile(
    party: list[dict[str, Any]],
    trainer: dict[str, Any],
    strategies: Iterable[dict[str, Any]] = (),
    *,
    state_hash: str,
    rom: Any | None = None,
) -> dict[str, Any]:
    """Generate trainer-specific bindings while keeping tactics in the strategy DB."""
    if not party:
        raise PolicyError("generated profile requires a non-empty party")
    if not (trainer.get("battle") or {}).get("roster_complete"):
        raise PolicyError("generated profile requires a complete trainer roster")
    roles = {
        f"member_{index}": {
            key: int(mon[key]) for key in ("personality", "ot_id") if mon.get(key) is not None
        } or {"species": int(mon["species"])}
        for index, mon in enumerate(party)
    }
    chart = rom.type_chart() if rom is not None else {}

    def effectiveness(move_type: int, defender_types: list[int]) -> float:
        value = 1.0
        for defender_type in dict.fromkeys(int(item) for item in defender_types):
            value *= float(chart.get(int(move_type), {}).get(defender_type, 1.0))
        return value

    assignments = []
    for position, enemy in enumerate(trainer.get("roster") or [], 1):
        enemy_types = [int(value) for value in enemy.get("types", [])]
        immune_type = ABILITY_TYPE_IMMUNITIES.get(enemy.get("ability_id"))
        ranked = []
        for index, mon in enumerate(party):
            candidate_types = [int(value) for value in mon.get("types") or []]
            candidate_immune_type = ABILITY_TYPE_IMMUNITIES.get(mon.get("ability"))
            best = 0.0
            for move_id in mon.get("moves") or []:
                if not move_id or rom is None:
                    continue
                move = rom.move(int(move_id))
                if move.category != "status" and move.power > 0:
                    multiplier = 0.0 if move.type_id == immune_type else effectiveness(move.type_id, enemy_types)
                    if int(enemy.get("held_item_id", 0) or 0) == 553 and move.type_id == 12 and multiplier > 1:
                        multiplier /= 2  # Rindo Berry's first super-effective Grass hit.
                    stab = 1.5 if move.type_id in candidate_types else 1.0
                    best = max(best, float(move.power) * multiplier * stab)
            incoming = []
            for move_id in enemy.get("moves") or []:
                if not move_id or rom is None:
                    continue
                move = rom.move(int(move_id))
                if move.category == "status" or move.power <= 0:
                    continue
                defended_types = [value for value in candidate_types if not (int(move_id) == 614 and value == 2)]
                multiplier = 0.0 if move.type_id == candidate_immune_type else effectiveness(move.type_id, defended_types)
                incoming.append(multiplier)
            worst = max(incoming, default=0.0)
            immunities = sum(value == 0 for value in incoming)
            resistances = sum(0 < value < 1 for value in incoming)
            ranked.append((-worst, best, immunities, resistances, int(mon.get("hp", mon.get("max_hp", 0)) or 0), -index, f"member_{index}"))
        ranked.sort(reverse=True)
        assignments.append({
            "target": {
                "roster_position": int(enemy.get("send_out_position", position) or position),
                "species": int(enemy["species_id"]),
            },
            "primary": ranked[0][6],
            "backups": [item[6] for item in ranked[1:3]],
        })
    context = _prebattle_context(party, trainer)
    manifest = {"clone_trial": [], "blind_live_soft": [], "full_live": [], "rejected": []}
    for strategy in sorted(strategies, key=lambda item: str(item.get("id", ""))):
        sid = strategy.get("id")
        status = strategy.get("status")
        if not sid or status == "retired" or not strategy.get("executable"):
            continue
        if not prebattle_strategy_applicable(strategy, context):
            manifest["rejected"].append({"id": sid, "reason": "preconditions_not_met"})
            continue
        manifest["clone_trial"].append(sid)
        if status == "reusable_tested":
            manifest["blind_live_soft"].append(sid)
        elif status in {"exact_proven", "reusable_proven"}:
            manifest["full_live"].append(sid)
    trainer_behavior = {
        "key": trainer.get("key"), "battle": trainer.get("battle"), "roster": trainer.get("roster"),
    }
    trainer_hash = hashlib.sha256(json.dumps(trainer_behavior, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schema_version": 2,
        "id": f"generated-{str(trainer.get('key')).replace(':', '-').replace('/', '-')}",
        "trainer_key": trainer.get("key"),
        "party_bindings": roles,
        "matchup_assignments": assignments,
        "strategy_manifest": manifest,
        "exceptions": [],
        "generation": {
            "party_state_hash": state_hash,
            "trainer_record_hash": trainer_hash,
            "classification": "heuristic" if rom is None else "expected-best",
            "uncertainties": list(trainer.get("uncertainties") or []),
        },
    }


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _species_matches(value: Any, species: int | None) -> bool:
    return species in {int(item) for item in _list(value)}


def _identity(mon: dict[str, Any]) -> tuple[str, int]:
    state = mon.get("state", mon)
    personality = state.get("personality")
    if personality is not None:
        return "personality", int(personality)
    return "species", int(state.get("species", 0))


def _compact_mon(certificate: dict[str, Any], slot: int) -> dict[str, Any]:
    compact = certificate.get("compact_state") or {}
    for mon in (compact.get("battle", {}).get("mons", []) or ()):
        if mon.get("slot") == slot:
            return mon
    state = certificate.get("state", {}).get("player" if slot == 0 else "opponent", {})
    return {"slot": slot, **state}


def _actor_slot(certificate: dict[str, Any], action: dict[str, Any] | None = None) -> int:
    value = (action or {}).get("actor", (certificate.get("boundary") or {}).get("actor", 0))
    return int(value) if value in (0, 2) else 0


def _opponent_slot(action: dict[str, Any] | None = None) -> int:
    value = (action or {}).get("target", 1)
    return int(value) if value in (1, 3) else 1


def _party(certificate: dict[str, Any]) -> list[dict[str, Any]]:
    return list((certificate.get("compact_state") or {}).get("party", []) or [])


def _battle_state(mon: dict[str, Any]) -> dict[str, Any]:
    """Adapt compact RAM fields to the scorer's state-field names."""
    state = dict(mon.get("state", mon))
    if "current_hp" not in state:
        state["current_hp"] = state.get("hp", 0)
    if not state.get("types"):
        try:
            from games.runbun import RunBunAdapter

            state["types"] = RunBunAdapter._mon_types({"state": {**state, "types": ()}})
        except (AttributeError, KeyError, TypeError, ValueError):
            state["types"] = ()
    return state


def _damage_bounds(
    move_id: int,
    attacker: dict[str, Any],
    defender: dict[str, Any],
    move_data: dict[int, SimpleNamespace] | None = None,
) -> tuple[float, float]:
    try:
        from games.runbun import RunBunAdapter

        return RunBunAdapter._damage_bounds(
            move_id, _battle_state(attacker), _battle_state(defender), move_data=move_data,
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return (0.0, 0.0)


def _certificate_move_data(certificate: dict[str, Any]) -> dict[int, SimpleNamespace]:
    return {
        int(move_id): SimpleNamespace(**metadata)
        for move_id, metadata in (certificate.get("move_data") or {}).items()
    }


def _critical_damage_bound(move_id: int, normal_max: float) -> float:
    """Keep fixed-damage moves out of the ordinary critical multiplier."""
    try:
        from games.runbun import MOVE_FIXED_DAMAGE_FRACTIONS

        if move_id in MOVE_FIXED_DAMAGE_FRACTIONS:
            return normal_max
    except (ImportError, AttributeError):
        pass
    return normal_max * 2


def _survival_margin_after_hits(
    move_ids: Iterable[int],
    attacker: dict[str, Any],
    defender: dict[str, Any],
    hits: int,
    move_data: dict[int, SimpleNamespace] | None = None,
) -> float:
    """Return worst remaining HP while recomputing current-HP damage each hit."""
    states = [float(defender.get("current_hp", defender.get("hp", 0)) or 0)]
    moves = tuple(int(move_id) for move_id in move_ids if move_id)
    for _ in range(hits):
        states = [
            hp - _critical_damage_bound(
                move_id,
                _damage_bounds(
                    move_id, attacker,
                    {**defender, "current_hp": max(0, int(hp))}, move_data,
                )[1],
            )
            for hp in states
            for move_id in moves
        ] or states
    return min(states)


def _definitely_incapacitated(status: int) -> bool:
    return bool(status & FREEZE_STATUS or status & 0x7)


def _status_residual_damage(status: int, max_hp: int) -> int:
    if status & 0x10:  # Burn uses the modern 1/16 tick in this ROM.
        return max(1, max_hp // 16)
    if status & (0x08 | 0x80):
        return max(1, max_hp // 8)
    return 0


def _switch_projection(
    certificate: dict[str, Any],
    action: dict[str, Any],
    opponent: dict[str, Any],
    *,
    forced_switch: bool,
) -> dict[str, Any]:
    """Project whether a replacement reaches and survives its next action."""
    target = next((mon for mon in _party(certificate) if mon.get("slot") == action.get("slot")), {})
    target_hp = int(target.get("hp", target.get("current_hp", action.get("hp", 0))) or 0)
    target_state = _battle_state(target)
    opponent_state = _battle_state(opponent)
    move_data = _certificate_move_data(certificate)
    outgoing = [
        _damage_bounds(int(move_id), target_state, opponent_state, move_data)
        for slot, move_id in enumerate(target_state.get("moves", ()) or ())
        if move_id and int((target_state.get("pp") or (0, 0, 0, 0))[slot] or 0) > 0
    ]
    minimum = max((bounds[0] for bounds in outgoing), default=0.0)
    estimate = max(((bounds[0] + bounds[1]) / 2 for bounds in outgoing), default=0.0)
    from games.runbun import MOVE_PRIORITY_IDS

    target_moves = target_state.get("moves", ()) or ()
    target_pp = target_state.get("pp", ()) or ()
    acts_before_threat = bool(
        _effective_speed(target_state) > _effective_speed(opponent_state)
        or any(
            move_id in MOVE_PRIORITY_IDS and slot < len(target_pp) and int(target_pp[slot] or 0) > 0
            for slot, move_id in enumerate(target_moves)
        )
    )
    required_hits = 1 if forced_switch or acts_before_threat else 2
    survival_margin = _survival_margin_after_hits(
        opponent_state.get("moves", ()), opponent_state, target_state, required_hits, move_data
    )
    status = int(target.get("status", 0) or 0)
    survival_margin -= _status_residual_damage(status, int(target.get("max_hp", target_hp) or target_hp))
    safe = target_hp > 0 and not _definitely_incapacitated(status) and survival_margin > 0
    return {
        "safe": safe,
        "survival_margin": survival_margin,
        "required_hits": required_hits,
        "acts_before_threat": acts_before_threat,
        "damage_min": minimum,
        "damage_est": estimate,
        "target_identity": _identity_value(target or action),
    }


def _automatic_strategy(strategy: dict[str, Any]) -> bool:
    """Only cross-trainer proof is strong enough for unattended activation."""
    return strategy.get("status") == "reusable_proven" and bool(strategy.get("auto_match"))


def _profile_behavior(
    profile: dict[str, Any], strategies: Iterable[dict[str, Any]], activation_mode: str = "legacy",
) -> dict[str, Any]:
    try:
        from games.runbun import BATTLE_SCORER_VERSION
        from games.run_and_bun.capabilities import BATTLE_EXECUTION_VERSION, BATTLE_OBSERVATION_VERSION
    except ImportError:
        BATTLE_SCORER_VERSION = "runbun-tactical-v1"
        BATTLE_OBSERVATION_VERSION = "battle-cert-v1"
        BATTLE_EXECUTION_VERSION = "battle-step-v1"
    executable_strategies = []
    normalized = _normalize_profile(profile)
    manifest = profile.get("strategy_manifest") or {}
    wanted = set(normalized.get("strategy_ids", ())) | {
        str(sid) for key in ("clone_trial", "blind_live_soft", "full_live") for sid in manifest.get(key, [])
    }
    for strategy in strategies:
        if (strategy.get("id") in wanted or _automatic_strategy(strategy)) and strategy.get("executable"):
            executable_strategies.append({
                "id": strategy["id"],
                "executable": strategy["executable"],
                "auto_match": strategy.get("auto_match"),
            })
    return {
        "engine_version": POLICY_ENGINE_VERSION,
        "activation_mode": activation_mode,
        "tactical_scorer_version": BATTLE_SCORER_VERSION,
        "observation_version": BATTLE_OBSERVATION_VERSION,
        "execution_version": BATTLE_EXECUTION_VERSION,
        "profile": {
            "id": normalized.get("id"),
            "trainer_key": normalized.get("trainer_key"),
            "roles": normalized.get("roles", {}),
            "strategy_ids": normalized.get("strategy_ids", []),
            "strategy_manifest": manifest,
            "reserve_objectives": normalized.get("reserve_objectives", []),
            "constraints": normalized.get("constraints", []),
        },
        "strategies": sorted(executable_strategies, key=lambda item: item["id"]),
    }


def policy_bundle_hash(
    profile: dict[str, Any], strategies: Iterable[dict[str, Any]] = (), activation_mode: str = "legacy",
) -> str:
    """Hash only behavior-affecting policy data, not prose or evidence."""
    payload = json.dumps(
        _profile_behavior(profile, strategies, activation_mode),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_profile(profile: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if profile.get("schema_version") not in {1, 2}:
        errors.append("schema_version must be 1 or 2")
    if not isinstance(profile.get("id"), str) or not profile["id"].strip():
        errors.append("id is required")
    normalized = _normalize_profile(profile)
    roles = normalized.get("roles")
    if not isinstance(roles, dict) or not roles:
        errors.append("roles must be a non-empty object")
        roles = {}
    seen_species: dict[int, str] = {}
    for role, binding in roles.items():
        if not isinstance(role, str) or not role.strip() or not isinstance(binding, dict):
            errors.append(f"invalid role binding: {role}")
            continue
        selectors = [key for key in ("species", "personality", "ot_id") if key in binding]
        if not selectors or any(not isinstance(binding[key], int) or binding[key] <= 0 for key in selectors):
            errors.append(f"roles.{role} must bind a positive species or personality")
        if "species" in binding:
            species = int(binding["species"])
            if species in seen_species:
                errors.append(f"species {species} is ambiguously bound to {seen_species[species]} and {role}")
            seen_species[species] = role
    errors.extend(_validate_rules(normalized.get("constraints", []), "constraints"))
    reserves = normalized.get("reserve_objectives", [])
    if not isinstance(reserves, list):
        errors.append("reserve_objectives must be an array")
    else:
        for index, reserve in enumerate(reserves):
            if not isinstance(reserve, dict) or reserve.get("role") not in roles:
                errors.append(f"reserve_objectives[{index}].role must reference a known role")
            if not isinstance(reserve.get("for_opponent_species"), list) or not reserve["for_opponent_species"]:
                errors.append(f"reserve_objectives[{index}].for_opponent_species must be non-empty")
    strategies = normalized.get("strategy_ids", [])
    if not isinstance(strategies, list) or not all(isinstance(item, str) and item for item in strategies):
        errors.append("strategy_ids must be an array of non-empty strings")
    if profile.get("schema_version") == 2:
        manifest = profile.get("strategy_manifest")
        if not isinstance(manifest, dict):
            errors.append("strategy_manifest must be an object")
        else:
            for key in ("clone_trial", "blind_live_soft", "full_live"):
                values = manifest.get(key, [])
                if not isinstance(values, list) or not all(isinstance(item, str) and item for item in values):
                    errors.append(f"strategy_manifest.{key} must be an array of strategy IDs")
    return errors


def validate_auto_match(match: Any, name: str = "auto_match") -> list[str]:
    """Validate the same fixed predicates used by unattended strategies."""
    if not isinstance(match, dict):
        return [f"{name} must be an object"]
    normalized = dict(match)
    if "battle_formats" in normalized:
        if "battle_format" in normalized:
            return [f"{name} cannot contain both battle_format and battle_formats"]
        normalized["battle_format"] = normalized.pop("battle_formats")
    errors = _validate_when(normalized, name, allow_roles=False)
    if not set(normalized) & AUTO_MATCH_DISCRIMINATORS:
        errors.append(f"{name} needs at least one state discriminator besides battle_format")
    return errors


def _validate_when(when: Any, name: str, *, allow_roles: bool = True) -> list[str]:
    if not isinstance(when, dict):
        return [f"{name} must be an object"]
    errors: list[str] = []
    unknown = set(when) - SUPPORTED_PREDICATES
    errors.extend(f"{name}.{key} is unsupported" for key in sorted(unknown))
    if not allow_roles:
        errors.extend(
            f"{name}.{key} requires trainer-profile role bindings"
            for key in ("active_role", "role_available", "action_count")
            if key in when
        )
    for key in LIST_PREDICATES & set(when):
        values = _list(when[key])
        expected = str if key == "active_role" else int
        if not values or any(not isinstance(value, expected) or isinstance(value, bool) for value in values):
            errors.append(f"{name}.{key} must contain at least one {expected.__name__}")
    if "battle_format" in when:
        formats = set(_list(when["battle_format"]))
        if not formats or not formats <= {"single", "double"}:
            errors.append(f"{name}.battle_format must contain single or double")
    if "speed_relation" in when and when["speed_relation"] not in {"faster", "slower", "tie"}:
        errors.append(f"{name}.speed_relation must be faster, slower, or tie")
    for key in ("forced_switch", "fresh_entry", "survives_critical", "guaranteed_ko"):
        if key in when and not isinstance(when[key], bool):
            errors.append(f"{name}.{key} must be boolean")
    for key in (
        "player_hp_lte", "player_hp_gte", "opponent_hp_lte", "opponent_hp_gte",
        "player_status_any", "opponent_status_any",
    ):
        if key in when and (not isinstance(when[key], int) or isinstance(when[key], bool)):
            errors.append(f"{name}.{key} must be integer")
    for low, high in (("player_hp_gte", "player_hp_lte"), ("opponent_hp_gte", "opponent_hp_lte")):
        if low in when and high in when and isinstance(when[low], int) and isinstance(when[high], int) and when[low] > when[high]:
            errors.append(f"{name}.{low} cannot exceed {high}")
    count = when.get("action_count")
    if count is not None and (
        not isinstance(count, dict)
        or not isinstance(count.get("move_id"), int)
        or not any(key in count for key in ("equals", "lte", "gte"))
    ):
        errors.append(f"{name}.action_count requires move_id and equals/lte/gte")
    return errors


def _validate_rules(rules: Any, name: str, *, require_ids: bool = True) -> list[str]:
    errors: list[str] = []
    if not isinstance(rules, list):
        return [f"{name} must be an array"]
    for index, rule in enumerate(rules):
        prefix = f"{name}[{index}]"
        if not isinstance(rule, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if require_ids and (not isinstance(rule.get("id"), str) or not rule["id"].strip()):
            errors.append(f"{prefix}.id is required")
        errors.extend(_validate_when(rule.get("when", {}), f"{prefix}.when"))
        directives = rule.get("directives", [])
        if not isinstance(directives, list) or not directives:
            errors.append(f"{prefix}.directives must be a non-empty array")
            continue
        for directive in directives:
            if not isinstance(directive, dict) or directive.get("kind") not in DIRECTIVES:
                errors.append(f"{prefix} has unsupported directive")
            elif directive.get("kind") == "reserve" and not isinstance(directive.get("role"), str):
                errors.append(f"{prefix}.reserve requires a role")
            elif directive.get("kind") != "reserve" and not isinstance(directive.get("action"), dict):
                errors.append(f"{prefix}.{directive['kind']} requires an action object")
            elif directive.get("kind") != "reserve":
                unknown = set(directive["action"]) - ACTION_SELECTOR_FIELDS
                errors.extend(f"{prefix}.{directive['kind']}.action.{key} is unsupported" for key in sorted(unknown))
    return errors


def _strategy_rules(strategy: dict[str, Any]) -> list[dict[str, Any]]:
    executable = strategy.get("executable")
    if not isinstance(executable, dict):
        return []
    rules = executable.get("rules")
    return list(rules) if isinstance(rules, list) else [executable]


def validate_policy_bundle(profile: dict[str, Any], strategies: Iterable[dict[str, Any]]) -> list[str]:
    """Reject unsafe bundle wiring before the first clone or live action."""
    errors = validate_profile(profile)
    rows = list(strategies)
    by_id: dict[str, dict[str, Any]] = {}
    for index, strategy in enumerate(rows):
        sid = strategy.get("id")
        if not isinstance(sid, str) or not sid:
            errors.append(f"strategies[{index}].id is required")
            continue
        if sid in by_id:
            errors.append(f"duplicate strategy id {sid}")
        by_id[sid] = strategy
        if strategy.get("auto_match") is not None:
            errors.extend(validate_auto_match(strategy["auto_match"], f"strategies[{index}].auto_match"))
        errors.extend(_validate_rules(_strategy_rules(strategy), f"strategies[{index}].executable", require_ids=False))
    normalized = _normalize_profile(profile)
    manifest = profile.get("strategy_manifest") or {}
    wanted = set(normalized.get("strategy_ids", ())) | {
        str(sid) for key in ("clone_trial", "blind_live_soft", "full_live") for sid in manifest.get(key, [])
    }
    errors.extend(f"unknown strategy id {sid}" for sid in sorted(wanted - set(by_id)))

    selected: list[tuple[str, dict[str, Any]]] = [
        (f"profile:{rule.get('id', index)}", rule)
        for index, rule in enumerate(normalized.get("constraints", []))
    ]
    for strategy in rows:
        if strategy.get("id") in wanted or _automatic_strategy(strategy):
            selected.extend(
                (f"strategy:{strategy.get('id')}/{rule.get('id', index)}", rule)
                for index, rule in enumerate(_strategy_rules(strategy))
            )

    seen_rule_ids: set[str] = set()
    effects: dict[tuple[str, str], dict[str, set[str]]] = {}
    roles = set(normalized.get("roles", {}))
    for source, rule in selected:
        if source in seen_rule_ids:
            errors.append(f"duplicate executable rule id {source}")
        seen_rule_ids.add(source)
        when_key = json.dumps(rule.get("when", {}), sort_keys=True, separators=(",", ":"))
        for directive in rule.get("directives", []):
            if directive.get("kind") == "reserve":
                if directive.get("role") not in roles:
                    errors.append(f"{source} reserves unknown role {directive.get('role')}")
                continue
            selector = directive.get("action", {})
            if selector.get("role") is not None and selector.get("role") not in roles:
                errors.append(f"{source} selects unknown role {selector.get('role')}")
            selector_key = json.dumps(selector, sort_keys=True, separators=(",", ":"))
            kinds = effects.setdefault((when_key, selector_key), {})
            kinds.setdefault(str(directive.get("kind")), set()).add(source)
    for (_when, selector), kinds in effects.items():
        if kinds.get("prefer") and kinds.get("forbid"):
            sources = sorted(kinds["prefer"] | kinds["forbid"])
            errors.append(f"contradictory prefer/forbid for action {selector}: {', '.join(sources)}")
    return errors


def load_profile(path: str | Path) -> dict[str, Any]:
    profile = json.loads(Path(path).read_text(encoding="utf-8"))
    errors = validate_profile(profile)
    if errors:
        raise PolicyError("; ".join(errors))
    return profile


def load_strategies(path: str | Path = STRATEGY_DB) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") not in {1, 2}:
        raise PolicyError("unsupported strategy database schema")
    return list(data.get("strategies", []))


def _set(value: Any) -> set[Any]:
    if value is None:
        return set()
    return set(value if isinstance(value, (list, tuple, set)) else [value])


def _fixed_context_matches(match: dict[str, Any], context: dict[str, Any]) -> bool:
    """Evaluate only the fixed, JSON-safe tactical predicates."""
    if "battle_format" in match and context.get("battle_format") not in _set(match["battle_format"]):
        return False
    intersections = {
        "active_role": "active_role",
        "opponent_species": "opponent_species",
        "active_move_ids_any": "active_move_ids",
        "active_types_any": "active_types",
        "opponent_types_any": "opponent_types",
        "active_abilities_any": "active_ability",
        "opponent_abilities_any": "opponent_abilities",
    }
    for predicate, field in intersections.items():
        if predicate in match and not (_set(match[predicate]) & _set(context.get(field))):
            return False
    if "active_move_ids_all" in match and not _set(match["active_move_ids_all"]) <= _set(context.get("active_move_ids")):
        return False
    for predicate, field in (
        ("forced_switch", "forced_switch"),
        ("fresh_entry", "fresh_entry"),
        ("survives_critical", "survives_critical"),
        ("guaranteed_ko", "guaranteed_ko"),
    ):
        if predicate in match and bool(match[predicate]) != bool(context.get(field)):
            return False
    for predicate, field, compare in (
        ("player_hp_lte", "player_hp", lambda actual, wanted: actual <= wanted),
        ("player_hp_gte", "player_hp", lambda actual, wanted: actual >= wanted),
        ("opponent_hp_lte", "opponent_hp", lambda actual, wanted: actual <= wanted),
        ("opponent_hp_gte", "opponent_hp", lambda actual, wanted: actual >= wanted),
    ):
        if predicate in match and not compare(int(context.get(field, 0) or 0), int(match[predicate])):
            return False
    if "player_status_any" in match and not int(context.get("player_status", 0) or 0) & int(match["player_status_any"]):
        return False
    if "opponent_status_any" in match and not int(context.get("opponent_status", 0) or 0) & int(match["opponent_status_any"]):
        return False
    if "speed_relation" in match and match["speed_relation"] != context.get("speed_relation"):
        return False
    if "role_available" in match and match["role_available"] not in _set(context.get("roles_available")):
        return False
    return True


def strategy_applicable(strategy: dict[str, Any], context: dict[str, Any]) -> bool:
    """Match the fixed executable applicability schema."""
    match = strategy.get("auto_match")
    if validate_auto_match(match):
        return False
    # Schema 2 used battle_formats; accept it during migration without
    # allowing the rest of the legacy matcher to bypass validation.
    normalized = dict(match)
    if "battle_formats" in normalized:
        normalized["battle_format"] = normalized.pop("battle_formats")
    return _fixed_context_matches(normalized, context)


def _effective_speed(mon: dict[str, Any]) -> float:
    try:
        from games.runbun import RunBunAdapter

        return float(RunBunAdapter._effective_speed(_battle_state(mon)))
    except (AttributeError, KeyError, TypeError, ValueError):
        return float(_battle_state(mon).get("speed", 0) or 0)


def _strategy_context(
    certificate: dict[str, Any],
    *,
    history: "BattleHistory | None" = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actor = _actor_slot(certificate)
    active = _compact_mon(certificate, actor)
    battle_mons = list(((certificate.get("compact_state") or {}).get("battle") or {}).get("mons", []) or [])
    opponent_slots = (1, 3) if (certificate.get("boundary") or {}).get("format") == "double" else (1,)
    opponents = [
        mon for mon in battle_mons
        if mon.get("slot") in opponent_slots
        and int(mon.get("species", 0) or 0) > 0
        and int(mon.get("hp", mon.get("current_hp", 0)) or 0) > 0
    ]
    opponent = opponents[0] if opponents else _compact_mon(certificate, 1)
    active_state = _battle_state(active)
    opponent_state = _battle_state(opponent)
    pp = active_state.get("pp") or ()
    moves = [
        int(move_id) for index, move_id in enumerate(active_state.get("moves") or ())
        if move_id and (index >= len(pp) or int(pp[index] or 0) > 0)
    ]
    report = certificate.get("report", certificate)
    incoming_critical = float((report.get("incoming") or {}).get("critical_max_damage_est", 0) or 0)
    player_hp = int(active.get("hp", active.get("current_hp", 0)) or 0)
    alternatives = report.get("alternatives") or []
    opponent_hp = int(opponent.get("hp", opponent.get("current_hp", 0)) or 0)
    guaranteed_ko = any(
        item.get("guaranteed_hit", True)
        and float((item.get("damage_range") or [0])[0]) >= opponent_hp > 0
        for item in alternatives
        if int(item.get("actor", actor)) == actor
    )
    active_speed = _effective_speed(active_state)
    opponent_speed = max((_effective_speed(item) for item in opponents), default=_effective_speed(opponent_state))
    roles_available = []
    if profile:
        roles_available = [
            role for mon in _party(certificate)
            if int(mon.get("hp", mon.get("current_hp", 0)) or 0) > 0
            and (role := _role_for_mon(profile, mon)) is not None
        ]
    return {
        "battle_format": (certificate.get("boundary") or {}).get("format", "single"),
        "active_role": _role_for_mon(profile or {}, active),
        "opponent_species": [int(item.get("species", 0) or 0) for item in opponents] or [int(opponent.get("species", 0) or 0)],
        "active_move_ids": moves,
        "active_types": list(active_state.get("types") or ()),
        "opponent_types": sorted({int(type_id) for item in opponents for type_id in (_battle_state(item).get("types") or ())}),
        "active_ability": int(active_state.get("ability", 0) or 0),
        "opponent_abilities": [int(_battle_state(item).get("ability", 0) or 0) for item in opponents],
        "player_hp": player_hp,
        "opponent_hp": opponent_hp,
        "player_status": int(active_state.get("status", 0) or 0),
        "opponent_status": int(opponent_state.get("status", 0) or 0),
        "forced_switch": bool((certificate.get("boundary") or {}).get("party_switch_required")),
        "fresh_entry": history.fresh_entry_for(certificate) if history else bool((certificate.get("history") or {}).get("fresh_entry", True)),
        "survives_critical": incoming_critical < player_hp,
        "guaranteed_ko": guaranteed_ko,
        "speed_relation": "faster" if active_speed > opponent_speed else "slower" if active_speed < opponent_speed else "tie",
        "roles_available": roles_available,
    }


def _strategy_applies_to_certificate(
    strategy: dict[str, Any],
    certificate: dict[str, Any],
    *,
    history: "BattleHistory | None" = None,
    profile: dict[str, Any] | None = None,
) -> bool:
    return strategy_applicable(strategy, _strategy_context(certificate, history=history, profile=profile))


class BattleHistory:
    """Small replayable state derived only from verified action records."""

    def __init__(self) -> None:
        self._counts: Counter[tuple[str, int, int]] = Counter()
        self._fresh_entry = True
        self._fresh_by_identity: dict[str, bool] = {}
        self._records: list[dict[str, Any]] = []
        self._switch_edges: list[tuple[int, str, str]] = []
        self._trapped_identity: str | None = None
        self._trap_turns = 0
        self._defeated_species: set[int] = set()
        self._fresh_enemy_by_identity: dict[str, bool] = {}
        self._enemy_lock_move: int | None = None
        self._enemy_lock_species: int | None = None
        self._enemy_lock_uses = 0

    @classmethod
    def from_jsonl(
        cls,
        path: str | Path,
        *,
        battle_id: str | None = None,
        before_state_hash: str | None = None,
    ) -> "BattleHistory":
        history = cls()
        source = Path(path)
        if not source.exists():
            return history
        for line in source.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("kind") == "battle_transition":
                continue
            if battle_id is not None and record.get("battle_id") != battle_id:
                continue
            if before_state_hash is not None and record.get("pre_state_hash") == before_state_hash:
                break
            if record.get("action") and record.get("verified") is True:
                history.record(record.get("certificate") or {}, record)
        return history

    @property
    def fresh_entry(self) -> bool:
        return self._fresh_entry

    def fresh_entry_for(self, certificate: dict[str, Any], action: dict[str, Any] | None = None) -> bool:
        identity = _identity_value(_compact_mon(certificate, _actor_slot(certificate, action)))
        return self._fresh_by_identity.get(identity, True)

    @property
    def switch_forbidden(self) -> bool:
        return self._trap_turns > 0

    def switch_forbidden_for(self, certificate: dict[str, Any], action: dict[str, Any] | None = None) -> bool:
        """Only the currently trapped battler loses voluntary switching."""
        return self._trap_turns > 0 and self._trapped_identity == _identity_value(_compact_mon(certificate, _actor_slot(certificate, action)))

    def repeats_last_switch(self, certificate: dict[str, Any], action: dict[str, Any]) -> bool:
        """Detect a voluntary immediate reversal after a prior pivot.

        This is deliberately narrow: it catches A→B→C→B, not all switching.
        A first tactical pivot remains legal, while an unproductive reversal
        cannot keep consuming HP without creating progress.
        """
        actor = _actor_slot(certificate, action)
        edges = [edge for edge in self._switch_edges if edge[0] == actor]
        if action.get("kind") != "switch" or not self.fresh_entry_for(certificate, action) or len(edges) < 1:
            return False
        active = _identity_value(_compact_mon(certificate, _actor_slot(certificate, action)))
        target = _identity_value(action)
        _actor, previous_origin, previous_target = edges[-1]
        return previous_target == active and previous_origin == target

    @property
    def consecutive_switches(self) -> int:
        count = 0
        for record in reversed(self._records):
            if record.get("action", {}).get("kind") != "switch":
                break
            count += 1
        return count

    def consecutive_switches_for(self, certificate: dict[str, Any], action: dict[str, Any] | None = None) -> int:
        actor = _actor_slot(certificate, action)
        count = 0
        for record in reversed(self._records):
            if record.get("actor_slot") != actor:
                continue
            if record.get("action", {}).get("kind") != "switch" or record.get("forced_switch"):
                break
            count += 1
        return count

    def action_count(self, role: str, opponent_species: int, move_id: int, profile: dict[str, Any], certificate: dict[str, Any]) -> int:
        player = _compact_mon(certificate, _actor_slot(certificate))
        if role != _role_for_mon(profile, player):
            return 0
        return self._counts[(_identity_value(player), int(opponent_species), int(move_id))]

    def opponent_fresh_entry(self, opponent: dict[str, Any]) -> bool:
        return self._fresh_enemy_by_identity.get(_identity_value(opponent), True)

    def enemy_move_constraint(
        self, opponent_species: int, *, opponent: dict[str, Any] | None = None,
    ) -> set[int] | None:
        """Return a forced second Thrash/Outrage turn; later duration is random."""
        if self._enemy_lock_species == int(opponent_species) and self._enemy_lock_uses == 1:
            return {int(self._enemy_lock_move)} if self._enemy_lock_move is not None else None
        if opponent is not None and not self.opponent_fresh_entry(opponent):
            state = _battle_state(opponent)
            return {int(move_id) for move_id in state.get("moves", ()) if move_id and int(move_id) != 252}
        return None

    def record(self, certificate: dict[str, Any], result: dict[str, Any] | dict[str, Any]) -> None:
        action = result.get("action", result)
        if not isinstance(action, dict):
            return
        player = _compact_mon(certificate, _actor_slot(certificate, action))
        opponent = _compact_mon(certificate, _opponent_slot(action))
        if action.get("kind") == "move" and action.get("move_id") is not None:
            self._counts[(_identity_value(player), int(opponent.get("species", 0)), int(action["move_id"]))] += 1
        player_identity = _identity_value(player)
        actual = result.get("actual", {}) if isinstance(result, dict) else {}
        enemy_move = actual.get("enemy_move_id")
        actor_slot = _actor_slot(certificate, action)
        post_battler = next(
            (mon for mon in (result.get("observation", {}).get("battle", {}).get("mons", []) or ()) if mon.get("slot") == actor_slot),
            None,
        ) if isinstance(result, dict) else None
        post_state = _battle_state(post_battler or {})
        post_opponent = next(
            (mon for mon in (result.get("observation", {}).get("battle", {}).get("mons", []) or ()) if mon.get("slot") == _opponent_slot(action)),
            None,
        ) if isinstance(result, dict) else None
        post_opponent_state = _battle_state(post_opponent or {})
        opponent_species = int(opponent.get("species", 0) or 0)
        opponent_identity = _identity_value(opponent)
        self._fresh_enemy_by_identity[opponent_identity] = False
        if post_opponent_state.get("species") and int(post_opponent_state.get("current_hp", post_opponent_state.get("hp", 0)) or 0) > 0:
            post_opponent_identity = _identity_value(post_opponent_state)
            if post_opponent_identity != opponent_identity:
                self._fresh_enemy_by_identity[post_opponent_identity] = True
        if opponent_species and post_opponent is not None and (
            int(post_opponent_state.get("species", 0) or 0) != opponent_species
            or int(post_opponent_state.get("current_hp", post_opponent_state.get("hp", 0)) or 0) <= 0
        ):
            self._defeated_species.add(opponent_species)
            self._enemy_lock_move = self._enemy_lock_species = None
            self._enemy_lock_uses = 0
        elif enemy_move is not None and int(enemy_move) in LOCKED_ENEMY_MOVE_IDS:
            if self._enemy_lock_move == int(enemy_move) and self._enemy_lock_species == opponent_species:
                self._enemy_lock_uses += 1
            else:
                self._enemy_lock_move = int(enemy_move)
                self._enemy_lock_species = opponent_species
                self._enemy_lock_uses = 1
        elif enemy_move is not None or int(action.get("move_id", 0) or 0) == 252:
            self._enemy_lock_move = self._enemy_lock_species = None
            self._enemy_lock_uses = 0
        if action.get("kind") == "switch" and (action.get("species") or action.get("personality")):
            post_identity = _identity_value(action)
        else:
            post_identity = _identity_value(post_state) if post_state.get("species") else player_identity
        if enemy_move is not None and int(enemy_move) in VOLATILE_TRAP_MOVE_IDS:
            # A switch changes the active identity before the opponent's reply;
            # trap the post-action battler, not the one that left.
            self._trapped_identity = post_identity
            self._trap_turns = 5
        elif self._trap_turns:
            if post_identity != self._trapped_identity:
                self._trapped_identity = None
                self._trap_turns = 0
            else:
                self._trap_turns = max(0, self._trap_turns - 1)
                if not self._trap_turns:
                    self._trapped_identity = None
        self._fresh_by_identity[player_identity] = False
        self._fresh_by_identity[post_identity] = post_identity != player_identity
        self._fresh_entry = self._fresh_by_identity[post_identity]
        if action.get("kind") == "switch":
            self._switch_edges.append((actor_slot, player_identity, post_identity))
        self._records.append({
            "action": dict(action),
            "actor_slot": actor_slot,
            "actor_identity": player_identity,
            "post_identity": post_identity,
            "opponent_species": opponent.get("species"),
            "enemy_move_id": int(enemy_move) if enemy_move is not None else None,
            "forced_switch": bool((certificate.get("boundary") or {}).get("party_switch_required")),
        })

    def snapshot(self) -> dict[str, Any]:
        return {
            "fresh_entry": self._fresh_entry,
            "fresh_entries": dict(sorted(self._fresh_by_identity.items())),
            "switch_forbidden": self.switch_forbidden,
            "trapped_identity": self._trapped_identity,
            "trap_turns": self._trap_turns,
            "defeated_species": sorted(self._defeated_species),
            "recent_switch_edges": [list(edge) for edge in self._switch_edges[-4:]],
            "action_counts": [
                {"actor": actor, "opponent_species": opponent, "move_id": move, "count": count}
                for (actor, opponent, move), count in sorted(self._counts.items())
            ],
            "verified_actions": len(self._records),
            "enemy_move_constraint": sorted(self.enemy_move_constraint(self._enemy_lock_species or 0) or ()),
        }


def _identity_value(mon: dict[str, Any]) -> str:
    kind, value = _identity(mon)
    return f"{kind}:{value}"


def _role_for_mon(profile: dict[str, Any], mon: dict[str, Any]) -> str | None:
    state = mon.get("state", mon)
    for role, binding in profile.get("roles", {}).items():
        keys = [key for key in ("personality", "ot_id", "species") if key in binding]
        matches = [state.get(key) == binding.get(key) for key in keys]
        if matches and (all(matches) if profile.get("schema_version") == 2 else any(matches)):
            return role
    return None


def _active_role(profile: dict[str, Any], certificate: dict[str, Any]) -> str | None:
    return _role_for_mon(profile, _compact_mon(certificate, _actor_slot(certificate)))


def _match_when(
    when: dict[str, Any],
    *,
    profile: dict[str, Any],
    certificate: dict[str, Any],
    history: BattleHistory,
) -> bool:
    context = _strategy_context(certificate, history=history, profile=profile)
    if not _fixed_context_matches(
        {key: value for key, value in when.items() if key != "action_count"},
        context,
    ):
        return False
    count = when.get("action_count")
    if count:
        active_role = context.get("active_role")
        opponent_species = min(_set(context.get("opponent_species")), default=0)
        role = count.get("role", active_role)
        observed = history.action_count(role, int(opponent_species), int(count["move_id"]), profile, certificate)
        if "equals" in count and observed != int(count["equals"]):
            return False
        if "lte" in count and observed > int(count["lte"]):
            return False
        if "gte" in count and observed < int(count["gte"]):
            return False
    return True


def _action_matches(
    action: dict[str, Any], selector: dict[str, Any], role: str | None, certificate: dict[str, Any],
) -> bool:
    if selector.get("kind") and action.get("kind") != selector["kind"]:
        return False
    if selector.get("move_id") is not None and action.get("move_id") != selector["move_id"]:
        return False
    if selector.get("species") is not None and action.get("species") != selector["species"]:
        return False
    if selector.get("slot") is not None and action.get("slot") != selector["slot"]:
        return False
    if selector.get("role") is not None and role != selector["role"]:
        return False
    report = certificate.get("report", certificate)
    actor = _actor_slot(certificate, action)
    target_slot = _opponent_slot(action)
    opponent = _compact_mon(certificate, target_slot)
    player = _compact_mon(certificate, actor)
    move = next((
        item for item in report.get("alternatives", [])
        if int(item.get("actor", actor)) == actor
        and int(item.get("slot", -1)) == int(action.get("slot", -1))
        and int(item.get("move_id", -1)) == int(action.get("move_id", -1))
        and (action.get("target") not in (1, 3) or int(item.get("target", target_slot)) == target_slot)
    ), None)
    if action.get("kind") == "move":
        incoming = (report.get("incoming_by_target") or {}).get(str(target_slot), report.get("incoming") or {})
        critical = float(incoming.get("critical_max_damage_est", 0) or 0)
        player_hp = int(player.get("hp", player.get("current_hp", 0)) or 0)
        opponent_hp = int(opponent.get("hp", opponent.get("current_hp", 0)) or 0)
        minimum = float(((move or {}).get("damage_range") or [0])[0])
        facts = {
            "safe": bool((move or {}).get("ko_before_hit") or critical < player_hp),
            "guaranteed_ko": bool((move or {}).get("guaranteed_hit", True) and minimum >= opponent_hp > 0),
            "acts_before_threat": bool((move or {}).get("acts_first")),
            "guaranteed_hit": bool((move or {}).get("guaranteed_hit", True)),
            "survival_margin": player_hp - critical,
            "priority": int((move or {}).get("priority", 0) or 0),
            "move_type": (move or {}).get("move_type"),
            "move_category": (move or {}).get("category"),
        }
    else:
        projection = _switch_projection(certificate, action, opponent, forced_switch=bool((certificate.get("boundary") or {}).get("party_switch_required")))
        target = next((item for item in _party(certificate) if item.get("slot") == action.get("slot")), action)
        facts = {
            "safe": projection["safe"], "guaranteed_ko": False,
            "acts_before_threat": projection["acts_before_threat"], "guaranteed_hit": True,
            "survival_margin": projection["survival_margin"], "priority": 0,
            "switch_target_types": _battle_state(target).get("types") or (),
            "switch_target_ability": int(_battle_state(target).get("ability", 0) or 0),
        }
    for key in ("safe", "guaranteed_ko", "acts_before_threat", "guaranteed_hit"):
        if key in selector and bool(selector[key]) != bool(facts[key]):
            return False
    if "survival_margin_gte" in selector and float(facts["survival_margin"]) < float(selector["survival_margin_gte"]):
        return False
    if "priority_gte" in selector and int(facts["priority"]) < int(selector["priority_gte"]):
        return False
    if "move_type_any" in selector and facts.get("move_type") not in _set(selector["move_type_any"]):
        return False
    if "move_category" in selector and facts.get("move_category") != selector["move_category"]:
        return False
    if "switch_target_types_any" in selector and not (_set(selector["switch_target_types_any"]) & _set(facts.get("switch_target_types"))):
        return False
    if "switch_target_abilities_any" in selector and facts.get("switch_target_ability") not in _set(selector["switch_target_abilities_any"]):
        return False
    return True


class BattlePolicy:
    """Choose one legal action using tactical evidence plus declarative rules."""

    def __init__(
        self,
        profile: dict[str, Any],
        *,
        strategies: Iterable[dict[str, Any]] = (),
        history: BattleHistory | None = None,
        activation_mode: str = "legacy",
    ) -> None:
        if activation_mode not in ACTIVATION_MODES:
            raise PolicyError(f"unsupported activation mode: {activation_mode}")
        self.strategies = list(strategies)
        errors = validate_policy_bundle(profile, self.strategies)
        if errors:
            raise PolicyError("; ".join(errors))
        self.source_profile = profile
        self.profile = _normalize_profile(profile)
        self.activation_mode = activation_mode
        self.history = history or BattleHistory()
        behavior_mode = "clone_trial" if activation_mode == "qualified_live" else activation_mode
        self.behavior_hash = policy_bundle_hash(profile, self.strategies, behavior_mode)
        self.last_decision: dict[str, Any] | None = None

    def _strategy_activation(self, strategy: dict[str, Any], certificate: dict[str, Any]) -> str | None:
        sid = strategy.get("id")
        wanted = set(self.profile.get("strategy_ids", []))
        if sid in wanted:
            return "full"
        manifest = self.source_profile.get("strategy_manifest") or {}
        if self.activation_mode in {"clone_trial", "qualified_live"} and sid in set(manifest.get("clone_trial", [])):
            return "full"
        if sid in set(manifest.get("full_live", [])):
            return "full"
        if self.activation_mode == "blind_live" and sid in set(manifest.get("blind_live_soft", [])):
            return "soft"
        if self.source_profile.get("schema_version") == 1 and _automatic_strategy(strategy) and _strategy_applies_to_certificate(
            strategy, certificate, history=self.history, profile=self.profile
        ):
            return "full"
        return None

    def _rules(self, certificate: dict[str, Any]) -> list[dict[str, Any]]:
        rules = list(self.profile.get("constraints", []))
        for strategy in self.strategies:
            activation = self._strategy_activation(strategy, certificate)
            if activation and isinstance(strategy.get("executable"), dict):
                executable = strategy["executable"]
                blocks = executable.get("rules")
                if not isinstance(blocks, list):
                    blocks = [executable]
                rules.extend(
                    {
                        "id": f"strategy:{strategy['id']}/rule:{block.get('id', index)}",
                        "strategy_id": strategy["id"],
                        "soft": activation == "soft",
                        "when": block.get("when", {}),
                        "directives": block.get("directives", []),
                    }
                    for index, block in enumerate(blocks)
                )
        return rules

    def _directive_effects(
        self, certificate: dict[str, Any], *, excluded_strategy_ids: set[str] | None = None,
    ) -> tuple[dict[str, int], dict[str, list[str]], dict[str, int], list[str]]:
        preferences: Counter[str] = Counter()
        reservations: Counter[str] = Counter()
        vetoes: dict[str, list[str]] = {}
        applied: list[str] = []
        for rule in self._rules(certificate):
            if rule.get("strategy_id") in (excluded_strategy_ids or set()):
                continue
            if not _match_when(rule.get("when", {}), profile=self.profile, certificate=certificate, history=self.history):
                continue
            active_directives = [
                directive for directive in rule.get("directives", [])
                if not (rule.get("soft") and directive.get("kind") == "forbid")
            ]
            if not active_directives:
                continue
            rule_applied = False
            for directive in active_directives:
                kind = directive.get("kind")
                if kind == "reserve":
                    protected_role = directive.get("role")
                    exceptions = {int(item) for item in directive.get("for_opponent_species", [])}
                    for action in certificate.get("legal_actions", []):
                        opponent = _compact_mon(certificate, _opponent_slot(action)).get("species")
                        if self._action_role(action, certificate) == protected_role and opponent not in exceptions:
                            reservations[_action_key(action)] += int(directive.get("priority", 1))
                            rule_applied = True
                    continue
                selector = directive.get("action", {})
                for action in certificate.get("legal_actions", []):
                    role = self._action_role(action, certificate)
                    if _action_matches(action, selector, role, certificate):
                        key = _action_key(action)
                        if kind == "prefer":
                            preferences[key] += int(directive.get("priority", 1))
                            rule_applied = True
                        elif kind == "forbid":
                            vetoes.setdefault(key, []).append(rule["id"])
                            rule_applied = True
            if rule_applied:
                applied.append(rule["id"])
        return dict(preferences), vetoes, dict(reservations), applied

    def _action_role(self, action: dict[str, Any], certificate: dict[str, Any]) -> str | None:
        if action.get("kind") == "move":
            return _role_for_mon(self.profile, _compact_mon(certificate, _actor_slot(certificate, action)))
        mon = next((item for item in _party(certificate) if item.get("slot") == action.get("slot")), None)
        return _role_for_mon(self.profile, mon or {"species": action.get("species")})

    def _reserve_score(self, action: dict[str, Any], certificate: dict[str, Any]) -> int:
        opponent_mon = _compact_mon(certificate, _opponent_slot(action))
        opponent = opponent_mon.get("species")
        role = self._action_role(action, certificate)
        if (
            self.source_profile.get("schema_version") == 2
            and (certificate.get("boundary") or {}).get("party_switch_required")
        ):
            if int(opponent_mon.get("hp", opponent_mon.get("current_hp", 0)) or 0) > 0:
                assignment = next((
                    item for item in self.source_profile.get("matchup_assignments", [])
                    if int((item.get("target") or {}).get("species", 0) or 0) == int(opponent or 0)
                ), None)
                if assignment:
                    backups = list(assignment.get("backups", []))
                    return 3 if assignment.get("primary") == role else max(0, 2 - backups.index(role)) if role in backups else 0
            remaining = [
                item for item in self.source_profile.get("matchup_assignments", [])
                if int((item.get("target") or {}).get("species", 0) or 0) not in self.history._defeated_species
            ]
            return sum(
                2 if item.get("primary") == role else 1 if role in item.get("backups", []) else 0
                for item in remaining
            )
        score = 0
        for objective in self.profile.get("reserve_objectives", []):
            if objective.get("role") != role:
                continue
            targets = {int(item) for item in objective.get("for_opponent_species", [])} - self.history._defeated_species
            if not targets:
                continue
            if action.get("kind") == "switch":
                score += (
                    int(objective.get("switch_bonus", True))
                    if opponent in targets else -(len(targets) + 1)
                )
            elif self._action_role(action, certificate) == role and opponent not in targets:
                score -= 1
        return score

    def _candidates(self, certificate: dict[str, Any], preferences: dict[str, int], vetoes: dict[str, list[str]], reservations: dict[str, int] | None = None) -> list[dict[str, Any]]:
        report = certificate.get("report", certificate)
        move_reports = list(report.get("alternatives", []))
        result = []
        forced_switch = bool((certificate.get("boundary") or {}).get("party_switch_required"))
        actionable_switch_exists = any(
            action.get("kind") == "switch" and not _definitely_incapacitated(int(action.get("status", 0) or 0))
            for action in certificate.get("legal_actions", [])
        )
        default_opponent = _compact_mon(certificate, 1)
        safe_return_targets = [
            action for action in certificate.get("legal_actions", [])
            if action.get("kind") == "switch"
            and _switch_projection(certificate, action, default_opponent, forced_switch=False)["safe"]
        ] if (certificate.get("boundary") or {}).get("format", "single") == "single" else []
        for action in certificate.get("legal_actions", []):
            actor = _actor_slot(certificate, action)
            target_slot = _opponent_slot(action)
            opponent = _compact_mon(certificate, target_slot)
            opponent_hp = int(opponent.get("hp", opponent.get("current_hp", 0)) or 0)
            player = _compact_mon(certificate, actor)
            player_hp = int(player.get("hp", player.get("current_hp", 0)) or 0)
            incapacitated = _definitely_incapacitated(int(player.get("status", 0) or 0))
            incoming = (report.get("incoming_by_target") or {}).get(str(target_slot), report.get("incoming") or {})
            incoming_max = float(incoming.get("max_damage_est", 0) or 0)
            incoming_critical_max = float(incoming.get("critical_max_damage_est", _critical_damage_bound(0, incoming_max)) or 0)
            required_hits = 1
            key = _action_key(action)
            matching_moves = [
                item for item in move_reports
                if int(item.get("actor", actor)) == actor
                and int(item.get("slot", -1)) == int(action.get("slot", -1))
                and int(item.get("move_id", -1)) == int(action.get("move_id", -1))
                and (action.get("target") not in (1, 3) or int(item.get("target", target_slot)) == target_slot)
            ]
            move = min(matching_moves, key=lambda item: item.get("damage_range", [0])[0]) if matching_moves else None
            continuation: dict[str, Any]
            if move:
                minimum, estimate = move.get("damage_range", [0, 0])[0], move.get("damage_est", 0)
                guaranteed = bool(move.get("guaranteed_hit", True) and minimum >= opponent_hp > 0)
                fresh_fake_out = (
                    int(action.get("move_id", 0)) == 252
                    and self.history.fresh_entry_for(certificate, action)
                    and move.get("guaranteed_hit", True)
                    and minimum > 0
                    and int(opponent.get("ability", 0) or 0) not in FLINCH_IMMUNE_ABILITIES
                )
                active_survives_next_critical = incoming_critical_max < player_hp
                safe = bool(
                    move.get("ko_before_hit")
                    or active_survives_next_critical
                    or fresh_fake_out
                )
                acts_before_threat = bool(not safe and (move.get("acts_first") or incoming_max < player_hp))
                survival_margin = player_hp - incoming_critical_max
                proof = move.get("evidence", {}).get("kind")
                if fresh_fake_out:
                    proof = "fresh_entry_flinch"
                continuation = {
                    "next_action_reachable": bool(move.get("ko_before_hit") or active_survives_next_critical or fresh_fake_out),
                    "active_survives_next_critical": active_survives_next_critical,
                    "safe_return_targets": [_action_key(item) for item in safe_return_targets] if fresh_fake_out else [],
                }
                if incapacitated:
                    minimum = estimate = 0.0
                    guaranteed = safe = acts_before_threat = False
                    proof = "status_prevents_next_action"
                    continuation["next_action_reachable"] = False
            else:
                projection = _switch_projection(certificate, action, opponent, forced_switch=forced_switch)
                minimum = projection["damage_min"]
                estimate = projection["damage_est"]
                guaranteed = bool(minimum >= opponent_hp > 0)
                acts_before_threat = projection["acts_before_threat"]
                required_hits = projection["required_hits"]
                survival_margin = projection["survival_margin"]
                safe = projection["safe"]
                proof = "switch_damage_model"
                continuation = {
                    "next_action_reachable": safe,
                    "required_incoming_hits": required_hits,
                    "projected_damage_min": minimum,
                    "target_identity": projection["target_identity"],
                }
            preserve = self._reserve_score(action, certificate) - (reservations or {}).get(key, 0)
            if action.get("kind") == "switch" and int(action.get("status", 0) or 0):
                preserve -= 1
            preference = preferences.get(key, 0)
            forbidden_by = [] if action.get("kind") == "switch" and incapacitated else list(vetoes.get(key, []))
            if action.get("kind") == "move" and int(action.get("move_id", 0)) == 252 and not self.history.fresh_entry_for(certificate, action):
                forbidden_by.append("mechanics:fake_out_requires_fresh_entry")
            if (
                action.get("kind") == "move"
                and int(action.get("move_id", 0)) in LOCKED_MULTI_TURN_MOVE_IDS
                and preference <= 0
            ):
                forbidden_by.append("mechanics:unmodeled_locked_move_requires_strategy")
            if (
                move
                and "status_move_effect_unmodeled" in move.get("uncertainties", [])
                and preference <= 0
            ):
                forbidden_by.append("mechanics:unmodeled_status_move_requires_strategy")
            if (
                action.get("kind") == "switch"
                and actionable_switch_exists
                and _definitely_incapacitated(int(action.get("status", 0) or 0))
            ):
                forbidden_by.append("status:incapacitated_switch_target")
            if action.get("kind") == "switch" and not forced_switch and self.history.switch_forbidden_for(certificate, action):
                forbidden_by.append("history:verified_volatile_trap")
            if action.get("kind") == "switch" and not forced_switch and not incapacitated and not safe:
                forbidden_by.append("safety:unsafe_voluntary_switch")
            if (
                action.get("kind") == "switch"
                and not forced_switch
                and self.history.repeats_last_switch(certificate, action)
            ):
                forbidden_by.append("history:immediate_switch_reversal")
            if (
                action.get("kind") == "switch"
                and not forced_switch
                and not incapacitated
                and self.history.consecutive_switches_for(certificate, action) >= 1
            ):
                forbidden_by.append("history:switch_stall_after_one")
            status_escape = action.get("kind") == "switch" and incapacitated
            action_safety_rank = 2 if status_escape and safe else int(safe or status_escape)
            score = [
                action_safety_rank,
                int(not safe and acts_before_threat and minimum > 0),
                preserve if forced_switch else 0,
                round(float(survival_margin if not safe else 0), 4),
                int(guaranteed),
                preserve if not forced_switch else 0,
                preference,
                int(action.get("kind") == "move" and acts_before_threat),
                round(float(minimum), 4),
                round(float(estimate), 4),
                int(action.get("hp", player_hp) or 0),
                -int(action.get("slot", 0)),
            ]
            result.append({
                "action": dict(action),
                "key": key,
                "score": score,
                "safe": safe,
                "survival_margin": round(float(survival_margin), 4),
                "required_hits": required_hits,
                "guaranteed_ko": guaranteed,
                "acts_before_threat": acts_before_threat,
                "progress_before_faint": bool(not safe and acts_before_threat and minimum > 0),
                "damage_min": minimum,
                "damage_est": estimate,
                "evidence": proof,
                "uncertainties": list(move.get("uncertainties", [])) if move else [],
                "continuation": continuation,
                "forbidden_by": forbidden_by,
            })
        allowed = [item for item in result if not item["forbidden_by"]]
        if not any(item["safe"] for item in allowed):
            soft_switch_vetoes = {"history:immediate_switch_reversal", "history:switch_stall_after_one"}
            for item in result:
                vetoes = set(item["forbidden_by"])
                if item["action"].get("kind") == "switch" and item["safe"] and vetoes and vetoes <= soft_switch_vetoes:
                    item["relaxed_vetoes"] = item["forbidden_by"]
                    item["forbidden_by"] = []
        if self.source_profile.get("schema_version") == 2:
            # Generated policy may refine equal tactical lines, but it may not
            # remove an action that strictly dominates every allowed alternative
            # on survival, progress, guaranteed success, and preservation.
            mechanical_prefixes = ("mechanics:", "status:", "history:", "safety:")
            allowed = [item for item in result if not item["forbidden_by"]]
            best_allowed_prefix = max((tuple(item["score"][:6]) for item in allowed), default=None)
            for item in sorted(result, key=lambda value: tuple(value["score"]), reverse=True):
                directive_vetoes = [veto for veto in item["forbidden_by"] if not str(veto).startswith(mechanical_prefixes)]
                mechanical_vetoes = [veto for veto in item["forbidden_by"] if str(veto).startswith(mechanical_prefixes)]
                if directive_vetoes and not mechanical_vetoes and (
                    best_allowed_prefix is None or tuple(item["score"][:6]) > best_allowed_prefix
                ):
                    item["relaxed_vetoes"] = directive_vetoes
                    item["forbidden_by"] = []
                    best_allowed_prefix = tuple(item["score"][:6])
        if not any(not item["forbidden_by"] for item in result):
            raise PolicyError("all legal actions are forbidden by the policy profile")
        return sorted(result, key=lambda item: tuple(item["score"]), reverse=True)

    def decide(self, certificate: dict[str, Any]) -> dict[str, Any]:
        legal = certificate.get("legal_actions", [])
        if not legal:
            raise PolicyError("battle certificate has no legal actions")
        preferences, vetoes, reservations, applied = self._directive_effects(certificate)
        candidates = self._candidates(certificate, preferences, vetoes, reservations)
        allowed = [item for item in candidates if not item["forbidden_by"]]
        selected = allowed[0]
        applied_strategy_ids = sorted({
            str(item).split("strategy:", 1)[1].split("/rule:", 1)[0]
            for item in applied if str(item).startswith("strategy:")
        })
        strategy_effects = []
        for strategy_id in applied_strategy_ids:
            without = self._directive_effects(certificate, excluded_strategy_ids={strategy_id})
            without_candidates = self._candidates(certificate, without[0], without[1], without[2])
            without_selected = next(item for item in without_candidates if not item["forbidden_by"])
            strategy_effects.append({
                "strategy_id": strategy_id,
                "selected_changed": without_selected["action"] != selected["action"],
                "selected_without": without_selected["action"],
            })
        influential_strategy_ids = sorted(
            item["strategy_id"] for item in strategy_effects if item["selected_changed"]
        )
        uncertainties: list[str] = []
        report = certificate.get("report", certificate)
        proof = report.get("proof", {})
        mechanics_gaps = list(proof.get("material_uncertainty") or [])
        other_uncertainties = list(dict.fromkeys([
            *selected.get("uncertainties", []),
            *mechanics_gaps,
        ]))
        if len(candidates) > 1 and candidates[0]["score"] == candidates[1]["score"]:
            uncertainties.append("top legal actions remain tied under available tactical evidence")
        if proof.get("level") in {None, "none"} and len(candidates) > 1:
            uncertainties.append("tactical evidence is incomplete for a multi-action boundary")
        forced = len(legal) == 1 or bool((certificate.get("boundary") or {}).get("party_switch_required") and len(legal) == 1)
        if forced:
            classification = "forced"
        elif selected["guaranteed_ko"] and not uncertainties and not mechanics_gaps:
            classification = "minimax"
        elif not uncertainties and not mechanics_gaps:
            classification = "expected-best"
        else:
            classification = "heuristic"
        decision = {
            "policy_id": self.profile["id"],
            "behavior_hash": self.behavior_hash,
            "action": selected["action"],
            "selected": selected,
            "candidates": candidates,
            "applied_strategy_ids": applied,
            "strategy_effects": strategy_effects,
            "influential_strategy_ids": influential_strategy_ids,
            "vetoes": vetoes,
            "reservations": reservations,
            "uncertainties": {"action_changing": uncertainties, "other": other_uncertainties},
            "mechanics_coverage": {
                "complete": not mechanics_gaps,
                "gaps": mechanics_gaps,
            },
            "classification": classification,
            "history": self.history.snapshot(),
        }
        self.last_decision = decision
        return decision

    def choose(self, certificate: dict[str, Any]) -> dict[str, Any]:
        """Compatibility convenience returning only the selected legal action."""
        return self.decide(certificate)["action"]

    def record_verified(self, certificate: dict[str, Any], action: dict[str, Any], result: dict[str, Any] | None = None) -> None:
        self.history.record(certificate, {"action": action, **(result or {})})


def _action_key(action: dict[str, Any]) -> str:
    return json.dumps(
        {key: action[key] for key in ("kind", "actor", "slot", "move_id", "target", "species") if key in action},
        sort_keys=True,
        separators=(",", ":"),
    )
