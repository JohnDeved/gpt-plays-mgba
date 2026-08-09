"""Compact native capability registry for Run & Bun.

The registry is the single description source for the model-facing CLI and
the optional MCP adapter.  Keep summaries short enough for discovery; callers
can inspect one capability when they need the complete schema.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable


class CapabilityError(RuntimeError):
    """An actionable error returned by a native capability."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        suggested_capability: str | None = None,
        required_user_action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.suggested_capability = suggested_capability
        self.required_user_action = required_user_action

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.suggested_capability:
            result["suggestedCapability"] = self.suggested_capability
        if self.required_user_action:
            result["requiredUserAction"] = self.required_user_action
        return result


# These versions are part of the action-affecting policy bundle. Bump them
# when canonical observation or verified action postconditions change.
BATTLE_OBSERVATION_VERSION = "battle-cert-v2"
BATTLE_EXECUTION_VERSION = "battle-step-v2"


@dataclass(frozen=True)
class Capability:
    name: str
    title: str
    description: str
    use_when: tuple[str, ...]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    side_effect: str
    retry_policy: str
    execute: Callable[[dict[str, Any]], dict[str, Any]]
    do_not_use_when: tuple[str, ...] = ()
    examples: tuple[dict[str, Any], ...] = ()

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "summary": self.description.split(" Use when:", 1)[0],
            "useWhen": list(self.use_when),
            "sideEffect": self.side_effect,
        }

    def inspect(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "description": self.description,
            "doNotUseWhen": list(self.do_not_use_when),
            "examples": list(self.examples),
            "inputSchema": self.input_schema,
            "outputSchema": self.output_schema,
            "retryPolicy": self.retry_policy,
        }

    def mcp_tool(self) -> dict[str, Any]:
        # MCP uses camelCase schema keys; the registry remains Python-native.
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "outputSchema": self.output_schema,
        }


