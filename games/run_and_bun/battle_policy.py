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
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
STRATEGY_DB = ROOT / ".agents" / "skills" / "develop-runbun-strategies" / "references" / "strategies.json"
POLICY_ENGINE_VERSION = "hybrid-policy-v4"
SUPPORTED_PREDICATES = frozenset({
    "active_role",
    "opponent_species",
    "forced_switch",
    "fresh_entry",
    "player_hp_lte",
    "player_hp_gte",
    "opponent_hp_lte",
    "opponent_hp_gte",
    "player_status_any",
    "role_available",
    "action_count",
})
DIRECTIVES = frozenset({"prefer", "forbid", "reserve"})


class PolicyError(ValueError):
    """A profile cannot safely produce a policy decision."""


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


def _damage_bounds(move_id: int, attacker: dict[str, Any], defender: dict[str, Any]) -> tuple[float, float]:
    try:
        from games.runbun import RunBunAdapter

        return RunBunAdapter._damage_bounds(move_id, _battle_state(attacker), _battle_state(defender))
    except (AttributeError, KeyError, TypeError, ValueError):
        return (0.0, 0.0)


def _profile_behavior(profile: dict[str, Any], strategies: Iterable[dict[str, Any]]) -> dict[str, Any]:
    try:
        from games.runbun import BATTLE_SCORER_VERSION
        from games.run_and_bun.capabilities import BATTLE_EXECUTION_VERSION, BATTLE_OBSERVATION_VERSION
    except ImportError:
        BATTLE_SCORER_VERSION = "runbun-tactical-v1"
        BATTLE_OBSERVATION_VERSION = "battle-cert-v1"
        BATTLE_EXECUTION_VERSION = "battle-step-v1"
    executable_strategies = []
    wanted = set(profile.get("strategy_ids", ()))
    for strategy in strategies:
        if strategy.get("id") in wanted and strategy.get("executable"):
            executable_strategies.append({
                "id": strategy["id"],
                "executable": strategy["executable"],
            })
    return {
        "engine_version": POLICY_ENGINE_VERSION,
        "tactical_scorer_version": BATTLE_SCORER_VERSION,
        "observation_version": BATTLE_OBSERVATION_VERSION,
        "execution_version": BATTLE_EXECUTION_VERSION,
        "profile": {
            "id": profile.get("id"),
            "trainer_key": profile.get("trainer_key"),
            "roles": profile.get("roles", {}),
            "strategy_ids": profile.get("strategy_ids", []),
            "reserve_objectives": profile.get("reserve_objectives", []),
            "constraints": profile.get("constraints", []),
        },
        "strategies": sorted(executable_strategies, key=lambda item: item["id"]),
    }


