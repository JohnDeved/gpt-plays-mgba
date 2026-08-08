"""Compact native capability registry for Run & Bun.

The registry is the single description source for the model-facing CLI and
the optional MCP adapter.  Keep summaries short enough for discovery; callers
can inspect one capability when they need the complete schema.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
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
                "level": mon.get("state", {}).get("level"),
                "hp": mon.get("state", {}).get("current_hp"),
                "max_hp": mon.get("state", {}).get("max_hp"),
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
                "ability": mon.get("state", {}).get("ability"),
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
    return result


def _with_adapter(callback: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
    from client.mgba_rpc import MGBA
    from games.runbun import RunBunAdapter

    with MGBA(timeout=15) as gba:
        return callback(RunBunAdapter(gba))


def _observe(args: dict[str, Any]) -> dict[str, Any]:
    return _with_adapter(lambda adapter: _compact_state(adapter.observe(), include_objects=bool(args.get("include_objects"))))


def _tactical_report(_: dict[str, Any]) -> dict[str, Any]:
    return _with_adapter(
        lambda adapter: adapter.explain_battle_action(
            adapter.observe(),
            damage_memory=adapter._damage_memory,
        )
    )


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


def _use_field_item(args: dict[str, Any]) -> dict[str, Any]:
    item = args.get("item")
    if not isinstance(item, str) or not item:
        raise CapabilityError("VALIDATION_ERROR", "use_field_item requires item")
    targets = {key: args[key] for key in ("target_slot", "target_species", "target_nickname") if key in args}
    if len(targets) != 1:
        raise CapabilityError("VALIDATION_ERROR", "use_field_item requires exactly one target selector")

    def use(adapter: Any) -> dict[str, Any]:
        result = adapter.use_field_item(item, **targets)
        return {
            "item": result.get("item"),
            "target_slot": result.get("target_slot"),
            "target_species": result.get("target_species"),
            "cursor": result.get("cursor"),
            "text": result.get("text"),
            "observation": _compact_state(result.get("state", {})),
        }

    return _with_adapter(use)


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
    if not selectors:
        raise CapabilityError("VALIDATION_ERROR", "seek_npc requires slot, local_id, or graphics_id")

    def seek(adapter: Any) -> dict[str, Any]:
        result = adapter.follow_live_path_to_npc(
            **selectors,
            interact=bool(args.get("interact", False)),
            grass_penalty=int(args.get("grass_penalty", 100)),
            chunk_steps=int(args.get("chunk_steps", 6)),
        )
        return {
            "reason": result.get("reason"),
            "target": result.get("target"),
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
        "game_inventory", "RAM inventory read",
        "Read money and item pockets from SaveBlock1 RAM with compact item IDs and quantities. Use when: deciding whether a medicine, ball, berry, or progression item is available.",
        ("check inventory", "find potion or candy", "read bag"),
        _OBJECT_SCHEMA, {"type": "object"}, "none", "safe", _inventory,
    ),
    Capability(
        "game_use_field_item", "RAM field-item use",
        "Use a verified field item through Bag pocket/item cursors and select the target by live party identity. Use when: applying Endless Candy or another explicitly supported field item outside battle.",
        ("use endless candy", "level a Pokémon", "use field item", "apply item to party"),
        {"type": "object", "properties": {"item": {"type": "string", "enum": ["Endless Candy"]}, "target_slot": {"type": "integer", "minimum": 0, "maximum": 5}, "target_species": {"type": "integer", "minimum": 1}, "target_nickname": {"type": "string"}}, "required": ["item"], "additionalProperties": False},
        {"type": "object"}, "write", "safe", _use_field_item,
        ("Do not use in battle; use the battle menu and tactical report.",),
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
        {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}, "expected_map": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2}, "grass_penalty": {"type": "integer", "minimum": 0, "default": 100}, "chunk_steps": {"type": "integer", "minimum": 1, "default": 6}, "max_replans": {"type": "integer", "minimum": 1, "default": 32}}, "required": ["x", "y"], "additionalProperties": False},
        {"type": "object"}, "write", "safe", _navigate,
        ("Do not use for selecting an NPC by identity; use game_seek_npc.",),
    ),
    Capability(
        "game_seek_npc", "Identity-based NPC seeker",
        "Find and approach a live NPC by slot, local ID, or graphics ID while rereading RAM object positions and replanning around movement. Use when: targeting a trainer, nurse, shopkeeper, or other specific actor.",
        ("find trainer", "seek NPC", "approach nurse", "target object"),
        {"type": "object", "properties": {"slot": {"type": "integer"}, "local_id": {"type": "integer"}, "graphics_id": {"type": "integer"}, "interact": {"type": "boolean", "default": False}, "grass_penalty": {"type": "integer", "minimum": 0, "default": 100}, "chunk_steps": {"type": "integer", "minimum": 1, "default": 6}}, "additionalProperties": False},
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