class CapabilityRegistry:
    """Search, inspect, execute, and policy-check native capabilities."""

    def __init__(self, capabilities: list[Capability]) -> None:
        self._capabilities = {item.name: item for item in capabilities}

    def names(self) -> list[str]:
        return sorted(self._capabilities)

    def get(self, name: str) -> Capability:
        try:
            return self._capabilities[name]
        except KeyError as error:
            raise CapabilityError(
                "NOT_FOUND",
                f"unknown capability: {name}",
                suggested_capability="capability_search",
            ) from error

    def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        if not query.strip():
            return [self._capabilities[name].summary() for name in self.names()[:limit]]
        stopwords = {"a", "an", "and", "for", "in", "is", "it", "me", "now", "of", "the", "to", "what"}
        generic_terms = {"battle", "game", "item", "map", "menu", "npc", "read", "route", "state", "walk"}
        terms = set(re.findall(r"[a-z0-9]+", query.lower())) - stopwords
        scored: list[tuple[float, Capability]] = []
        for capability in self._capabilities.values():
            name_text = capability.name.lower().replace("_", " ")
            title_text = capability.title.lower()
            use_when_text = " ".join(capability.use_when).lower()
            name_tokens = set(re.findall(r"[a-z0-9]+", name_text))
            title_tokens = set(re.findall(r"[a-z0-9]+", title_text))
            use_tokens = set(re.findall(r"[a-z0-9]+", use_when_text))
            searchable = " ".join(
                (
                    capability.name,
                    capability.title,
                    capability.description,
                    *capability.use_when,
                    *capability.do_not_use_when,
                )
            ).lower()
            score = 0.0
            for term in terms:
                if term in name_tokens:
                    score += 1.0 if term in generic_terms else 4.0
                elif term in title_tokens:
                    score += 3.0
                elif term in use_tokens:
                    score += 2.0
                elif term in searchable:
                    score += 0.25
            if query.lower().strip() in searchable:
                score += 3.0
            if score:
                scored.append((score, capability))
        scored.sort(key=lambda item: (-item[0], item[1].name))
        return [capability.summary() | {"score": round(score, 2)} for score, capability in scored[:limit]]

    def inspect(self, name: str) -> dict[str, Any]:
        return self.get(name).inspect()

    def execute(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        capability = self.get(name)
        args = arguments or {}
        try:
            return capability.execute(args)
        except CapabilityError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise CapabilityError("VALIDATION_ERROR", str(error)) from error
        except Exception as error:  # bridge errors must remain structured
            raise CapabilityError("TRANSIENT_FAILURE", str(error), retryable=True) from error

    def authorize_fallback(self, intent: str, proposed_tool: str, *, threshold: float = 1.0) -> dict[str, Any]:
        """Reject a generic fallback when a native capability matches intent."""
        matches = self.search(intent, limit=3)
        native = next(
            (match for match in matches if match["name"] != proposed_tool and match.get("score", 0) >= threshold),
            None,
        )
        if native:
            return {
                "allowed": False,
                "reason": "native_capability_available",
                "suggestedCapability": native["name"],
                "matches": matches,
            }
        return {"allowed": True, "reason": "no_matching_native_capability", "matches": matches}


def _compact_state(state: dict[str, Any], *, include_objects: bool = False) -> dict[str, Any]:
    """Bound observation size while retaining decision-relevant RAM facts."""
    result: dict[str, Any] = {
        "frame": state.get("frame"),
        "map": state.get("map"),
        "mode": state.get("mode"),
        "ui": state.get("ui"),
        "party": [
            {
                "slot": mon.get("slot"),
                "species": mon.get("state", {}).get("species"),
                "personality": mon.get("state", {}).get("personality"),
                "ot_id": mon.get("state", {}).get("ot_id"),
                "level": mon.get("state", {}).get("level"),
                "hp": mon.get("state", {}).get("current_hp"),
                "max_hp": mon.get("state", {}).get("max_hp"),
                "moves": mon.get("state", {}).get("moves"),
                "pp": mon.get("state", {}).get("pp"),
                "status": mon.get("state", {}).get("status"),
                "types": mon.get("state", {}).get("types"),
                "attack": mon.get("state", {}).get("attack"),
                "defense": mon.get("state", {}).get("defense"),
                "speed": mon.get("state", {}).get("speed"),
                "special_attack": mon.get("state", {}).get("special_attack"),
                "special_defense": mon.get("state", {}).get("special_defense"),
                "stat_stages": mon.get("state", {}).get("stat_stages"),
                "ability": mon.get("state", {}).get("ability"),
                "held_item": mon.get("state", {}).get("held_item"),
            }
            for mon in state.get("party", {}).get("mons", [])
            if mon.get("present")
        ],
    }
    battle = state.get("battle", {})
    result["battle"] = {
        "active": battle.get("active"),
        "kind": battle.get("kind"),
        "menu": battle.get("menu"),
        "party_switch_required": battle.get("party_switch_required"),
        "mons": [
            {
                "slot": mon.get("slot"),
                "species": mon.get("state", {}).get("species"),
                "level": mon.get("state", {}).get("level"),
                "hp": mon.get("state", {}).get("current_hp"),
                "max_hp": mon.get("state", {}).get("max_hp"),
                "types": mon.get("state", {}).get("types"),
                "moves": mon.get("state", {}).get("moves"),
                "move_names": mon.get("state", {}).get("move_names"),
                "pp": mon.get("state", {}).get("pp"),
                "speed": mon.get("state", {}).get("speed"),
                "attack": mon.get("state", {}).get("attack"),
                "defense": mon.get("state", {}).get("defense"),
                "special_attack": mon.get("state", {}).get("special_attack"),
                "special_defense": mon.get("state", {}).get("special_defense"),
                "stat_stages": mon.get("state", {}).get("stat_stages"),
                "status": mon.get("state", {}).get("status"),
                "ability": mon.get("state", {}).get("ability"),
                "held_item": mon.get("state", {}).get("held_item"),
                "personality": mon.get("state", {}).get("personality"),
            }
            for mon in battle.get("mons", [])
            if mon.get("present")
        ],
    }
    current_text = (state.get("text") or {}).get("current") or {}
    if current_text.get("text"):
        result["text"] = current_text["text"]
    if include_objects:
        result["objects"] = [
            {
                "slot": obj.get("slot"),
                "local_id": obj.get("local_id"),
                "graphics_id": obj.get("graphics_id"),
                "position": obj.get("position"),
                "facing_direction": obj.get("facing_direction"),
                "trainer_type": obj.get("trainer_type"),
            }
            for obj in state.get("objects", [])
            if not obj.get("is_player")
        ]
    # Frame count is telemetry, not decision state. Excluding it makes a hash
    # stable while the emulator waits on the same command prompt, yet still
    # changes when map/battle/UI facts or party resources change. The battle
    # party selector oscillates its field message-box mode while the same
    # RAM-backed selector remains open; that printer implementation detail is
    # not a legal-action change, so omit only those two ephemeral fields.
    canonical_payload = {key: value for key, value in result.items() if key != "frame"}
    hash_ui = dict(canonical_payload.get("ui") or {})
    hash_ui.pop("field_message_box_mode", None)
    hash_ui.pop("field_message_box_mode_name", None)
    canonical_payload["ui"] = hash_ui
    canonical = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    result["state_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return result


def _with_adapter(callback: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
    from client.mgba_rpc import MGBA
    from games.runbun import RunBunAdapter

    with MGBA(timeout=15) as gba:
        return callback(RunBunAdapter(gba))


def _observe(args: dict[str, Any]) -> dict[str, Any]:
    return _with_adapter(lambda adapter: _compact_state(adapter.observe(), include_objects=bool(args.get("include_objects"))))


def _tactical_report(_: dict[str, Any]) -> dict[str, Any]:
    def evaluate(adapter: Any) -> dict[str, Any]:
        observation = adapter.observe()
        report = adapter.explain_battle_action(
            observation,
            damage_memory=adapter._damage_memory,
            type_chart=adapter.rom_data().type_chart(),
            move_data=adapter.battle_move_data(observation),
        )
        report["state_hash"] = _compact_state(observation)["state_hash"]
        return report

    return _with_adapter(evaluate)


def _battle_snapshot(_: dict[str, Any]) -> dict[str, Any]:
    return _with_adapter(lambda adapter: {"snapshot": _compact_state(adapter.observe())})


def _battle_signature(observation: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(
        (
            mon.get("slot"),
            mon.get("state", {}).get("species"),
            mon.get("state", {}).get("current_hp"),
            tuple(mon.get("state", {}).get("pp") or ()),
        )
        for mon in observation.get("battle", {}).get("mons", [])
        if mon.get("present")
    )


def _stable_battle_observation(adapter: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Require the decision-relevant RAM state to agree twice without input."""
    first = adapter.observe()
    second = adapter.observe()
    first_compact = _compact_state(first)
    second_compact = _compact_state(second)
    if first_compact["state_hash"] != second_compact["state_hash"]:
        raise CapabilityError(
            "UNSTABLE_STATE",
            f"battle state changed without input: {first_compact['state_hash']} -> {second_compact['state_hash']}",
            retryable=True,
            suggested_capability="game_battle_snapshot",
        )
    return second, second_compact


def _battle_mon(observation: dict[str, Any], slot: int) -> dict[str, Any] | None:
    return next(
        (
            mon.get("state")
            for mon in observation.get("battle", {}).get("mons", [])
            if mon.get("present") and mon.get("slot") == slot
        ),
        None,
    )


def _legal_battle_actions(observation: dict[str, Any]) -> list[dict[str, Any]]:
    battle = observation.get("battle", {})
    if not battle.get("active") or battle.get("format") == "double":
        return []
    menu = battle.get("menu", {}).get("state")
    active = _battle_mon(observation, 0)
    if active is None:
        return []
    actions: list[dict[str, Any]] = []
    if menu in {"command_menu", "move_menu"}:
        for slot, move_id in enumerate(active.get("moves") or ()):
            pp = (active.get("pp") or (0, 0, 0, 0))[slot]
            if move_id and pp > 0:
                actions.append({"kind": "move", "slot": slot, "move_id": int(move_id), "pp": int(pp)})
    if menu in {"command_menu", "move_menu", "party_switch"}:
        active_personality = active.get("personality")
        for mon in observation.get("party", {}).get("mons", []):
            state = mon.get("state", {})
            if (
                mon.get("present")
                and state.get("current_hp", 0) > 0
                and (
                    state.get("personality") != active_personality
                    if active_personality is not None and state.get("personality") is not None
                    else state.get("species") != active.get("species")
                )
            ):
                actions.append({
                    "kind": "switch",
                    "slot": int(mon["slot"]),
                    "species": int(state["species"]),
                    "hp": int(state["current_hp"]),
                    "status": int(state.get("status", 0)),
                    **({"personality": int(state["personality"])} if state.get("personality") is not None else {}),
                })
    return actions


def _battle_boundary(observation: dict[str, Any]) -> dict[str, Any]:
    battle = observation.get("battle", {})
    menu_state = battle.get("menu", {}).get("state")
    forced = bool(battle.get("party_switch_required") or menu_state == "party_switch")
    return {
        "menu_state": menu_state,
        "party_switch_required": forced,
        "transition_cause": "forced_replacement" if forced else "normal_decision",
    }


def _battle_certificate(adapter: Any, observation: dict[str, Any]) -> dict[str, Any]:
    compact = _compact_state(observation)
    report = adapter.explain_battle_action(
        observation,
        damage_memory=adapter._damage_memory,
        type_chart=adapter.rom_data().type_chart(),
        move_data=adapter.battle_move_data(observation),
    )
    legal = _legal_battle_actions(observation)
    boundary = _battle_boundary(observation)
    decision = report.get("decision") or {}
    recommended = next(
        (
            action
            for action in legal
            if action["kind"] == decision.get("action")
            and action["slot"] == decision.get("slot")
        ),
        None,
    )
    payload = {
        "state_hash": compact["state_hash"],
        "legal_actions": legal,
        "boundary": boundary,
        "decision": decision,
        "proof": report.get("proof"),
    }
    certificate_id = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:20]
    return {
        **report,
        "compact_state": compact,
        "state_hash": compact["state_hash"],
        "legal_actions": legal,
        "boundary": boundary,
        "recommended_action": recommended,
        "certificate_id": certificate_id,
    }


def _battle_evaluate(args: dict[str, Any]) -> dict[str, Any]:
    def evaluate(adapter: Any) -> dict[str, Any]:
        observation, _ = _stable_battle_observation(adapter)
        certificate = _battle_certificate(adapter, observation)
        profile_path = args.get("profile")
        if profile_path:
            from games.run_and_bun.battle_policy import BattlePolicy, load_profile, load_strategies

            policy = BattlePolicy(load_profile(profile_path), strategies=load_strategies())
            certificate["policy_decision"] = policy.decide(certificate)
            certificate["behavior_hash"] = policy.behavior_hash
        return certificate

    return _with_adapter(evaluate)


def _transaction_path() -> Path:
    path = Path(__file__).resolve().parents[2] / "runtime" / "session" / "battle_transactions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _append_transaction(record: dict[str, Any]) -> None:
    with _transaction_path().open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")


def _open_battle_incident(
    *,
    summary: str,
    symptom: str,
    predicted: str,
    actual: str,
    classification: str,
    severity: str,
) -> str | None:
    """Use the repository incident tool; logging failure never hides gameplay evidence."""
    root = Path(__file__).resolve().parents[2]
    path = root / "runtime" / "incidents" / f"auto-battle-{time.time_ns()}.json"
    script = root / ".agents" / "skills" / "resolve-unexpected-tooling-issues" / "scripts" / "incident.py"
    try:
        subprocess.run(
            [
                sys.executable, str(script), "init", str(path),
                "--summary", summary,
                "--component", "certified battle interface",
                "--symptom", symptom,
                "--predicted", predicted,
                "--actual", actual,
                "--classification", classification,
                "--severity", severity,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return str(path)
    except Exception:
        return None


def _pp_deltas(
    before: dict[str, Any],
    after: dict[str, Any],
    slots: tuple[int, ...],
) -> list[dict[str, int]]:
    deltas: list[dict[str, int]] = []
    for battler in slots:
        pre = _battle_mon(before, battler)
        post = _battle_mon(after, battler)
        if not pre or not post or pre.get("species") != post.get("species"):
            continue
        for move_slot, (old, new) in enumerate(zip(pre.get("pp") or (), post.get("pp") or ())):
            if int(old) > int(new):
                deltas.append({
                    "battler": battler,
                    "slot": move_slot,
                    "move_id": int((pre.get("moves") or (0, 0, 0, 0))[move_slot]),
                    "delta": int(old) - int(new),
                })
    return deltas


def _audit_selected_move_pp(
    kind: str | None,
    slot: int,
    deltas: list[dict[str, int]],
    *,
    interrupted_before_execution: bool = False,
) -> list[str]:
    """Require exactly one final PP decrement for the selected allied move."""
    if kind != "move":
        return []
    if interrupted_before_execution and not deltas:
        return []
    selected = [item for item in deltas if item["battler"] == 0 and item["slot"] == slot]
    errors = []
    if len(selected) != 1 or selected[0]["delta"] != 1:
        errors.append(f"selected move PP delta was {selected or 'missing'}, expected exactly one")
    if any(item["battler"] != 0 or item["slot"] != slot for item in deltas):
        errors.append(f"another allied move consumed PP: {deltas}")
    return errors


def _battle_step(
    args: dict[str, Any],
    *,
    adapter: Any | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    expected_hash = args.get("state_hash")
    expected_certificate = args.get("certificate_id")
    action = args.get("action")
    if not isinstance(expected_hash, str) or not isinstance(expected_certificate, str) or not isinstance(action, dict):
        raise CapabilityError("VALIDATION_ERROR", "battle_step requires state_hash, certificate_id, and action")

    def step(adapter: Any) -> dict[str, Any]:
        from games.run_and_bun.state import RunBun

        before, before_compact = _stable_battle_observation(adapter)
        if before_compact["state_hash"] != expected_hash:
            raise CapabilityError(
                "STALE_STATE",
                f"battle state changed: expected {expected_hash}, got {before_compact['state_hash']}",
                retryable=True,
                suggested_capability="game_battle_evaluate",
            )
        if not before.get("battle", {}).get("active"):
            raise CapabilityError("NOT_IN_BATTLE", "battle_step requires an active battle")
        certificate = _battle_certificate(adapter, before)
        if certificate["certificate_id"] != expected_certificate:
            raise CapabilityError(
                "STALE_CERTIFICATE",
                f"battle evidence changed: expected {expected_certificate}, got {certificate['certificate_id']}",
                retryable=True,
                suggested_capability="game_battle_evaluate",
            )
        kind = action.get("kind")
        try:
            slot = int(action.get("slot", -1))
        except (TypeError, ValueError) as error:
            raise CapabilityError("VALIDATION_ERROR", "battle action slot must be an integer") from error
        legal = next(
            (item for item in certificate["legal_actions"] if item["kind"] == kind and item["slot"] == slot),
            None,
        )
        if legal is None:
            raise CapabilityError("ILLEGAL_ACTION", f"action is not legal in certificate {expected_certificate}")

        controller = RunBun(adapter.gba)
        pre_signature = _battle_signature(before)
        if kind == "move":
            controller.open_fight_menu()
            executed = controller.choose_move(slot)
        else:
            if before.get("battle", {}).get("menu", {}).get("state") == "move_menu":
                adapter.gba.press("B", frames=3)
                adapter.gba.wait_frames(30)
                normalized = adapter.observe()
                if normalized.get("battle", {}).get("menu", {}).get("state") != "command_menu":
                    raise RuntimeError("battle move menu did not close before switch")
            executed = controller.switch_pokemon(slot=slot)

        immediate = adapter.observe()
        discrepancies: list[str] = []

        voluntary_switch = kind == "switch" and before.get("battle", {}).get("menu", {}).get("state") == "command_menu"
        # Battler identity changes before the opponent's queued response on a
        # voluntary switch. Starting from the pre-switch signature would
        # accept the stale command prompt immediately; require one transition
        # after the new identity instead. Forced replacements do not consume
        # another opponent action and keep the original signature.
        verification_signature = _battle_signature(immediate) if voluntary_switch else pre_signature
        resolution = adapter.advance_battle_until_menu(
            max_frames=int(args.get("max_frames", 1200)),
            visual_fallback=False,
            after_action=True,
            pre_action_signature=verification_signature,
        )
        after_full = adapter.observe()
        after = _compact_state(after_full)
        final_player_pp = _pp_deltas(before, after_full, (0, 2))
        foe_pp = _pp_deltas(before, after_full, (1, 3))
        pre_player = _battle_mon(before, 0) or {}
        post_player = _battle_mon(after_full, 0) or {}
        fainted_before_execution = bool(
            kind == "move"
            and not final_player_pp
            and post_player.get("species") == pre_player.get("species")
            and post_player.get("current_hp", 0) <= 0
            and len(foe_pp) == 1
            and foe_pp[0]["delta"] == 1
        )
        status_prevented_execution = bool(
            kind == "move"
            and not final_player_pp
            and post_player.get("species") == pre_player.get("species")
            and int(pre_player.get("status", 0)) & 0x67
            and len(foe_pp) == 1
            and foe_pp[0]["delta"] == 1
        )
        prevented_before_execution = fainted_before_execution or status_prevented_execution
        # PP can update after the input acknowledgement. Audit only the stable
        # post-turn boundary; advance_battle_until_menu never selects another
        # move, so one final decrement proves one allied move was committed.
        discrepancies.extend(_audit_selected_move_pp(
            kind,
            slot,
            final_player_pp,
            interrupted_before_execution=prevented_before_execution,
        ))
        locked_rollout = bool(
            kind == "move"
            and legal.get("move_id") == 205
            and len(final_player_pp) == 1
            and final_player_pp[0]["slot"] == slot
            and final_player_pp[0]["delta"] == 1
            and 1 <= sum(item["delta"] for item in foe_pp) <= 5
        )
        if (len(foe_pp) > 1 or any(item["delta"] != 1 for item in foe_pp)) and not locked_rollout:
            discrepancies.append(f"opponent consumed unexpected PP: {foe_pp}")

        actual_enemy_move = foe_pp[0]["move_id"] if len(foe_pp) == 1 and foe_pp[0]["delta"] == 1 else None
        if actual_enemy_move is None:
            feedback = resolution.get("feedback", "").casefold()
            foe = _battle_mon(before, 1) or {}
            named = []
            for move_id in foe.get("moves") or ():
                if not move_id:
                    continue
                name = adapter.rom_data().move_name(int(move_id))
                if name and name.casefold() in feedback:
                    named.append(int(move_id))
            if len(set(named)) == 1:
                actual_enemy_move = named[0]

        terminal = {"command_menu", "move_menu", "party_switch", "battle_end", "not_in_battle"}
        if resolution.get("state") not in terminal:
            discrepancies.append(f"battle step ended at unverified boundary {resolution.get('state')}")
        if after["state_hash"] == expected_hash:
            discrepancies.append("authoritative state did not change")
        verified = not discrepancies
        action_id = hashlib.sha256(
            f"{expected_certificate}:{after['state_hash']}:{time.time_ns()}".encode("ascii")
        ).hexdigest()[:20]
        result = {
            "action_id": action_id,
            "verified": verified,
            "source": os.environ.get("RUNBUN_SESSION_KIND", "live") if persist else "branch_search",
            "certificate_id": expected_certificate,
            "certificate": {
                "state": certificate.get("state"),
                "compact_state": certificate.get("compact_state"),
                "boundary": certificate.get("boundary"),
            },
            "pre_state_hash": expected_hash,
            "post_state_hash": after["state_hash"],
            "action": legal,
            "executed": executed,
            "predicted": {
                "decision": certificate.get("decision"),
                "chosen": certificate.get("chosen"),
                "proof": certificate.get("proof"),
            },
            "policy_decision": args.get("policy_decision"),
            "battle_id": args.get("battle_id"),
            "actual": {
                "allied_pp_deltas": final_player_pp,
                "allied_action_outcome": "prevented_by_status" if status_prevented_execution else "interrupted_before_execution" if fainted_before_execution else "executed",
                "opponent_pp_deltas": foe_pp,
                "enemy_move_id": actual_enemy_move,
                "resolution": {
                    "state": resolution.get("state"),
                    "frames": resolution.get("frames"),
                    "presses": resolution.get("presses"),
                    "feedback": resolution.get("feedback", "")[-1200:],
                },
            },
            "discrepancies": discrepancies,
            "observation": after,
        }
        if persist:
            _append_transaction({"created_at": time.time(), **result})
        if verified and persist:
            from games.run_and_bun.experience import append_battle_transition

            append_battle_transition(
                before,
                after_full,
                legal,
                result["actual"],
                pre_state_hash=expected_hash,
                post_state_hash=after["state_hash"],
            )
        elif discrepancies and persist:
            result["incident"] = _open_battle_incident(
                summary="Certified battle step prediction mismatch",
                symptom="; ".join(discrepancies),
                predicted=f"One legal action reaches a changed stable battle boundary: {legal}",
                actual=json.dumps(result["actual"], sort_keys=True, default=str),
                classification="prediction_model",
                severity="high",
            )
        return result

    return step(adapter) if adapter is not None else _with_adapter(step)


def _branch_score(observation: dict[str, Any], *, outcome: str, depth: int, preserve_species: set[int]) -> list[int]:
    party = [mon.get("state", {}) for mon in observation.get("party", {}).get("mons", []) if mon.get("present")]
    alive = [mon for mon in party if int(mon.get("current_hp", 0)) > 0]
    protected = [mon for mon in alive if int(mon.get("species", 0)) in preserve_species]
    return [
        int(outcome not in {"loss", "error"}),
        int(outcome == "win"),
        len(protected),
        sum(int(mon.get("current_hp", 0)) for mon in protected),
        len(alive),
        sum(int(mon.get("current_hp", 0)) for mon in alive),
        sum(int(bool(mon.get("held_item"))) for mon in alive),
        sum(sum(int(pp) for pp in mon.get("pp", ())) for mon in alive),
        -depth,
    ]


def _branch_terminal(observation: dict[str, Any]) -> str | None:
    party = [mon.get("state", {}) for mon in observation.get("party", {}).get("mons", []) if mon.get("present")]
    if party and not any(int(mon.get("current_hp", 0)) > 0 for mon in party):
        return "loss"
    if not observation.get("battle", {}).get("active"):
        text = json.dumps(observation.get("text") or {}, ensure_ascii=False, default=str).casefold()
        if "whited out" in text or "out of usable pok" in text:
            return "loss"
        if "defeated" in text or "for winning" in text:
            return "win"
    return None


def _branch_action(action: dict[str, Any]) -> dict[str, Any]:
    return {key: action[key] for key in ("kind", "slot", "move_id", "species") if key in action}


def _search_battle_node(
    adapter: Any,
    state_path: Path,
    node_dir: Path,
    *,
    depth: int,
    max_depth: int,
    max_nodes: int,
    preserve_species: set[int],
    counter: dict[str, int],
    cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if counter["nodes"] >= max_nodes:
        return {"outcome": "node_limit", "complete": False, "score": [1, 0, 0, 0, 0, 0, 0, 0, -depth], "line": []}
    counter["nodes"] += 1
    adapter.gba.load_state(state_path)
    observation, compact = _stable_battle_observation(adapter)
    terminal = _branch_terminal(observation)
    if terminal:
        return {
            "outcome": terminal,
            "complete": True,
            "score": _branch_score(observation, outcome=terminal, depth=depth, preserve_species=preserve_species),
            "line": [],
            "terminal_state_hash": compact["state_hash"],
        }
    if depth >= max_depth:
        return {
            "outcome": "depth_limit",
            "complete": False,
            "score": _branch_score(observation, outcome="depth_limit", depth=depth, preserve_species=preserve_species),
            "line": [],
            "terminal_state_hash": compact["state_hash"],
        }

    exact_state_hash = hashlib.sha256(state_path.read_bytes()).hexdigest()
    if exact_state_hash in cache:
        counter["transpositions"] += 1
        return cache[exact_state_hash]
    certificate = _battle_certificate(adapter, observation)
    legal = certificate["legal_actions"]
    if not legal:
        return {
            "outcome": "no_legal_action",
            "complete": False,
            "score": _branch_score(observation, outcome="no_legal_action", depth=depth, preserve_species=preserve_species),
            "line": [],
            "terminal_state_hash": compact["state_hash"],
        }

    candidates: list[dict[str, Any]] = []
    all_complete = True
    for action in legal:
        if counter["nodes"] >= max_nodes:
            all_complete = False
            break
        adapter.gba.load_state(state_path)
        fresh, fresh_compact = _stable_battle_observation(adapter)
        fresh_certificate = _battle_certificate(adapter, fresh)
        selected = next(
            (
                item for item in fresh_certificate["legal_actions"]
                if item["kind"] == action["kind"] and item["slot"] == action["slot"]
            ),
            None,
        )
        if selected is None:
            candidates.append({
                "action": _branch_action(action), "outcome": "error", "complete": False,
                "score": [0, 0, 0, 0, 0, 0, 0, 0, -(depth + 1)], "line": [],
                "error": "legal action disappeared after exact state reload",
            })
            all_complete = False
            continue
        try:
            step = _battle_step(
                {
                    "state_hash": fresh_compact["state_hash"],
                    "certificate_id": fresh_certificate["certificate_id"],
                    "action": selected,
                    "max_frames": 1800,
                },
                adapter=adapter,
                persist=False,
            )
            if not step["verified"]:
                raise RuntimeError("; ".join(step["discrepancies"]))
            child_path = node_dir / f"node-{counter['nodes']:04d}-{depth + 1}.state"
            adapter.gba.save_state(child_path)
            child = _search_battle_node(
                adapter,
                child_path,
                node_dir,
                depth=depth + 1,
                max_depth=max_depth,
                max_nodes=max_nodes,
                preserve_species=preserve_species,
                counter=counter,
                cache=cache,
            )
            candidate = {**child, "action": _branch_action(selected), "line": [_branch_action(selected), *child["line"]]}
        except Exception as error:
            candidate = {
                "action": _branch_action(selected), "outcome": "error", "complete": False,
                "score": [0, 0, 0, 0, 0, 0, 0, 0, -(depth + 1)], "line": [],
                "error": f"{type(error).__name__}: {error}",
            }
        all_complete = all_complete and candidate["complete"]
        candidates.append(candidate)

    best = max(candidates, key=lambda item: tuple(item["score"]))
    result = {
        **best,
        "complete": all_complete,
        "alternatives": [
            {
                "action": item.get("action"),
                "outcome": item["outcome"],
                "score": item["score"],
                "complete": item["complete"],
                **({"error": item["error"]} if item.get("error") else {}),
            }
            for item in candidates
        ],
    }
    cache[exact_state_hash] = result
    return result


def _replay_battle_line(state_path: Path, line: list[dict[str, Any]]) -> dict[str, Any]:
    from client.mgba_clone import disposable_clone
    from games.runbun import RunBunAdapter

    with disposable_clone(state_path) as gba:
        adapter = RunBunAdapter(gba)
        for turn, requested in enumerate(line, 1):
            observation, compact = _stable_battle_observation(adapter)
            terminal = _branch_terminal(observation)
            if terminal:
                return {"passed": terminal == "win", "outcome": terminal, "turns": turn - 1, "state_hash": compact["state_hash"]}
            certificate = _battle_certificate(adapter, observation)
            action = next(
                (
                    item for item in certificate["legal_actions"]
                    if item["kind"] == requested["kind"] and item["slot"] == requested["slot"]
                ),
                None,
            )
            if action is None:
                return {"passed": False, "outcome": "action_missing", "turns": turn - 1, "state_hash": compact["state_hash"]}
            step = _battle_step(
                {"state_hash": compact["state_hash"], "certificate_id": certificate["certificate_id"], "action": action, "max_frames": 1800},
                adapter=adapter,
                persist=False,
            )
            if not step["verified"]:
                return {"passed": False, "outcome": "step_mismatch", "turns": turn, "discrepancies": step["discrepancies"]}
        observation, compact = _stable_battle_observation(adapter)
        outcome = _branch_terminal(observation) or "line_exhausted"
        return {"passed": outcome == "win", "outcome": outcome, "turns": len(line), "state_hash": compact["state_hash"]}


def search_battle_checkpoint(
    state_path: str | Path,
    *,
    max_depth: int = 12,
    max_nodes: int = 128,
    preserve_species: set[int] | None = None,
    replay_runs: int = 2,
) -> dict[str, Any]:
    """Search one exact battle checkpoint only in isolated cartridge clones."""
    from client.mgba_clone import disposable_clone
    from games.runbun import RunBunAdapter

    state = Path(state_path).resolve()
    if not state.is_file():
        raise CapabilityError("NOT_FOUND", f"battle checkpoint does not exist: {state}")
    with tempfile.TemporaryDirectory(prefix="runbun-battle-search-") as directory:
        node_dir = Path(directory)
        counter = {"nodes": 0, "transpositions": 0}
        with disposable_clone(state) as gba:
            adapter = RunBunAdapter(gba)
            root_observation, root_compact = _stable_battle_observation(adapter)
            if not root_observation.get("battle", {}).get("active"):
                raise CapabilityError("NOT_IN_BATTLE", "branch search checkpoint must contain an active battle")
            result = _search_battle_node(
                adapter,
                state,
                node_dir,
                depth=0,
                max_depth=max_depth,
                max_nodes=max_nodes,
                preserve_species=preserve_species or set(),
                counter=counter,
                cache={},
            )
        replays = [
            _replay_battle_line(state, result["line"])
            for _ in range(replay_runs)
        ] if result["outcome"] == "win" else []
    replay_verified = bool(replays) and all(run["passed"] for run in replays)
    return {
        "root_state_hash": root_compact["state_hash"],
        "checkpoint_sha256": hashlib.sha256(state.read_bytes()).hexdigest(),
        "classification": "deterministic_checkpoint_best" if result["complete"] else "heuristic_bounded_search",
        "proof": {
            "claim": "best over explored legal actions for the exact savestate and fixed input timing",
            "complete_legal_tree": result["complete"],
            "two_run_terminal_replay": replay_verified and len(replays) >= 2,
            "material_uncertainty": "Input-delay RNG variants and unmodeled actions are not enumerated.",
        },
        "outcome": result["outcome"],
        "score": result["score"],
        "line": result["line"],
        "alternatives": result.get("alternatives", []),
        "nodes": counter["nodes"],
        "transpositions": counter["transpositions"],
        "limits": {"max_depth": max_depth, "max_nodes": max_nodes},
        "replays": replays,
    }


def _battle_branch_search(args: dict[str, Any]) -> dict[str, Any]:
    raise CapabilityError(
        "SEARCH_DISABLED",
        "exhaustive branch search is disabled until semantic cycle pruning and a strict wall-clock budget are regression-tested; use bounded policy replays in disposable clones",
        suggested_capability="game_battle_evaluate",
    )

    expected_hash = args.get("state_hash")
    if not isinstance(expected_hash, str):
        raise CapabilityError("VALIDATION_ERROR", "battle_branch_search requires state_hash")
    max_depth = int(args.get("max_depth", 12))
    max_nodes = int(args.get("max_nodes", 128))
    replay_runs = int(args.get("replay_runs", 2))
    if not 1 <= max_depth <= 64 or not 1 <= max_nodes <= 2048 or not 0 <= replay_runs <= 4:
        raise CapabilityError("VALIDATION_ERROR", "invalid branch search limit")
    preserve_species = {int(value) for value in args.get("preserve_species", [])}

    from client.mgba_rpc import MGBA
    from games.runbun import RunBunAdapter

    with tempfile.TemporaryDirectory(prefix="runbun-live-root-") as directory:
        root = Path(directory) / "root.state"
        with MGBA(timeout=15) as gba:
            adapter = RunBunAdapter(gba)
            observation, compact = _stable_battle_observation(adapter)
            if compact["state_hash"] != expected_hash:
                raise CapabilityError("STALE_STATE", f"expected {expected_hash}, got {compact['state_hash']}", retryable=True, suggested_capability="game_battle_evaluate")
            if not observation.get("battle", {}).get("active"):
                raise CapabilityError("NOT_IN_BATTLE", "branch search requires an active live battle")
            gba.save_state(root)
        result = search_battle_checkpoint(
            root,
            max_depth=max_depth,
            max_nodes=max_nodes,
            preserve_species=preserve_species,
            replay_runs=replay_runs,
        )
        with MGBA(timeout=15) as gba:
            _, after = _stable_battle_observation(RunBunAdapter(gba))
    if after["state_hash"] != expected_hash:
        incident = _open_battle_incident(
            summary="Battle branch search changed live state",
            symptom=f"live hash changed {expected_hash} -> {after['state_hash']}",
            predicted="All branch inputs execute only in disposable clones.",
            actual="The live canonical hash changed during isolated search.",
            classification="environment",
            severity="catastrophic",
        )
        raise CapabilityError("LIVE_STATE_CHANGED", f"isolated branch search changed live state; incident={incident}")
    result["live_state_preserved"] = True
    return result


def _capture_target(args: dict[str, Any]) -> dict[str, Any]:
    expected_hash = args.get("state_hash")
    target_species = args.get("target_species")
    if not isinstance(expected_hash, str) or target_species is None or "nickname" not in args:
        raise CapabilityError("VALIDATION_ERROR", "capture_target requires state_hash, target_species, and nickname")
    nickname = args.get("nickname")
    if not isinstance(nickname, str) or re.fullmatch(r"[A-Za-z]{1,10}", nickname) is None:
        raise CapabilityError("VALIDATION_ERROR", "nickname must contain 1..10 ASCII letters")
    normalized_nickname = nickname[:1].upper() + nickname[1:].lower()

    def act(adapter: Any) -> dict[str, Any]:
        observation, compact = _stable_battle_observation(adapter)
        if compact["state_hash"] != expected_hash:
            raise CapabilityError("STALE_STATE", f"expected {expected_hash}, got {compact['state_hash']}", retryable=True, suggested_capability="game_observe")
        target = _battle_mon(observation, 1) or {}
        if int(target.get("species", 0)) != int(target_species):
            raise CapabilityError("TARGET_MISMATCH", f"expected species {target_species}, got {target.get('species')}")
        certificate = adapter.capture_decision_certificate(
            observation,
            poke_balls=adapter._poke_ball_quantity(adapter.inventory()),
            type_chart=adapter.rom_data().type_chart(),
            damage_memory=adapter._damage_memory,
            move_data=adapter.battle_move_data(observation),
        )
        decision = certificate["decision"]
        certificate["state_hash"] = compact["state_hash"]
        certificate["nickname"] = normalized_nickname
        certificate["certificate_id"] = hashlib.sha256(
            json.dumps({"state_hash": compact["state_hash"], "target_species": int(target_species), "nickname": normalized_nickname, "decision": decision, "proof": certificate["proof"]}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:20]
        if decision["kind"] == "blocked":
            raise CapabilityError("CAPTURE_BLOCKED", decision["reason"])
        if decision["kind"] == "throw_ball":
            result = adapter.throw_poke_ball_hotkey(
                max_frames=int(args.get("max_frames", 1800)), nickname=normalized_nickname
            )
            return {"certificate": certificate, "result": result, "observation": _compact_state(adapter.observe())}

        battle_certificate = _battle_certificate(adapter, observation)
        action = next(
            (item for item in battle_certificate["legal_actions"] if item["kind"] == "move" and item["slot"] == decision["slot"]),
            None,
        )
        if action is None:
            raise CapabilityError("ILLEGAL_ACTION", "capture weakening action is absent from canonical legal actions")
        result = _battle_step(
            {"state_hash": compact["state_hash"], "certificate_id": battle_certificate["certificate_id"], "action": action, "max_frames": int(args.get("max_frames", 1800))},
            adapter=adapter,
        )
        return {"certificate": certificate, "result": result, "observation": result["observation"]}

    return _with_adapter(act)


def _hunt_wild_species(args: dict[str, Any]) -> dict[str, Any]:
    target_species = args.get("target_species")
    if target_species is None:
        raise CapabilityError("VALIDATION_ERROR", "wild hunt requires target_species")

    def hunt(adapter: Any) -> dict[str, Any]:
        from games.run_and_bun.wild_encounters import lookup_catchable

        state = adapter.observe()
        map_state = state.get("map") or {}
        map_id = (int(map_state["group"]), int(map_state["number"]))
        locations = lookup_catchable(
            adapter.gba, species_id=int(target_species), map_id=map_id,
            methods={"land"}, visited_only=False,
        )
        if not locations:
            raise CapabilityError(
                "TARGET_NOT_CATCHABLE_HERE",
                f"species {target_species} is absent from land encounters on {map_id}",
                suggested_capability="game_wild_encounter_lookup",
            )
        result = adapter.hunt_wild_species(
            int(target_species),
            max_steps=int(args.get("max_steps", 2000)),
            max_encounters=int(args.get("max_encounters", 100)),
        )
        final = result.pop("state")
        return result | {
            "encounter_source": locations,
            "observation": _compact_state(final),
        }

    return _with_adapter(hunt)


def _experience_query(args: dict[str, Any]) -> dict[str, Any]:
    from games.run_and_bun.experience import query_damage

    def integer(name: str) -> int | None:
        value = args.get(name)
        return int(value) if value is not None else None

    return {
        "damage": query_damage(
            attacker_species=integer("attacker_species"),
            move_id=integer("move_id"),
            defender_species=integer("defender_species"),
        )
    }


def _map_snapshot(args: dict[str, Any]) -> dict[str, Any]:
    def read(adapter: Any) -> dict[str, Any]:
        from games.run_and_bun.live_map import read_live_map, read_live_warps

        state = adapter.observe()
        live = read_live_map(adapter.gba)
        result: dict[str, Any] = {
            "map": state.get("map"),
            "dimensions": {
                "buffer": [live.width, live.height],
                "active": [live.active_width, live.active_height],
                "grid_ptr": live.grid_ptr,
                "origin": live.origin,
            },
            "warps": [warp.as_dict() for warp in read_live_warps(adapter.gba)],
        }
        if args.get("include_ascii"):
            result["ascii"] = live.ascii(
                start=(state["map"]["x"], state["map"]["y"]) if state.get("map") else None
            )
        if args.get("include_tiles"):
            result["tiles"] = live.layout(include_tiles=True, include_ascii=False)["tiles"]
        return result

    return _with_adapter(read)


def _map_name(map_id: tuple[int, int]) -> str | None:
    from games.run_and_bun.state import KNOWN_MAPS

    if map_id in KNOWN_MAPS:
        return KNOWN_MAPS[map_id]
    if map_id[0] == 0 and 16 <= map_id[1] <= 49:
        return f"Route{map_id[1] + 85}"
    return None


def _map_transitions(_: dict[str, Any]) -> dict[str, Any]:
    def read(adapter: Any) -> dict[str, Any]:
        result = adapter.live_map_transitions()
        map_state = result.get("map") or {}
        current = (map_state.get("group"), map_state.get("number"))
        connections = []
        for connection in result.get("connections", []):
            destination = tuple(connection["destination"])
            connections.append(
                {
                    **connection,
                    "destination_name": _map_name(destination),
                    "selectable": connection["direction"] in {"north", "south", "west", "east"},
                }
            )
        return {
            "map": map_state,
            "map_name": _map_name(current) if None not in current else None,
            "connections": connections,
            "warps": result.get("warps", []),
            "selection_rule": result.get("selection_rule"),
        }

    return _with_adapter(read)


def _travel_transition(args: dict[str, Any]) -> dict[str, Any]:
    direction = args.get("direction")
    destination = args.get("destination")
    if direction is None and destination is None:
        raise CapabilityError(
            "VALIDATION_ERROR",
            "travel transition requires direction or destination",
        )
    if direction is not None and direction not in {"north", "south", "west", "east"}:
        raise CapabilityError("VALIDATION_ERROR", "direction must be north, south, east, or west")
    if destination is not None:
        if not isinstance(destination, list) or len(destination) != 2:
            raise CapabilityError("VALIDATION_ERROR", "destination must be [map_group, map_number]")

    def travel(adapter: Any) -> dict[str, Any]:
        result = adapter.travel_live_transition(
            direction=direction,
            destination=tuple(destination) if destination is not None else None,
            max_candidates=int(args.get("max_candidates", 24)),
            grass_penalty=int(args.get("grass_penalty", 100)),
            verified_defeated_trainer_local_ids={
                int(value) for value in args.get("defeated_trainer_local_ids", [])
            },
        )
        state = result.get("state", {})
        map_state = state.get("map") or {}
        actual = (map_state.get("group"), map_state.get("number"))
        return {
            "verified": result.get("verified", False),
            "source_map": result.get("source_map"),
            "source_map_name": _map_name(tuple(result["source_map"])) if result.get("source_map") else None,
            "connection": {
                **result.get("connection", {}),
                "destination_name": _map_name(tuple(result["connection"]["destination"]))
                if result.get("connection", {}).get("destination")
                else None,
            },
            "attempts": result.get("attempts", []),
            "actual_map": actual,
            "actual_map_name": _map_name(actual) if None not in actual else None,
            "observation": _compact_state(state),
        }

    return _with_adapter(travel)


def _travel_warp(args: dict[str, Any]) -> dict[str, Any]:
    destination = args.get("destination")
    if not isinstance(destination, list) or len(destination) != 2:
        raise CapabilityError("VALIDATION_ERROR", "travel warp requires destination [map_group, map_number]")

    def travel(adapter: Any) -> dict[str, Any]:
        before = adapter.observe()
        map_state = before.get("map") or {}
        source = (int(map_state["group"]), int(map_state["number"]))
        wanted = tuple(int(value) for value in destination)
        warps = [
            warp for warp in adapter.live_map_transitions().get("warps", [])
            if tuple(warp["destination"]) == wanted
        ]
        if not warps:
            raise CapabilityError(
                "WARP_NOT_FOUND",
                f"destination {wanted} has no loaded-map warp",
                retryable=True,
                suggested_capability="game_map_transitions",
            )
        current = (int(map_state["x"]), int(map_state["y"]))
        warp = _select_destination_warp(warps, current)
        from games.run_and_bun.live_map import read_live_map

        live = read_live_map(adapter.gba)
        warp_position = (int(warp["x"]), int(warp["y"]))
        target_position, activation_direction = _warp_entry_approach(
            live, current, warp_position
        )
        result = adapter.follow_live_path_adaptive(
            target_position,
            expected_map=source,
            grass_penalty=int(args.get("grass_penalty", 100)),
            chunk_steps=int(args.get("chunk_steps", 6)),
            max_replans=int(args.get("max_replans", 32)),
            verified_defeated_trainer_local_ids={
                int(value) for value in args.get("defeated_trainer_local_ids", [])
            },
        )
        state = result["state"]
        actual_state = state.get("map") or {}
        actual = (actual_state.get("group"), actual_state.get("number"))
        if actual == source and not state.get("battle", {}).get("active") and state.get("mode") == "overworld":
            activation_direction = activation_direction or _warp_activation_direction(
                read_live_map(adapter.gba), warp_position
            )
            if activation_direction:
                adapter.gba.sequence([
                    {"keys": [activation_direction], "frames": 12},
                    {"keys": [], "frames": 4},
                ])
                adapter.gba.wait_frames(120)
                state = adapter.observe()
                actual_state = state.get("map") or {}
                actual = (actual_state.get("group"), actual_state.get("number"))
        if actual != wanted:
            code = "WARP_INTERRUPTED_BY_BATTLE" if state.get("battle", {}).get("active") else "WARP_NOT_TAKEN"
            raise CapabilityError(
                code,
                f"warp at {(warp['x'], warp['y'])} ended on {actual}, expected {wanted}",
                retryable=True,
                suggested_capability="game_map_transitions",
            )
        return {
            "verified": True, "source_map": source, "warp": warp,
            "destination": actual, "destination_name": _map_name(actual),
            "activation_direction": activation_direction,
            "replans": result.get("replans"), "observation": _compact_state(state),
        }

    return _with_adapter(travel)


def _select_destination_warp(
    warps: list[dict[str, Any]], current: tuple[int, int]
) -> dict[str, Any]:
    """Choose the nearest equivalent destination tile deterministically."""
    return min(
        warps,
        key=lambda warp: (
            abs(int(warp["x"]) - current[0]) + abs(int(warp["y"]) - current[1]),
            int(warp["y"]), int(warp["x"]), int(warp.get("warp_id", 0)),
        ),
    )


def _warp_activation_direction(live: Any, position: tuple[int, int]) -> str | None:
    """Return the unique direction from a warp event into a blocked exit tile."""
    candidates = []
    for dx, dy, key in ((0, -1, "UP"), (1, 0, "RIGHT"), (0, 1, "DOWN"), (-1, 0, "LEFT")):
        neighbor = (position[0] + dx, position[1] + dy)
        if not (0 <= neighbor[0] < live.active_width and 0 <= neighbor[1] < live.active_height) or not live.walkable(*neighbor):
            candidates.append(key)
    return candidates[0] if len(candidates) == 1 else None


def _warp_entry_approach(
    live: Any,
    current: tuple[int, int],
    warp: tuple[int, int],
) -> tuple[tuple[int, int], str | None]:
    """Return a reachable event tile or adjacent approach plus activation key."""
    if live.walkable(*warp):
        return warp, None
    candidates = []
    for dx, dy, key in ((0, -1, "DOWN"), (1, 0, "LEFT"), (0, 1, "UP"), (-1, 0, "RIGHT")):
        approach = (warp[0] + dx, warp[1] + dy)
        try:
            path = live.path_to(current, approach, allow_nonwalkable_start=True)
        except ValueError:
            continue
        candidates.append((len(path), approach, key))
    if not candidates:
        raise ValueError(f"warp has no reachable activation approach: {warp!r}")
    _, approach, key = min(candidates)
    return approach, key


def _map_transit_options(_: dict[str, Any]) -> dict[str, Any]:
    def read(adapter: Any) -> dict[str, Any]:
        result = adapter.live_transit_options()
        map_state = result.get("map") or {}
        source = tuple(result["source_map"])
        return {
            "map": map_state,
            "map_name": _map_name(source),
            "options": result.get("options", []),
            "selection_rule": result.get("selection_rule"),
        }

    return _with_adapter(read)


def _travel_transit(args: dict[str, Any]) -> dict[str, Any]:
    if "local_id" not in args and "graphics_id" not in args:
        raise CapabilityError(
            "VALIDATION_ERROR",
            "travel transit requires local_id or graphics_id",
        )
    expected = args.get("expected_destination")
    if expected is not None and (not isinstance(expected, list) or len(expected) != 2):
        raise CapabilityError(
            "VALIDATION_ERROR",
            "expected_destination must be [map_group, map_number]",
        )

    def travel(adapter: Any) -> dict[str, Any]:
        result = adapter.travel_live_transit(
            local_id=int(args["local_id"]) if "local_id" in args else None,
            graphics_id=int(args["graphics_id"]) if "graphics_id" in args else None,
            expected_destination=tuple(expected) if expected is not None else None,
            max_pages=int(args.get("max_pages", 32)),
            max_wait_frames=int(args.get("max_wait_frames", 3600)),
        )
        destination = tuple(result["destination"])
        return {
            "verified": result.get("verified"),
            "source_map": result.get("source_map"),
            "selected": result.get("selected"),
            "interaction": result.get("interaction"),
            "dialogue": result.get("dialogue", []),
            "destination": result.get("destination"),
            "destination_name": _map_name(destination),
            "observation": _compact_state(result.get("state", {})),
        }

    return _with_adapter(travel)


def _inventory(_: dict[str, Any]) -> dict[str, Any]:
    def read(adapter: Any) -> dict[str, Any]:
        inventory = adapter.inventory()
        return {
            "money": inventory.get("money"),
            "items": {
                pocket: [
                    {"slot": item["slot"], "id": item["item_id"], "quantity": item["quantity"]}
                    for item in entries
                ]
                for pocket, entries in inventory.get("pockets", {}).items()
                if entries and not pocket.startswith("ui_")
            },
        }

    return _with_adapter(read)


def _progress_snapshot(args: dict[str, Any]) -> dict[str, Any]:
    """Return a stable progression digest plus only explicitly requested raw IDs."""
    flag_ids = [int(value) for value in args.get("flag_ids", ())]
    variable_ids = [int(value) for value in args.get("variable_ids", ())]

    def read(adapter: Any) -> dict[str, Any]:
        progress = adapter.progress()
        set_flags = set(progress["set_flag_ids"])
        variables = {int(key): int(value) for key, value in progress["variables"].items()}
        payload = {
            "set_flag_ids": sorted(set_flags),
            "variables": sorted(variables.items()),
        }
        digest = hashlib.sha256(
            json.dumps(payload, separators=(",", ":")).encode("ascii")
        ).hexdigest()[:20]
        result = {
            "save_block1": progress["save_block1"],
            "progress_hash": digest,
            "flag_count": len(set_flags),
            "variable_count": len(variables),
            "flags": {str(flag_id): flag_id in set_flags for flag_id in flag_ids},
            "variables": {str(variable_id): variables.get(variable_id, 0) for variable_id in variable_ids},
            "key_items": [
                {"id": int(item["item_id"]), "quantity": int(item["quantity"])}
                for item in adapter.inventory().get("pockets", {}).get("key_items", ())
                if item.get("item_id") and item.get("quantity")
            ],
            "observation": _compact_state(adapter.observe()),
        }
        if args.get("include_all"):
            result["set_flag_ids"] = sorted(set_flags)
            result["all_variables"] = {str(key): value for key, value in sorted(variables.items())}
        return result

    return _with_adapter(read)


_NATURES = (
    "Hardy", "Lonely", "Brave", "Adamant", "Naughty",
    "Bold", "Docile", "Relaxed", "Impish", "Lax",
    "Timid", "Hasty", "Serious", "Jolly", "Naive",
    "Modest", "Mild", "Quiet", "Bashful", "Rash",
    "Calm", "Gentle", "Sassy", "Careful", "Quirky",
)


def _storage_snapshot_data(adapter: Any, *, include_empty: bool = False) -> dict[str, Any]:
    _, compact = _stable_battle_observation(adapter)
    storage = adapter.pokemon_storage(include_empty=include_empty)
    rom = adapter.rom_data()
    records = []
    for record in storage["records"]:
        state = record["state"]
        level = (
            rom.species(int(state["species"])).level_from_experience(int(state["experience"]))
            if record["present"] else None
        )
        records.append({
            "box": record["box"],
            "slot": record["slot"],
            "location": {"kind": "pc", "box": record["box"], "slot": record["slot"]},
            "present": record["present"],
            "reference": record["reference"],
            "species": state["species"],
            "nickname": state["nickname"],
            "level": level,
            "level_source": (
                "verified ROM species growth curve and boxed experience"
                if record["present"] else "empty slot"
            ),
            "nature": _NATURES[int(state["personality"]) % 25],
            "ability_slot": int(state["ability_num"]),
            "friendship": int(state["friendship"]),
            "status": None,
            "status_source": "party-only runtime field unavailable while boxed",
            "held_item": state["held_item"],
            "experience": state["experience"],
            "moves": [
                {"id": move_id, "name": rom.move_name(move_id), "pp": state["pp"][slot]}
                for slot, move_id in enumerate(state["moves"])
                if move_id
            ],
            "ivs": state["ivs"],
            "evs": state["evs"],
            "checksum_valid": state["checksum"]["valid"],
        })
    fingerprint = {
        "field_state_hash": compact["state_hash"],
        "current_box": storage["current_box"],
        "records": [
            {
                "reference": record["reference"], "species": record["species"],
                "held_item": record["held_item"], "experience": record["experience"],
                "moves": record["moves"], "checksum_valid": record["checksum_valid"],
            }
            for record in records if record["present"]
        ],
    }
    state_hash = hashlib.sha256(
        json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "state_hash": state_hash,
        "field_state_hash": compact["state_hash"],
        "current_box": storage["current_box"],
        "occupied": storage["occupied"],
        "records": records,
    }


def _storage_snapshot(args: dict[str, Any]) -> dict[str, Any]:
    return _with_adapter(lambda adapter: _storage_snapshot_data(adapter, include_empty=bool(args.get("include_empty"))))


def _pc_transfer(args: dict[str, Any]) -> dict[str, Any]:
    operation = args.get("operation")
    if operation not in {"deposit", "withdraw", "swap"}:
        raise CapabilityError("VALIDATION_ERROR", "pc_transfer operation must be deposit, withdraw, or swap")
    expected_hash = args.get("state_hash")
    if not isinstance(expected_hash, str):
        raise CapabilityError("VALIDATION_ERROR", "pc_transfer requires the fresh game_storage_snapshot state_hash")

    def reference(name: str) -> tuple[int, int] | None:
        value = args.get(name)
        if value is None:
            return None
        if not isinstance(value, dict) or "personality" not in value or "ot_id" not in value:
            raise CapabilityError("VALIDATION_ERROR", f"{name} requires personality and ot_id")
        return int(value["personality"]), int(value["ot_id"])

    party_reference = reference("party_reference")
    box_reference = reference("box_reference")
    if operation in {"deposit", "swap"} and party_reference is None:
        raise CapabilityError("VALIDATION_ERROR", f"{operation} requires party_reference")
    if operation in {"withdraw", "swap"} and box_reference is None:
        raise CapabilityError("VALIDATION_ERROR", f"{operation} requires box_reference")

    def transfer(adapter: Any) -> dict[str, Any]:
        snapshot = _storage_snapshot_data(adapter)
        if snapshot["state_hash"] != expected_hash:
            raise CapabilityError("STALE_STATE", f"storage state changed: expected {expected_hash}, got {snapshot['state_hash']}", retryable=True, suggested_capability="game_storage_snapshot")
        observation = adapter.observe()
        party_slot = None
        if party_reference is not None:
            matches = [
                mon for mon in observation.get("party", {}).get("mons", [])
                if mon.get("present") and (int(mon["state"]["personality"]), int(mon["state"]["ot_id"])) == party_reference
            ]
            if len(matches) != 1:
                raise CapabilityError("STALE_REFERENCE", f"party_reference resolved to {len(matches)} Pokémon", retryable=True, suggested_capability="game_observe")
            party_slot = int(matches[0]["slot"])
        box_record = None
        if box_reference is not None:
            matches = [
                record for record in snapshot["records"]
                if record["present"] and (int(record["reference"]["personality"]), int(record["reference"]["ot_id"])) == box_reference
            ]
            if len(matches) != 1:
                raise CapabilityError("STALE_REFERENCE", f"box_reference resolved to {len(matches)} Pokémon", retryable=True, suggested_capability="game_storage_snapshot")
            box_record = matches[0]
        result = adapter.pc_transfer(
            operation,
            party_slot=party_slot,
            box=int(box_record["box"] if box_record is not None else args.get("destination_box", snapshot["current_box"])),
            box_slot=int(box_record["slot"]) if box_record is not None else None,
        )
        return {key: value for key, value in result.items() if key != "state"} | {
            "observation": _compact_state(result["state"])
        }

    return _with_adapter(transfer)


def _party_reorder(args: dict[str, Any]) -> dict[str, Any]:
    expected_hash = args.get("state_hash")
    order = args.get("personalities")
    if not isinstance(expected_hash, str) or not isinstance(order, list):
        raise CapabilityError("VALIDATION_ERROR", "party_reorder requires state_hash and personalities")

    def reorder(adapter: Any) -> dict[str, Any]:
        _, compact = _stable_battle_observation(adapter)
        if compact["state_hash"] != expected_hash:
            raise CapabilityError("STALE_STATE", f"expected {expected_hash}, got {compact['state_hash']}", retryable=True, suggested_capability="game_observe")
        result = adapter.party_reorder([int(value) for value in order])
        return {key: value for key, value in result.items() if key != "state"} | {"observation": _compact_state(result["state"])}

    return _with_adapter(reorder)


def _heal_party(args: dict[str, Any]) -> dict[str, Any]:
    expected_hash = args.get("state_hash")
    if not isinstance(expected_hash, str):
        raise CapabilityError("VALIDATION_ERROR", "heal_party requires state_hash")

    def heal(adapter: Any) -> dict[str, Any]:
        _, compact = _stable_battle_observation(adapter)
        if compact["state_hash"] != expected_hash:
            raise CapabilityError("STALE_STATE", f"expected {expected_hash}, got {compact['state_hash']}", retryable=True, suggested_capability="game_observe")
        result = adapter.heal_party()
        return {key: value for key, value in result.items() if key != "state"} | {"observation": _compact_state(result["state"])}

    return _with_adapter(heal)


_POKECENTER_SERVICES = [
    {"id": "remember_move", "label": "Remember a move", "cost": {"item": "Heart Scale", "quantity": 1}},
    {"id": "forget_move", "label": "Forget a move", "cost": None},
    {"id": "maximize_iv", "label": "Maximize IVs", "cost": {"item": "Heart Scale", "quantity": 1, "per_stat": True}},
    {"id": "change_nature", "label": "Change nature", "cost": {"item": "Heart Scale", "quantity": 3}, "options": 25},
    {"id": "change_nickname", "label": "Change nickname", "cost": None},
    {"id": "apply_status", "label": "Apply status", "cost": None, "options": ["Burn", "Freeze", "Paralysis", "Poison", "Sleep"]},
]


def _pokecenter_service_catalog(_: dict[str, Any]) -> dict[str, Any]:
    def read(adapter: Any) -> dict[str, Any]:
        from games.run_and_bun.objects import read_live_event_targets

        state = adapter.observe()
        map_state = state.get("map") or {}
        map_id = (int(map_state["group"]), int(map_state["number"]))
        templates = {
            (target.local_id, target.graphics_id): target
            for target in read_live_event_targets(adapter.gba, map_id=map_id)
        }
        actors = [
            obj | ({"script_address": f"0x{templates[(obj['local_id'], obj['graphics_id'])].script_address:08x}"} if (obj["local_id"], obj["graphics_id"]) in templates else {})
            for obj in adapter.live_objects()
            if obj.get("graphics_id") in {28, 70} and obj.get("local_id") in {4, 5, 6}
        ]
        return {
            "rom_script": "0x082a741e",
            "rom_labels": "0x086b3c68",
            "services": list(_POKECENTER_SERVICES),
            "live_candidates": actors,
            "map": state.get("map"),
            "selection_rule": "approach the stable local/graphics identity, verify the six-entry ROM menu, then apply one explicitly planned service",
        }

    return _with_adapter(read)


def _pokecenter_service(args: dict[str, Any]) -> dict[str, Any]:
    service = args.get("service")
    expected_hash = args.get("state_hash")
    if service not in {"change_nickname", "apply_status"} or not isinstance(expected_hash, str) or "target_slot" not in args:
        raise CapabilityError("VALIDATION_ERROR", "service, state_hash, and target_slot are required")
    if service == "apply_status" and "status" not in args:
        raise CapabilityError("VALIDATION_ERROR", "apply_status requires status")
    if service == "change_nickname" and "nickname" not in args:
        raise CapabilityError("VALIDATION_ERROR", "change_nickname requires nickname")

    def apply(adapter: Any) -> dict[str, Any]:
        _, compact = _stable_battle_observation(adapter)
        if compact["state_hash"] != expected_hash:
            raise CapabilityError("STALE_STATE", f"expected {expected_hash}, got {compact['state_hash']}", retryable=True, suggested_capability="game_observe")
        if service == "change_nickname":
            result = adapter.pokecenter_change_nickname(int(args["target_slot"]), str(args["nickname"]))
        else:
            result = adapter.pokecenter_apply_status(int(args["target_slot"]), str(args["status"]))
        return {key: value for key, value in result.items() if key != "state"} | {
            "observation": _compact_state(result["state"])
        }

    return _with_adapter(apply)


def _pokemon_build_options(args: dict[str, Any]) -> dict[str, Any]:
    def read(adapter: Any) -> dict[str, Any]:
        state = adapter.observe()
        rom = adapter.rom_data()
        storage = _storage_snapshot_data(adapter)
        party = []
        selectable: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for mon in state.get("party", {}).get("mons", []):
            if not mon.get("present"):
                continue
            raw = mon["state"]
            reference = {"personality": int(raw["personality"]), "ot_id": int(raw["ot_id"])}
            entry = {
                "location": {"kind": "party", "slot": mon["slot"]},
                "reference": reference, "species": raw["species"], "level": raw["level"],
                "nature": _NATURES[int(raw["personality"]) % 25],
                "ability_slot": int(raw["ability_num"]), "friendship": int(raw["friendship"]),
                "hp": [raw["current_hp"], raw["max_hp"]], "status": raw["status"],
                "held_item": raw["held_item"], "moves": list(raw["moves"]),
                "pp": list(raw["pp"]), "ivs": list(raw["ivs"]),
            }
            party.append(entry)
            selectable.append((entry, raw))
        selectable.extend((entry, None) for entry in storage["records"] if entry["present"])

        inventory = adapter.inventory()
        unique_items = {
            int(item["address"]): item
            for pocket in inventory.get("pockets", {}).values()
            for item in pocket
        }
        heart_scales = sum(
            int(item["quantity"]) for item in unique_items.values()
            if int(item["item_id"]) == 152
        )
        result = {
            "party": party,
            "storage": storage["records"],
            "storage_state_hash": storage["state_hash"],
            "resources": {"heart_scale": heart_scales},
            "services": [
                service | {
                    "available_now": not service.get("cost")
                    or heart_scales >= int(service["cost"]["quantity"])
                }
                for service in _POKECENTER_SERVICES
            ],
        }
        wanted = args.get("reference")
        if wanted is not None:
            identity = (int(wanted["personality"]), int(wanted["ot_id"]))
            matches = [
                (entry, raw) for entry, raw in selectable
                if (entry["reference"]["personality"], entry["reference"]["ot_id"]) == identity
            ]
            if len(matches) != 1:
                raise CapabilityError(
                    "REFERENCE_NOT_FOUND",
                    f"selected reference resolved to {len(matches)} party/PC Pokémon",
                    retryable=True,
                    suggested_capability="game_pokemon_build_options",
                )
            entry, raw = matches[0]
            current_moves = {
                int(move["id"] if isinstance(move, dict) else move)
                for move in (raw["moves"] if raw is not None else entry["moves"])
                if move
            }
            relearn = []
            for learned in rom.level_up_moves(
                int(entry["species"]), through_level=int(entry["level"])
            ):
                if learned.move_id in current_moves or learned.move_id in {item["id"] for item in relearn}:
                    continue
                move = rom.move(learned.move_id)
                relearn.append({
                    "id": move.move_id, "name": move.name, "level": learned.level,
                    "type": move.type_name, "power": move.power,
                    "accuracy": move.accuracy, "category": move.category,
                })
            result["selected"] = entry | {
                "relearn_candidates": relearn,
                "relearn_source": "verified ROM level-up learnset through current level",
                "remember_move_available_now": heart_scales >= 1,
            }
        return result

    return _with_adapter(read)


def _trainer_lookup(args: dict[str, Any]) -> dict[str, Any]:
    required = ("map_group", "map_number", "local_id")
    if any(key not in args for key in required):
        raise CapabilityError(
            "VALIDATION_ERROR",
            "trainer lookup requires map_group, map_number, and local_id",
        )
    from games.run_and_bun.trainer_database import lookup_trainer

    return lookup_trainer(
        map_group=int(args["map_group"]),
        map_number=int(args["map_number"]),
        local_id=int(args["local_id"]),
        graphics_id=int(args["graphics_id"]) if "graphics_id" in args else None,
        script_address=args.get("script_address"),
    )


def _wild_encounter_lookup(args: dict[str, Any]) -> dict[str, Any]:
    def read(adapter: Any) -> dict[str, Any]:
        from games.run_and_bun.rom_data import BattleRomData
        from games.run_and_bun.wild_encounters import lookup_catchable

        map_value = args.get("map")
        locations = lookup_catchable(
            adapter.gba,
            species_id=int(args["species_id"]) if "species_id" in args else None,
            species_name=args.get("species_name"),
            map_id=tuple(map_value) if map_value is not None else None,
            methods=set(args["methods"]) if args.get("methods") else None,
            visited_only=bool(args.get("visited_only", False)),
        )
        rom = BattleRomData(adapter.gba)
        species = {}
        requested_level = int(args["level"]) if "level" in args else None
        for species_id in sorted({item["species_id"] for item in locations}):
            record = rom.species(species_id)
            level = requested_level or max(
                item["max_level"] for item in locations if item["species_id"] == species_id
            )
            profile = {
                "base_stats": dict(zip(
                    ("hp", "attack", "defense", "speed", "sp_attack", "sp_defense"),
                    record.base_stats,
                )),
                "types": list(record.type_names),
                "catch_rate": record.catch_rate,
                "growth_rate": record.growth_rate,
                "abilities": list(record.ability_ids),
                "zero_ev_iv_nature_stat_ranges": {
                    name: list(bounds)
                    for name, bounds in record.zero_ev_stat_ranges(level).items()
                },
            }
            profile["level_up_moves_through_level"] = [
                {
                    "level": learned.level,
                    **({
                        "id": move.move_id, "name": move.name, "type": move.type_name,
                        "power": move.power, "accuracy": move.accuracy,
                        "category": move.category,
                    } if (move := rom.move(learned.move_id)) else {}),
                }
                for learned in rom.level_up_moves(species_id, through_level=level)
            ]
            profile["learnset_level"] = level
            species[str(species_id)] = profile
        return {
            "query": dict(args),
            "location_count": len(locations),
            "species_count": len(species),
            "species": species,
            "locations": locations,
        }

    return _with_adapter(read)


def _use_field_item(args: dict[str, Any]) -> dict[str, Any]:
    item = args.get("item")
    if not isinstance(item, str) or not item:
        raise CapabilityError("VALIDATION_ERROR", "use_field_item requires item")
    target_keys = ("target_slot", "target_species", "target_nickname")
    targets = {key: args[key] for key in target_keys if key in args}
    if len(targets) != 1:
        raise CapabilityError("VALIDATION_ERROR", "use_field_item requires exactly one target selector")

    def use(adapter: Any) -> dict[str, Any]:
        result = adapter.use_field_item(item, **targets)
        # RunBun.use_field_item deliberately returns a small field-state
        # payload whose party is already a list. It is not the full adapter
        # observation shape consumed by _compact_state; passing it through
        # that decoder caused a post-write formatter crash after a valid UI
        # transaction.
        return {
            "item": result.get("item"),
            "target_slot": result.get("target_slot"),
            "target_species": result.get("target_species"),
            "cursor": result.get("cursor"),
            "text": result.get("text"),
            "observation": result.get("state", {}),
        }

    return _with_adapter(use)


def _resolve_move_learning(args: dict[str, Any]) -> dict[str, Any]:
    if "target_species" not in args or "forget_slot" not in args:
        raise CapabilityError("VALIDATION_ERROR", "resolve move learning requires target_species and forget_slot")

    def resolve(adapter: Any) -> dict[str, Any]:
        result = adapter.resolve_field_move_learning(
            target_species=int(args["target_species"]),
            forget_slot=int(args["forget_slot"]),
            expected_move_id=int(args["expected_move_id"]) if "expected_move_id" in args else None,
            max_frames=int(args.get("max_frames", 1800)),
        )
        return {
            "target_species": result.get("target_species"),
            "forgotten_slot": result.get("forgotten_slot"),
            "old_moves": result.get("old_moves"),
            "new_moves": result.get("new_moves"),
            "expected_move_id": result.get("expected_move_id"),
            "observation": _compact_state(result.get("state", {})),
        }

    return _with_adapter(resolve)


def _battle_advance(args: dict[str, Any]) -> dict[str, Any]:
    def advance(adapter: Any) -> dict[str, Any]:
        result = adapter.advance_battle_until_menu(
            max_frames=int(args.get("max_frames", 900)),
            visual_fallback=False,
        )
        return {
            "state": result.get("state"),
            "frames": result.get("frames"),
            "presses": result.get("presses"),
            "feedback": result.get("feedback", "")[-1200:],
            "observation": _compact_state(adapter.observe()),
        }

    return _with_adapter(advance)


def _navigate(args: dict[str, Any]) -> dict[str, Any]:
    if "x" not in args or "y" not in args:
        raise CapabilityError("VALIDATION_ERROR", "navigate requires integer x and y")

    def navigate(adapter: Any) -> dict[str, Any]:
        expected = args.get("expected_map")
        expected_map = tuple(expected) if expected is not None else None
        result = adapter.follow_live_path_adaptive(
            (int(args["x"]), int(args["y"])),
            expected_map=expected_map,
            grass_penalty=int(args.get("grass_penalty", 100)),
            chunk_steps=int(args.get("chunk_steps", 6)),
            max_replans=int(args.get("max_replans", 32)),
            avoid_trainer_sight_lines=bool(args.get("avoid_trainer_sight_lines", True)),
            verified_defeated_trainer_local_ids={
                int(value) for value in args.get("defeated_trainer_local_ids", [])
            },
        )
        return {
            "reason": result.get("reason"),
            "map": result.get("map"),
            "position": result.get("position"),
            "replans": result.get("replans"),
            "actions": len(result.get("actions", [])),
            "observation": _compact_state(result.get("state", {})),
        }

    return _with_adapter(navigate)


def _seek_npc(args: dict[str, Any]) -> dict[str, Any]:
    selectors = {key: args[key] for key in ("slot", "local_id", "graphics_id") if key in args}
    script_value = args.get("script_address")
    if not selectors and script_value is None:
        raise CapabilityError("VALIDATION_ERROR", "seek_npc requires slot, local_id, graphics_id, or script_address")

    def seek(adapter: Any) -> dict[str, Any]:
        resolved_script = None
        if script_value is not None:
            from games.run_and_bun.objects import read_live_event_targets

            resolved_script = int(str(script_value), 0) if isinstance(script_value, str) else int(script_value)
            state = adapter.observe()
            map_state = state.get("map") or {}
            map_id = (int(map_state["group"]), int(map_state["number"]))
            matches = [
                target for target in read_live_event_targets(adapter.gba, map_id=map_id)
                if int(target.script_address) == resolved_script
                and all(int(getattr(target, key)) == int(value) for key, value in selectors.items())
            ]
            if len(matches) != 1:
                raise CapabilityError("NPC_IDENTITY_AMBIGUOUS", f"script {resolved_script:#010x} resolved to {len(matches)} event targets")
            selectors.update({"local_id": matches[0].local_id, "graphics_id": matches[0].graphics_id})
        result = adapter.follow_live_path_to_npc(
            **selectors,
            interact=bool(args.get("interact", False)),
            grass_penalty=int(args.get("grass_penalty", 100)),
            chunk_steps=int(args.get("chunk_steps", 6)),
        )
        return {
            "reason": result.get("reason"),
            "target": (result.get("target") or {}) | ({"script_address": f"0x{resolved_script:08x}"} if resolved_script is not None else {}),
            "preflight": result.get("preflight"),
            "approach": result.get("approach"),
            "replans": result.get("replans"),
            "observation": _compact_state(result.get("state", {})),
        }

    return _with_adapter(seek)


def _checkpoint(args: dict[str, Any]) -> dict[str, Any]:
    path = args.get("path")
    mode = args.get("mode", "save")
    if not path or mode not in {"save", "load"}:
        raise CapabilityError("VALIDATION_ERROR", "checkpoint requires path and mode=save|load")

    def checkpoint(adapter: Any) -> dict[str, Any]:
        if mode == "save":
            adapter.gba.save_state(path)
        else:
            adapter.gba.load_state(path)
            adapter.gba.wait_frames(4)
        return {"mode": mode, "path": path, "observation": _compact_state(adapter.observe())}

    return _with_adapter(checkpoint)


_OBJECT_SCHEMA = {"type": "object", "additionalProperties": False}
_CAPABILITIES = [
    Capability(
        "game_battle_snapshot", "Canonical battle snapshot",
        "Read the compact authoritative battle state and a short state hash. Use when: starting or checking a battle transaction before selecting an action.",
        ("snapshot battle", "canonical battle state", "get battle hash"),
        _OBJECT_SCHEMA, {"type": "object"}, "none", "safe", _battle_snapshot,
        ("Do not use screenshots as the source of battle facts.",),
    ),
    Capability(
        "game_battle_evaluate", "Bounded battle evaluation",
        "Enumerate legal battle actions with bounded damage, turn-order evidence, proof level, and a deterministic decision certificate. Use when: choosing a move or switch before committing an important turn.",
        ("evaluate battle", "choose safest move", "calculate damage bounds", "compare legal actions"),
        {"type": "object", "properties": {"profile": {"type": "string"}}, "additionalProperties": False}, {"type": "object"}, "none", "safe", _battle_evaluate,
    ),
    Capability(
        "game_battle_step", "Certified single battle step",
        "Validate one current decision certificate, execute exactly one legal move or switch, advance to a stable RAM boundary, and audit PP/result deltas. Use when: executing every important single-battle action after game_battle_evaluate.",
        ("execute certified battle move", "commit and verify one battle action", "safe battle step"),
        {"type": "object", "properties": {"state_hash": {"type": "string"}, "certificate_id": {"type": "string"}, "action": {"type": "object"}, "policy_decision": {"type": "object"}, "battle_id": {"type": "string"}, "max_frames": {"type": "integer", "minimum": 1, "default": 1200}}, "required": ["state_hash", "certificate_id", "action"], "additionalProperties": False},
        {"type": "object"}, "write", "never retry without a fresh evaluation", _battle_step,
        ("Do not use for double battles or with a stale certificate.",),
    ),
    Capability(
        "game_battle_branch_search", "Isolated cartridge battle search",
        "Temporarily disabled: exhaustive clone search repeated semantic cycles and had no strict wall-clock budget. Use when: only after cycle-pruning and timeout regressions are complete.",
        ("search hard battle", "test battle lines in clones", "find reproducible winning line"),
        {"type": "object", "properties": {"state_hash": {"type": "string"}, "max_depth": {"type": "integer", "minimum": 1, "maximum": 64, "default": 12}, "max_nodes": {"type": "integer", "minimum": 1, "maximum": 2048, "default": 128}, "preserve_species": {"type": "array", "items": {"type": "integer"}}, "replay_runs": {"type": "integer", "minimum": 0, "maximum": 4, "default": 2}}, "required": ["state_hash"], "additionalProperties": False},
        {"type": "object"}, "write", "never retry after a live-state preservation failure", _battle_branch_search,
        ("Do not invoke while disabled; use a persisted policy with bounded disposable-clone replays.",),
    ),
    Capability(
        "game_hunt_wild_species", "RAM-driven wild target hunt",
        "Validate the target against the current ROM encounter table, oscillate on a reachable grass pair, flee non-targets, and stop at the target command menu. Use when: searching a low-rate wild counter without repeated manual movement calls.",
        ("hunt wild Pokemon", "find target encounter", "search grass for species"),
        {"type": "object", "properties": {"target_species": {"type": "integer", "minimum": 1}, "max_steps": {"type": "integer", "minimum": 1, "default": 2000}, "max_encounters": {"type": "integer", "minimum": 1, "default": 100}}, "required": ["target_species"], "additionalProperties": False},
        {"type": "object"}, "write", "safe; retry only from returned overworld state when not found", _hunt_wild_species,
        ("Do not use to catch the target; stop and use game_capture_target with its fresh state hash.",),
    ),
    Capability(
        "game_capture_target", "Certified wild capture action",
        "Verify the wild species and fresh RAM hash, require a cute/funny species-fitting nickname, choose a crit-safe weakening move when available, otherwise throw with L, apply the name only after a verified catch, and audit the result. Use when: catching an explicitly selected counter Pokémon.",
        ("catch target Pokemon", "weaken wild Pokemon safely", "throw ball with L hotkey"),
        {"type": "object", "properties": {"state_hash": {"type": "string"}, "target_species": {"type": "integer", "minimum": 1}, "nickname": {"type": "string", "description": "Required cute, funny, species-fitting nickname chosen before capture.", "minLength": 1, "maxLength": 10, "pattern": "^[A-Za-z]+$"}, "max_frames": {"type": "integer", "minimum": 1, "default": 1800}}, "required": ["state_hash", "target_species", "nickname"], "additionalProperties": False},
        {"type": "object"}, "write", "retry only from a fresh capture certificate after an escaped ball", _capture_target,
        ("Do not capture without first choosing a cute/funny fitting nickname; do not open the Bag because this interface uses L.",),
    ),
    Capability(
        "game_experience_query", "Relevant battle experience",
        "Retrieve exact-key damage samples as bounded min/max evidence from the append-only local play ledger. Use when: evaluating a known attacker/move/defender interaction or investigating a surprise.",
        ("query battle experience", "retrieve damage samples", "check learned damage"),
        {"type": "object", "properties": {"attacker_species": {"type": "integer"}, "move_id": {"type": "integer"}, "defender_species": {"type": "integer"}}, "additionalProperties": False},
        {"type": "object"}, "none", "safe", _experience_query,
    ),
    Capability(
        "game_observe", "RAM gameplay observation",
        "Read compact map, party, battle, menu, task text, and optional NPC state from live RAM. Use when: asking what is happening in the game, before a decision, after an action, or when an image would otherwise be requested.",
        ("observe game state", "what is happening now", "read battle state", "read map or party", "check battle or menu", "replace screenshot inspection"),
        {"type": "object", "properties": {"include_objects": {"type": "boolean", "default": False}}, "additionalProperties": False},
        {"type": "object"}, "none", "safe", _observe,
        ("Do not use for executing movement or button input; use game_navigate_live or game_battle_advance.",),
    ),
    Capability(
        "game_tactical_report", "Auditable battle decision",
        "Compute a compact tactical choice with legal alternatives, damage estimates, turn order, observed evidence, and uncertainty. Use when: choosing the next battle move, switch, or explaining why a turn is best.",
        ("choose battle move", "explain battle tactic", "prove turn choice", "compare moves"),
        _OBJECT_SCHEMA, {"type": "object"}, "none", "safe", _tactical_report,
        ("Do not use when no battle is active; game_observe is the cheaper check.",),
    ),
    Capability(
        "game_map_snapshot", "Live map and warp graph",
        "Read the loaded collision/elevation/grass grid and warp destinations directly from RAM. Use when: navigating an unknown map, planning a route, avoiding tall grass, or discovering map connections.",
        ("read map layout", "find warps", "plan grass-free route", "understand collision"),
        {"type": "object", "properties": {"include_ascii": {"type": "boolean", "default": False}, "include_tiles": {"type": "boolean", "default": False}}, "additionalProperties": False},
        {"type": "object"}, "none", "safe", _map_snapshot,
        ("Do not use screenshots to infer collision while this capability succeeds.",),
    ),
    Capability(
        "game_map_transitions", "Loaded map transition options",
        "Read direct map-to-map connections, destination IDs/names, edge directions, offsets, and event warps from authoritative RAM. Use when: choosing the next route, checking which adjacent maps are available, or planning backtracking without trial-and-error boundary probing.",
        ("list adjacent routes", "what map exits are available", "show current route", "inspect map connections"),
        _OBJECT_SCHEMA, {"type": "object"}, "none", "safe", _map_transitions,
        ("Do not use screenshots to infer map identity or exits while this capability succeeds.",),
    ),
    Capability(
        "game_travel_transition", "Verified map transition",
        "Walk to a live map edge, select one decoded connection, and verify the destination map ID from SaveBlock RAM. Use when: moving to a selected adjacent route or town after inspecting game_map_transitions.",
        ("go to adjacent route", "select map exit", "travel to Dewford", "cross map connection"),
        {"type": "object", "properties": {"direction": {"type": "string", "enum": ["north", "south", "east", "west"]}, "destination": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2}, "max_candidates": {"type": "integer", "minimum": 1, "default": 24}, "grass_penalty": {"type": "integer", "minimum": 0, "default": 100}, "defeated_trainer_local_ids": {"type": "array", "items": {"type": "integer", "minimum": 0}}}, "additionalProperties": False},
        {"type": "object"}, "write", "retry only after fresh game_map_transitions", _travel_transition,
        ("Do not use without first reading the current map transition options.",),
    ),
    Capability(
        "game_travel_warp", "Verified event warp",
        "Select the nearest loaded event warp for one destination, path to its RAM coordinate, and verify the destination map ID. Use when: entering a cave, building, or other warp listed by game_map_transitions.",
        ("enter cave", "take map warp", "enter building", "travel through door"),
        {"type": "object", "properties": {"destination": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2}, "grass_penalty": {"type": "integer", "minimum": 0, "default": 100}, "chunk_steps": {"type": "integer", "minimum": 1, "default": 6}, "max_replans": {"type": "integer", "minimum": 1, "default": 32}, "defeated_trainer_local_ids": {"type": "array", "items": {"type": "integer", "minimum": 0}}}, "required": ["destination"], "additionalProperties": False},
        {"type": "object"}, "write", "retry only after fresh game_map_transitions", _travel_warp,
        ("Do not use unless game_map_transitions lists at least one matching warp.",),
    ),
    Capability(
        "game_map_transit_options", "Scripted map transit options",
        "Read loaded-map event scripts and identify ferry or voyage NPCs by stable local identity and ROM dialogue. Use when: a destination is reached through an NPC or scripted boat rather than a direct map edge.",
        ("find ferry NPC", "list boat destinations", "inspect scripted transit", "find route voyage"),
        _OBJECT_SCHEMA, {"type": "object"}, "none", "safe", _map_transit_options,
        ("Do not probe water boundaries when a ROM-identified transit actor exists.",),
    ),
    Capability(
        "game_travel_transit", "Verified scripted map transit",
        "Approach a ROM-identified ferry NPC, advance its voyage dialogue, wait through intermediate maps, and verify the stable destination map from RAM. Use when: taking a boat or other scripted route transit.",
        ("take the boat", "sail to Slateport", "use ferry NPC", "travel by scripted route"),
        {"type": "object", "properties": {"local_id": {"type": "integer", "minimum": 0}, "graphics_id": {"type": "integer", "minimum": 0}, "expected_destination": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2}, "max_pages": {"type": "integer", "minimum": 1, "default": 32}, "max_wait_frames": {"type": "integer", "minimum": 1, "default": 3600}}, "additionalProperties": False},
        {"type": "object"}, "write", "retry only after fresh game_map_transit_options", _travel_transit,
        ("Do not use without first identifying the transit NPC from live ROM scripts.",),
    ),
    Capability(
        "game_inventory", "RAM inventory read",
        "Read money and item pockets from SaveBlock1 RAM with compact item IDs and quantities. Use when: deciding whether a medicine, ball, berry, or progression item is available.",
        ("check inventory", "find potion or candy", "read bag"),
        _OBJECT_SCHEMA, {"type": "object"}, "none", "safe", _inventory,
    ),
    Capability(
        "game_progress_snapshot", "Authoritative progression snapshot",
        "Read a stable raw flag/variable digest, selected progression IDs, key items, map, party, and battle state from RAM. Use when: reconciling the run ledger, comparing checkpoints, or verifying a story milestone.",
        ("read game progression", "reconcile goal ledger", "compare checkpoint progress", "verify story flags"),
        {"type": "object", "properties": {"flag_ids": {"type": "array", "items": {"type": "integer", "minimum": 0}}, "variable_ids": {"type": "array", "items": {"type": "integer", "minimum": 0}}, "include_all": {"type": "boolean", "default": False}}, "additionalProperties": False},
        {"type": "object"}, "none", "safe", _progress_snapshot,
        ("Do not assign semantic names to raw IDs without ROM or controlled-diff evidence.",),
    ),
    Capability(
        "game_storage_snapshot", "PC Pokémon roster",
        "Read all 14 PC boxes from the pointed encrypted storage image with a transfer state hash, stable identity/location, nature, ability slot, friendship, held item, moves, IVs, EVs, and checksum validity. Use when: selecting counters or checking which Pokémon are available before team preparation.",
        ("list PC Pokemon", "check boxed counters", "inspect Pokemon storage"),
        {"type": "object", "properties": {"include_empty": {"type": "boolean", "default": False}}, "additionalProperties": False},
        {"type": "object"}, "none", "safe", _storage_snapshot,
    ),
    Capability(
        "game_pc_transfer", "Verified PC team transfer",
        "Deposit, withdraw, or swap one Pokémon through the storage UI using a fresh storage hash and stable personality/OT references, then verify encrypted RAM identity changes. Use when: assembling an explicitly planned counter-team at a Pokémon Center PC.",
        ("withdraw PC Pokemon", "deposit party Pokemon", "swap party with box"),
        {"type": "object", "properties": {"operation": {"type": "string", "enum": ["deposit", "withdraw", "swap"]}, "state_hash": {"type": "string"}, "party_reference": {"type": "object", "properties": {"personality": {"type": "integer"}, "ot_id": {"type": "integer"}}, "required": ["personality", "ot_id"], "additionalProperties": False}, "box_reference": {"type": "object", "properties": {"personality": {"type": "integer"}, "ot_id": {"type": "integer"}}, "required": ["personality", "ot_id"], "additionalProperties": False}, "destination_box": {"type": "integer", "minimum": 0, "maximum": 13}}, "required": ["operation", "state_hash"], "additionalProperties": False},
        {"type": "object"}, "write", "never retry without game_storage_snapshot", _pc_transfer,
        ("Do not use outside a clean overworld Pokémon Center or without an explicit target identity.",),
    ),
    Capability(
        "game_party_reorder", "Verified party order",
        "Reorder the complete field party through the native 2×3 Pokémon menu and verify every swap by encrypted personality identity. Use when: a prepared strategy requires an exact lead and bench order.",
        ("set party order", "choose exact lead", "arrange battle team"),
        {"type": "object", "properties": {"state_hash": {"type": "string"}, "personalities": {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 6}}, "required": ["state_hash", "personalities"], "additionalProperties": False},
        {"type": "object"}, "write", "never retry without a fresh observation", _party_reorder,
        ("Do not identify duplicate species by species ID; use personality identities from game_observe.",),
    ),
    Capability(
        "game_heal_party", "Verified Pokémon Center healing",
        "Interact with the RAM-identified Center nurse and verify full HP, cleared status, and exact ROM-derived PP for every party member. Use when: preparing for a required fight at a Pokémon Center.",
        ("heal team", "restore party before trainer", "recover PP"),
        {"type": "object", "properties": {"state_hash": {"type": "string"}}, "required": ["state_hash"], "additionalProperties": False},
        {"type": "object"}, "write", "never retry without a fresh observation", _heal_party,
        ("Do not use outside a clean overworld Center with one local-id 1, graphics-id 58 nurse.",),
    ),
    Capability(
        "game_pokemon_build_options", "Party and PC build options",
        "Return the current party, every boxed Pokémon, and the mapped tactical service catalog in one compact planning read. Use when: preparing a team for a known hard fight.",
        ("show team building options", "prepare counter team", "compare party and PC"),
        {"type": "object", "properties": {"reference": {"type": "object", "properties": {"personality": {"type": "integer"}, "ot_id": {"type": "integer"}}, "required": ["personality", "ot_id"], "additionalProperties": False}}, "additionalProperties": False},
        {"type": "object"}, "none", "safe", _pokemon_build_options,
    ),
    Capability(
        "game_pokecenter_service_catalog", "Pokécenter tactical services",
        "Map the verified six-function utility NPC menu, exact costs/options, ROM pointers, and live stable NPC candidates. Use when: planning move relearning, move deletion, IV maximization, nature/nickname changes, or status setup.",
        ("list Pokecenter NPC functions", "check move relearn cost", "plan IV nature or status service"),
        _OBJECT_SCHEMA, {"type": "object"}, "none", "safe", _pokecenter_service_catalog,
    ),
    Capability(
        "game_pokecenter_service", "Verified Pokécenter service",
        "Apply one clone-regression-tested utility-NPC operation and verify its party RAM result. Use when: assigning a planned nickname or preparing a status strategy.",
        ("rename Pokemon", "apply status to Pokemon", "prepare status strategy"),
        {"type": "object", "properties": {"service": {"type": "string", "enum": ["change_nickname", "apply_status"]}, "state_hash": {"type": "string"}, "target_slot": {"type": "integer", "minimum": 0, "maximum": 5}, "nickname": {"type": "string", "minLength": 1, "maxLength": 10}, "status": {"type": "string", "enum": ["Burn", "Freeze", "Paralysis", "Poison", "Sleep"]}}, "required": ["service", "state_hash", "target_slot"], "additionalProperties": False},
        {"type": "object"}, "write", "never retry without fresh party state", _pokecenter_service,
        ("Do not use an unmapped service mutation; inspect game_pokecenter_service_catalog instead.",),
    ),
    Capability(
        "game_trainer_lookup", "Known trainer roster",
        "Resolve a trainer's complete known roster, held items, moves, stats, and preparation status from stable overworld map/local identity. Use when: a trainer NPC is visible, before entering its sight line, after observing a new trainer, or when planning a rematch.",
        ("identify trainer roster", "look up trainer by NPC", "prepare for visible trainer", "check rematch team"),
        {"type": "object", "properties": {"map_group": {"type": "integer", "minimum": 0}, "map_number": {"type": "integer", "minimum": 0}, "local_id": {"type": "integer", "minimum": 0}, "graphics_id": {"type": "integer", "minimum": 0}, "script_address": {"type": "string"}}, "required": ["map_group", "map_number", "local_id"], "additionalProperties": False},
        {"type": "object"}, "none", "safe", _trainer_lookup,
        ("Do not key a trainer only by runtime object slot or current coordinates.",),
    ),
    Capability(
        "game_wild_encounter_lookup", "ROM catchable Pokémon index",
        "Search complete Run & Bun ROM encounter tables by species, map, method, or prior visit. Use when: building a counter-team, deciding where to backtrack, or listing catchable Pokémon before a hard fight.",
        ("find where a Pokemon is catchable", "list wild Pokemon on a map", "backtrack for a counter", "search visited encounter tables"),
        {"type": "object", "properties": {"species_id": {"type": "integer", "minimum": 1}, "species_name": {"type": "string"}, "map": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2}, "methods": {"type": "array", "items": {"type": "string", "enum": ["land", "water", "rock_smash", "old_rod", "good_rod", "super_rod"]}}, "visited_only": {"type": "boolean", "default": False}, "level": {"type": "integer", "minimum": 1, "maximum": 100}}, "additionalProperties": False},
        {"type": "object"}, "none", "safe", _wild_encounter_lookup,
        ("Do not use screenshots or external encounter guides while the verified ROM profile succeeds.",),
    ),
    Capability(
        "game_use_field_item", "RAM field-item use",
        "Use a verified field item through Bag pocket/item cursors and select the target by live party identity. Use when: applying Endless Candy outside battle.",
        ("use endless candy", "level a Pokémon", "use field item", "apply item to party"),
        {"type": "object", "properties": {"item": {"type": "string", "enum": ["Endless Candy"]}, "target_slot": {"type": "integer", "minimum": 0, "maximum": 5}, "target_species": {"type": "integer", "minimum": 1}, "target_nickname": {"type": "string"}}, "required": ["item"], "additionalProperties": False},
        {"type": "object"}, "write", "safe", _use_field_item,
        ("Do not use in battle; use the battle menu and tactical report.",),
    ),
    Capability(
        "game_resolve_move_learning", "Verified field move learning",
        "Resolve a pending four-move learn screen by selecting an explicitly planned replacement slot, verify the new move in party RAM, and close the reusable field-item UI. Use when: Endless Candy pauses because a Pokémon wants to learn a move.",
        ("choose move to forget", "resolve move learning", "replace a field move", "finish candy level-up"),
        {"type": "object", "properties": {"target_species": {"type": "integer", "minimum": 1}, "forget_slot": {"type": "integer", "minimum": 0, "maximum": 3}, "expected_move_id": {"type": "integer", "minimum": 1}, "max_frames": {"type": "integer", "minimum": 1, "default": 1800}}, "required": ["target_species", "forget_slot"], "additionalProperties": False},
        {"type": "object"}, "write", "retry only after fresh field move-learning observation", _resolve_move_learning,
        ("Do not choose a forget slot without a strategic move plan.",),
    ),
    Capability(
        "game_battle_advance", "RAM battle text advancement",
        "Advance only battle message boxes until the live command or move menu returns, then return bounded feedback and a fresh RAM observation. Use when: a battle is waiting on text before the next tactical decision.",
        ("advance battle text", "finish battle message", "wait for move menu"),
        {"type": "object", "properties": {"max_frames": {"type": "integer", "minimum": 1, "default": 900}}, "additionalProperties": False},
        {"type": "object"}, "write", "safe", _battle_advance,
        ("Do not use to blindly press through a battle turn; use game_tactical_report first.",),
    ),
    Capability(
        "game_navigate_live", "Adaptive RAM pathfinding",
        "Navigate to a map coordinate using the live collision grid, dynamic object occupancy, short input chunks, replanning, and a high grass penalty. Use when: walking to a coordinate or warp without image steering.",
        ("walk to coordinate", "navigate map", "avoid grass", "route around moving NPC"),
        {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}, "expected_map": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2}, "grass_penalty": {"type": "integer", "minimum": 0, "default": 100}, "chunk_steps": {"type": "integer", "minimum": 1, "default": 6}, "max_replans": {"type": "integer", "minimum": 1, "default": 32}, "avoid_trainer_sight_lines": {"type": "boolean", "default": True}, "defeated_trainer_local_ids": {"type": "array", "items": {"type": "integer", "minimum": 0}}}, "required": ["x", "y"], "additionalProperties": False},
        {"type": "object"}, "write", "safe", _navigate,
        ("Do not use for selecting an NPC by identity; use game_seek_npc.",),
    ),
    Capability(
        "game_seek_npc", "Identity-based NPC seeker",
        "Find and approach an NPC by runtime identity or loaded-map ROM script address while rereading RAM positions and replanning around movement. Use when: targeting a trainer, nurse, utility service, shopkeeper, or other specific actor.",
        ("find trainer", "seek NPC", "approach nurse", "target object"),
        {"type": "object", "properties": {"slot": {"type": "integer"}, "local_id": {"type": "integer"}, "graphics_id": {"type": "integer"}, "script_address": {"oneOf": [{"type": "integer"}, {"type": "string"}]}, "interact": {"type": "boolean", "default": False}, "grass_penalty": {"type": "integer", "minimum": 0, "default": 100}, "chunk_steps": {"type": "integer", "minimum": 1, "default": 6}}, "additionalProperties": False},
        {"type": "object"}, "write", "safe", _seek_npc,
        ("Do not use without an identity selector; use game_map_snapshot to discover objects first.",),
    ),
    Capability(
        "game_checkpoint", "Savestate checkpoint",
        "Save or load an explicit local emulator checkpoint and return a compact RAM observation. Use when: creating a recovery point before a risky battle or restoring a named local checkpoint.",
        ("save checkpoint", "restore savestate", "create recovery point"),
        {"type": "object", "properties": {"path": {"type": "string"}, "mode": {"type": "string", "enum": ["save", "load"], "default": "save"}}, "required": ["path"], "additionalProperties": False},
        {"type": "object"}, "write", "safe", _checkpoint,
    ),
]


def default_registry() -> CapabilityRegistry:
    return CapabilityRegistry(list(_CAPABILITIES))


def json_dumps(value: Any) -> str:
    """Stable compact JSON for CLI/MCP text content."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