def policy_bundle_hash(profile: dict[str, Any], strategies: Iterable[dict[str, Any]] = ()) -> str:
    """Hash only behavior-affecting policy data, not prose or evidence."""
    payload = json.dumps(
        _profile_behavior(profile, strategies),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_profile(profile: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if profile.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if not isinstance(profile.get("id"), str) or not profile["id"].strip():
        errors.append("id is required")
    roles = profile.get("roles")
    if not isinstance(roles, dict) or not roles:
        errors.append("roles must be a non-empty object")
        roles = {}
    seen_species: dict[int, str] = {}
    for role, binding in roles.items():
        if not isinstance(role, str) or not role.strip() or not isinstance(binding, dict):
            errors.append(f"invalid role binding: {role}")
            continue
        selectors = [key for key in ("species", "personality") if key in binding]
        if len(selectors) != 1 or not isinstance(binding[selectors[0]], int) or binding[selectors[0]] <= 0:
            errors.append(f"roles.{role} must bind exactly one positive species or personality")
        if "species" in binding:
            species = int(binding["species"])
            if species in seen_species:
                errors.append(f"species {species} is ambiguously bound to {seen_species[species]} and {role}")
            seen_species[species] = role
    errors.extend(_validate_rules(profile.get("constraints", []), "constraints"))
    reserves = profile.get("reserve_objectives", [])
    if not isinstance(reserves, list):
        errors.append("reserve_objectives must be an array")
    else:
        for index, reserve in enumerate(reserves):
            if not isinstance(reserve, dict) or reserve.get("role") not in roles:
                errors.append(f"reserve_objectives[{index}].role must reference a known role")
            if not isinstance(reserve.get("for_opponent_species"), list) or not reserve["for_opponent_species"]:
                errors.append(f"reserve_objectives[{index}].for_opponent_species must be non-empty")
    strategies = profile.get("strategy_ids", [])
    if not isinstance(strategies, list) or not all(isinstance(item, str) and item for item in strategies):
        errors.append("strategy_ids must be an array of non-empty strings")
    return errors


def _validate_rules(rules: Any, name: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(rules, list):
        return [f"{name} must be an array"]
    for index, rule in enumerate(rules):
        prefix = f"{name}[{index}]"
        if not isinstance(rule, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if not isinstance(rule.get("id"), str) or not rule["id"].strip():
            errors.append(f"{prefix}.id is required")
        when = rule.get("when", {})
        if not isinstance(when, dict):
            errors.append(f"{prefix}.when must be an object")
        else:
            unknown = set(when) - SUPPORTED_PREDICATES
            errors.extend(f"{prefix}.when.{key} is unsupported" for key in sorted(unknown))
            count = when.get("action_count")
            if count is not None and (
                not isinstance(count, dict)
                or not isinstance(count.get("move_id"), int)
                or not any(key in count for key in ("equals", "lte", "gte"))
            ):
                errors.append(f"{prefix}.when.action_count requires move_id and equals/lte/gte")
        directives = rule.get("directives", [])
        if not isinstance(directives, list) or not directives:
            errors.append(f"{prefix}.directives must be a non-empty array")
            continue
        for directive in directives:
            if not isinstance(directive, dict) or directive.get("kind") not in DIRECTIVES:
                errors.append(f"{prefix} has unsupported directive")
            elif directive.get("kind") != "reserve" and not isinstance(directive.get("action"), dict):
                errors.append(f"{prefix}.{directive['kind']} requires an action object")
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


class BattleHistory:
    """Small replayable state derived only from verified action records."""

    def __init__(self) -> None:
        self._counts: Counter[tuple[str, int, int]] = Counter()
        self._fresh_entry = True
        self._records: list[dict[str, Any]] = []

    @classmethod
    def from_jsonl(cls, path: str | Path, *, battle_id: str | None = None) -> "BattleHistory":
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
            if record.get("action") and record.get("verified") is True:
                history.record(record.get("certificate") or {}, record)
        return history

    @property
    def fresh_entry(self) -> bool:
        return self._fresh_entry

    def action_count(self, role: str, opponent_species: int, move_id: int, profile: dict[str, Any], certificate: dict[str, Any]) -> int:
        player = _compact_mon(certificate, 0)
        if role != _role_for_mon(profile, player):
            return 0
        return self._counts[(_identity_value(player), int(opponent_species), int(move_id))]

    def record(self, certificate: dict[str, Any], result: dict[str, Any] | dict[str, Any]) -> None:
        action = result.get("action", result)
        if not isinstance(action, dict):
            return
        player = _compact_mon(certificate, 0)
        opponent = _compact_mon(certificate, 1)
        if action.get("kind") == "move" and action.get("move_id") is not None:
            self._counts[(_identity_value(player), int(opponent.get("species", 0)), int(action["move_id"]))] += 1
        self._fresh_entry = action.get("kind") == "switch"
        self._records.append({"action": dict(action), "opponent_species": opponent.get("species")})

    def snapshot(self) -> dict[str, Any]:
        return {
            "fresh_entry": self._fresh_entry,
            "action_counts": [
                {"actor": actor, "opponent_species": opponent, "move_id": move, "count": count}
                for (actor, opponent, move), count in sorted(self._counts.items())
            ],
            "verified_actions": len(self._records),
        }


def _identity_value(mon: dict[str, Any]) -> str:
    kind, value = _identity(mon)
    return f"{kind}:{value}"


def _role_for_mon(profile: dict[str, Any], mon: dict[str, Any]) -> str | None:
    state = mon.get("state", mon)
    for role, binding in profile.get("roles", {}).items():
        key = "personality" if "personality" in binding else "species"
        if state.get(key) == binding.get(key):
            return role
    return None


def _active_role(profile: dict[str, Any], certificate: dict[str, Any]) -> str | None:
    return _role_for_mon(profile, _compact_mon(certificate, 0))


def _match_when(
    when: dict[str, Any],
    *,
    profile: dict[str, Any],
    certificate: dict[str, Any],
    history: BattleHistory,
) -> bool:
    player = _compact_mon(certificate, 0)
    opponent = _compact_mon(certificate, 1)
    player_hp = int(player.get("hp", player.get("current_hp", 0)) or 0)
    opponent_hp = int(opponent.get("hp", opponent.get("current_hp", 0)) or 0)
    active_role = _active_role(profile, certificate)
    if "active_role" in when and active_role not in _list(when["active_role"]):
        return False
    if "opponent_species" in when and not _species_matches(when["opponent_species"], opponent.get("species")):
        return False
    forced = bool((certificate.get("boundary") or {}).get("party_switch_required"))
    if "forced_switch" in when and bool(when["forced_switch"]) != forced:
        return False
    if "fresh_entry" in when and bool(when["fresh_entry"]) != history.fresh_entry:
        return False
    if "player_hp_lte" in when and not player_hp <= int(when["player_hp_lte"]):
        return False
    if "player_hp_gte" in when and not player_hp >= int(when["player_hp_gte"]):
        return False
    if "opponent_hp_lte" in when and not opponent_hp <= int(when["opponent_hp_lte"]):
        return False
    if "opponent_hp_gte" in when and not opponent_hp >= int(when["opponent_hp_gte"]):
        return False
    if "player_status_any" in when and not int(player.get("status", 0) or 0) & int(when["player_status_any"]):
        return False
    if "role_available" in when:
        available = any(
            _role_for_mon(profile, mon) == when["role_available"]
            and int(mon.get("hp", mon.get("current_hp", 0)) or 0) > 0
            for mon in _party(certificate)
        )
        if not available:
            return False
    count = when.get("action_count")
    if count:
        role = count.get("role", active_role)
        observed = history.action_count(role, int(opponent.get("species", 0)), int(count["move_id"]), profile, certificate)
        if "equals" in count and observed != int(count["equals"]):
            return False
        if "lte" in count and observed > int(count["lte"]):
            return False
        if "gte" in count and observed < int(count["gte"]):
            return False
    return True


def _action_matches(action: dict[str, Any], selector: dict[str, Any], role: str | None) -> bool:
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
    return True


class BattlePolicy:
    """Choose one legal action using tactical evidence plus declarative rules."""

    def __init__(
        self,
        profile: dict[str, Any],
        *,
        strategies: Iterable[dict[str, Any]] = (),
        history: BattleHistory | None = None,
    ) -> None:
        errors = validate_profile(profile)
        if errors:
            raise PolicyError("; ".join(errors))
        self.profile = profile
        self.strategies = list(strategies)
        self.history = history or BattleHistory()
        self.behavior_hash = policy_bundle_hash(profile, self.strategies)
        self.last_decision: dict[str, Any] | None = None

    def _rules(self) -> list[dict[str, Any]]:
        rules = list(self.profile.get("constraints", []))
        wanted = set(self.profile.get("strategy_ids", []))
        for strategy in self.strategies:
            if strategy.get("id") in wanted and isinstance(strategy.get("executable"), dict):
                executable = strategy["executable"]
                blocks = executable.get("rules")
                if not isinstance(blocks, list):
                    blocks = [executable]
                rules.extend(
                    {
                        "id": f"strategy:{strategy['id']}",
                        "when": block.get("when", {}),
                        "directives": block.get("directives", []),
                    }
                    for block in blocks
                )
        return rules

    def _directive_effects(self, certificate: dict[str, Any]) -> tuple[dict[str, int], dict[str, list[str]], list[str]]:
        preferences: Counter[str] = Counter()
        vetoes: dict[str, list[str]] = {}
        applied: list[str] = []
        for rule in self._rules():
            if not _match_when(rule.get("when", {}), profile=self.profile, certificate=certificate, history=self.history):
                continue
            applied.append(rule["id"])
            for directive in rule.get("directives", []):
                kind = directive.get("kind")
                if kind == "reserve":
                    continue
                selector = directive.get("action", {})
                for action in certificate.get("legal_actions", []):
                    role = self._action_role(action, certificate)
                    if _action_matches(action, selector, role):
                        key = _action_key(action)
                        if kind == "prefer":
                            preferences[key] += int(directive.get("priority", 1))
                        elif kind == "forbid":
                            vetoes.setdefault(key, []).append(rule["id"])
        return dict(preferences), vetoes, applied

    def _action_role(self, action: dict[str, Any], certificate: dict[str, Any]) -> str | None:
        if action.get("kind") == "move":
            return _active_role(self.profile, certificate)
        mon = next((item for item in _party(certificate) if item.get("slot") == action.get("slot")), None)
        return _role_for_mon(self.profile, mon or {"species": action.get("species")})

    def _reserve_score(self, action: dict[str, Any], certificate: dict[str, Any]) -> int:
        opponent = _compact_mon(certificate, 1).get("species")
        role = self._action_role(action, certificate)
        score = 0
        for objective in self.profile.get("reserve_objectives", []):
            if objective.get("role") != role:
                continue
            targets = {int(item) for item in objective.get("for_opponent_species", [])}
            if action.get("kind") == "switch":
                score += 2 if opponent in targets else -2
            elif _active_role(self.profile, certificate) == role and opponent not in targets:
                score -= 1
        return score

    def _candidates(self, certificate: dict[str, Any], preferences: dict[str, int], vetoes: dict[str, list[str]]) -> list[dict[str, Any]]:
        report = certificate.get("report", certificate)
        move_reports = {
            (int(item.get("slot", -1)), int(item.get("move_id", -1))): item
            for item in report.get("alternatives", [])
        }
        opponent = _compact_mon(certificate, 1)
        opponent_hp = int(opponent.get("hp", opponent.get("current_hp", 0)) or 0)
        incoming_max = float((report.get("incoming") or {}).get("max_damage_est", 0) or 0)
        player_hp = int(_compact_mon(certificate, 0).get("hp", _compact_mon(certificate, 0).get("current_hp", 0)) or 0)
        result = []
        for action in certificate.get("legal_actions", []):
            key = _action_key(action)
            move = move_reports.get((int(action.get("slot", -1)), int(action.get("move_id", -1))))
            if move:
                minimum, estimate = move.get("damage_range", [0, 0])[0], move.get("damage_est", 0)
                guaranteed = bool(minimum >= opponent_hp > 0)
                fresh_fake_out = int(action.get("move_id", 0)) == 252 and self.history.fresh_entry
                safe = bool(move.get("ko_before_hit") or incoming_max < player_hp or fresh_fake_out)
                proof = move.get("evidence", {}).get("kind")
                if fresh_fake_out:
                    proof = "fresh_entry_flinch"
            else:
                target = next((mon for mon in _party(certificate) if mon.get("slot") == action.get("slot")), {})
                target_hp = int(target.get("hp", target.get("current_hp", action.get("hp", 0))) or 0)
                target_state = _battle_state(target)
                opponent_state = _battle_state(opponent)
                target_threat = max(
                    (_damage_bounds(int(move_id), opponent_state, target_state)[1] for move_id in opponent_state.get("moves", ()) if move_id),
                    default=0.0,
                )
                outgoing = [
                    _damage_bounds(int(move_id), target_state, opponent_state)
                    for slot, move_id in enumerate(target_state.get("moves", ()) or ())
                    if move_id and int((target_state.get("pp") or (0, 0, 0, 0))[slot] or 0) > 0
                ]
                minimum = max((bounds[0] for bounds in outgoing), default=0.0)
                estimate = max(((bounds[0] + bounds[1]) / 2 for bounds in outgoing), default=0.0)
                guaranteed = bool(minimum >= opponent_hp > 0)
                safe = target_hp > 0 and not int(target.get("status", 0) or 0) & 0x67 and (
                    target_threat <= 0 or target_threat < target_hp
                )
                proof = "switch_damage_model"
            preserve = self._reserve_score(action, certificate)
            preference = preferences.get(key, 0)
            score = [
                int(safe),
                int(guaranteed),
                preserve,
                preference,
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
                "guaranteed_ko": guaranteed,
                "damage_min": minimum,
                "damage_est": estimate,
                "evidence": proof,
                "forbidden_by": vetoes.get(key, []),
            })
        allowed = [item for item in result if not item["forbidden_by"]]
        if not allowed:
            raise PolicyError("all legal actions are forbidden by the policy profile")
        return sorted(allowed, key=lambda item: tuple(item["score"]), reverse=True)

    def decide(self, certificate: dict[str, Any]) -> dict[str, Any]:
        legal = certificate.get("legal_actions", [])
        if not legal:
            raise PolicyError("battle certificate has no legal actions")
        preferences, vetoes, applied = self._directive_effects(certificate)
        candidates = self._candidates(certificate, preferences, vetoes)
        selected = candidates[0]
        uncertainties: list[str] = []
        if len(candidates) > 1 and candidates[0]["score"] == candidates[1]["score"]:
            uncertainties.append("top legal actions remain tied under available tactical evidence")
        report = certificate.get("report", certificate)
        proof = report.get("proof", {})
        if proof.get("level") in {None, "none"} and len(candidates) > 1:
            uncertainties.append("tactical evidence is incomplete for a multi-action boundary")
        forced = len(legal) == 1 or bool((certificate.get("boundary") or {}).get("party_switch_required") and len(legal) == 1)
        if forced:
            classification = "forced"
        elif selected["guaranteed_ko"] and not uncertainties:
            classification = "minimax"
        elif not uncertainties:
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
            "vetoes": vetoes,
            "uncertainties": {"action_changing": uncertainties, "other": []},
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
        {key: action[key] for key in ("kind", "slot", "move_id", "species") if key in action},
        sort_keys=True,
        separators=(",", ":"),
    )
