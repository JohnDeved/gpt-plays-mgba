from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace
from pathlib import Path
import time
from unittest.mock import patch

from games.run_and_bun.capabilities import (
    CapabilityError,
    _audit_selected_move_pp,
    _battle_branch_search,
    _battle_boundary,
    _battle_signature,
    _battle_step,
    _branch_score,
    _branch_terminal,
    _compact_state,
    _capture_target,
    _legal_battle_actions,
    _move_announced,
    _opponent_effect_observed,
    _pokecenter_service,
    _pp_deltas,
    _progress_snapshot,
    _rank_team_builds,
    _storage_snapshot_data,
    _select_destination_warp,
    _search_battle_node,
    _status_prevented_execution,
    _warp_activation_direction,
    _warp_entry_approach,
    default_registry,
)
from games.run_and_bun.live_map import LiveMap


class CapabilityRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = default_registry()

    def test_search_prefers_native_ram_observation(self):
        matches = self.registry.search("what is happening in the battle", limit=3)
        self.assertEqual(matches[0]["name"], "game_observe")

    def test_inspect_contains_model_selection_boundaries(self):
        item = self.registry.inspect("game_navigate_live")
        self.assertIn("Use when:", item["description"])
        self.assertTrue(item["doNotUseWhen"])
        self.assertEqual(item["inputSchema"]["additionalProperties"], False)

    def test_field_item_capability_is_identity_targeted(self):
        item = self.registry.inspect("game_use_field_item")
        self.assertIn("target_species", item["inputSchema"]["properties"])
        self.assertIn("Endless Candy", item["inputSchema"]["properties"]["item"]["enum"])
        self.assertIn("Potion", item["inputSchema"]["properties"]["item"]["enum"])

    def test_move_learning_capability_requires_explicit_replacement_slot(self):
        item = self.registry.inspect("game_resolve_move_learning")
        self.assertEqual(item["inputSchema"]["required"], ["target_species", "forget_slot"])
        self.assertIn("strategic move plan", item["doNotUseWhen"][0])

    def test_trainer_lookup_uses_stable_overworld_identity(self):
        item = self.registry.inspect("game_trainer_lookup")
        self.assertEqual(
            item["inputSchema"]["required"],
            ["map_group", "map_number", "local_id"],
        )
        self.assertIn("runtime object slot", item["doNotUseWhen"][0])

    def test_wild_lookup_supports_species_map_and_backtracking_filters(self):
        item = self.registry.inspect("game_wild_encounter_lookup")
        properties = item["inputSchema"]["properties"]
        self.assertIn("species_id", properties)
        self.assertIn("map", properties)
        self.assertIn("visited_only", properties)
        self.assertIn("external encounter guides", item["doNotUseWhen"][0])

    def test_team_preparation_interfaces_are_registered(self):
        for name in (
            "game_storage_snapshot", "game_pc_transfer",
            "game_party_reorder", "game_field_move_options", "game_heal_party", "game_pokemon_build_options",
            "game_pokecenter_service_catalog", "game_pokecenter_service", "game_field_ui_snapshot", "game_close_field_ui", "game_advance_dialogue",
        ):
            self.assertIn(name, self.registry.names())
        self.assertEqual(
            self.registry.inspect("game_pc_transfer")["inputSchema"]["properties"]["operation"]["enum"],
            ["deposit", "withdraw", "swap"],
        )
        transfer = self.registry.inspect("game_pc_transfer")["inputSchema"]
        self.assertEqual(transfer["required"], ["operation", "state_hash"])
        self.assertNotIn("party_slot", transfer["properties"])

        service = self.registry.inspect("game_pokecenter_service")["inputSchema"]
        self.assertEqual(service["properties"]["service"]["enum"], ["change_nickname", "apply_status"])
        self.assertEqual(service["required"], ["service", "state_hash", "target_slot"])
        self.assertIn("nickname", service["properties"])
        self.assertIn("trainer_key", self.registry.inspect("game_pokemon_build_options")["inputSchema"]["properties"])

    def test_trainer_build_plan_ranks_owned_coverage_without_mutating_party(self):
        class Rom:
            @staticmethod
            def type_chart():
                return {10: {10: 0.5, 12: 2.0}, 11: {10: 2.0, 12: 0.5}, 12: {10: 0.5, 11: 2.0}}

            @staticmethod
            def species(species_id):
                return SimpleNamespace(type_ids={1: (10,), 2: (11,), 3: (0,)}[species_id])

            @staticmethod
            def move(move_id):
                type_id = {101: 10, 102: 11, 201: 12, 202: 10}[move_id]
                return SimpleNamespace(move_id=move_id, type_id=type_id, power=60, category="physical")

        selectable = [
            {"location": {"kind": "party", "slot": 0}, "reference": {"personality": 10, "ot_id": 1}, "species": 1, "level": 15, "moves": [101]},
            {"location": {"kind": "pc", "box": 0, "slot": 1}, "reference": {"personality": 20, "ot_id": 1}, "species": 2, "level": 15, "moves": [{"id": 102}]},
            {"location": {"kind": "pc", "box": 0, "slot": 2}, "reference": {"personality": 30, "ot_id": 1}, "species": 3, "level": 20, "moves": [101]},
        ]
        trainer = {
            "key": "map:1:2/local:3", "battle": {"roster_complete": True},
            "roster": [
                {"species_id": 12, "types": [12], "moves": [201]},
                {"species_id": 10, "types": [10], "moves": [202]},
            ],
        }
        result = _rank_team_builds(selectable, trainer, Rom())
        self.assertEqual(result["classification"], "heuristic")
        self.assertEqual(result["covered_enemy_species"], [10, 12])
        self.assertEqual({item["species"] for item in result["recommended_party"]}, {1, 2, 3})
        self.assertEqual(result["profile_skeleton"]["trainer_key"], trainer["key"])
        self.assertEqual(len(result["profile_skeleton"]["roles"]), 3)

    def test_nickname_service_requires_fresh_state_and_routes_to_adapter(self):
        state = {"frame": 1, "map": {}, "mode": "overworld", "ui": {}, "party": {"mons": []}, "battle": {}}
        state_hash = _compact_state(state)["state_hash"]

        class Adapter:
            def observe(self):
                return state

            def pokecenter_change_nickname(self, slot, nickname):
                return {"target_slot": slot, "nickname_after": nickname, "state": state}

        with patch("games.run_and_bun.capabilities._with_adapter", side_effect=lambda callback: callback(Adapter())):
            result = _pokecenter_service({
                "service": "change_nickname", "state_hash": state_hash,
                "target_slot": 5, "nickname": "OhmNomNom",
            })
            with self.assertRaises(CapabilityError):
                _pokecenter_service({
                    "service": "change_nickname", "state_hash": "stale",
                    "target_slot": 5, "nickname": "OhmNomNom",
                })
        self.assertEqual((result["target_slot"], result["nickname_after"]), (5, "OhmNomNom"))

    def test_warp_activation_uses_unique_blocked_neighbor(self):
        words = [3 << 12] * 9
        words[2 + 1 * 3] = 1 << 10
        live = LiveMap(3, 3, 0, tuple(words), origin=0, active_width=3, active_height=3)
        self.assertEqual(_warp_activation_direction(live, (1, 1)), "RIGHT")

    def test_map_transition_accepts_verified_defeated_trainers(self):
        self.assertIn(
            "defeated_trainer_local_ids",
            self.registry.inspect("game_travel_transition")["inputSchema"]["properties"],
        )

    def test_nonwalkable_warp_uses_adjacent_entry_approach(self):
        words = [3 << 12] * 9
        words[1 + 1 * 3] = 1 << 10
        live = LiveMap(3, 3, 0, tuple(words), origin=0, active_width=3, active_height=3)
        self.assertEqual(_warp_entry_approach(live, (1, 2), (1, 1)), ((1, 2), "UP"))

    def test_storage_snapshot_has_stable_reference_build_fields_and_hash(self):
        class Rom:
            @staticmethod
            def move_name(move_id):
                return f"Move{move_id}"

            @staticmethod
            def species(species_id):
                class Species:
                    @staticmethod
                    def level_from_experience(experience):
                        return 5
                return Species()

        class Adapter:
            @staticmethod
            def observe():
                return {"frame": 1, "map": {"group": 9, "number": 11}, "party": {"mons": []}, "battle": {"mons": []}, "ui": {}}

            @staticmethod
            def rom_data():
                return Rom()

            @staticmethod
            def pokemon_storage(include_empty=False):
                state = {
                    "personality": 26, "ot_id": 7, "species": 1, "nickname": "A",
                    "ability_num": 1, "friendship": 90, "held_item": 2, "experience": 99,
                    "moves": [10, 0, 0, 0], "pp": [5, 0, 0, 0], "ivs": [1] * 6,
                    "evs": [0] * 6, "checksum": {"valid": True},
                }
                return {"current_box": 0, "occupied": 1, "records": [{"box": 0, "slot": 3, "present": True, "reference": {"personality": 26, "ot_id": 7, "box": 0, "slot": 3}, "state": state}]}

        first = _storage_snapshot_data(Adapter())
        second = _storage_snapshot_data(Adapter())
        record = first["records"][0]
        self.assertEqual(first["state_hash"], second["state_hash"])
        self.assertEqual(record["location"], {"kind": "pc", "box": 0, "slot": 3})
        self.assertEqual((record["nature"], record["ability_slot"], record["friendship"]), ("Lonely", 1, 90))
        self.assertEqual(record["level"], 5)

    def test_progress_snapshot_is_compact_and_filterable(self):
        class FakeAdapter:
            def observe(self):
                return {"frame": 1, "map": {"group": 0, "number": 25}, "party": {"mons": []}, "battle": {"mons": []}, "ui": {}}

            def progress(self):
                return {"save_block1": 0x02010000, "set_flag_ids": [2, 7, 99], "variables": {3: 12, 8: 4}}

            def inventory(self):
                return {"pockets": {"key_items": [{"item_id": 711, "quantity": 1}]}}

        with patch("games.run_and_bun.capabilities._with_adapter", side_effect=lambda callback: callback(FakeAdapter())):
            result = _progress_snapshot({"flag_ids": [2, 3], "variable_ids": [3, 4]})

        self.assertEqual(result["flags"], {"2": True, "3": False})
        self.assertEqual(result["variables"], {"3": 12, "4": 0})
        self.assertEqual(result["key_items"], [{"id": 711, "quantity": 1}])
        self.assertEqual(result["flag_count"], 3)
        self.assertNotIn("set_flag_ids", result)

    def test_npc_seeker_accepts_rom_script_identity(self):
        schema = self.registry.inspect("game_seek_npc")["inputSchema"]
        self.assertIn("script_address", schema["properties"])

    def test_battle_transaction_capabilities_are_registered(self):
        for name in ("game_battle_snapshot", "game_battle_evaluate", "game_battle_step", "game_battle_branch_search", "game_hunt_wild_species", "game_capture_target"):
            self.assertIn(name, self.registry.names())
        self.assertNotIn("game_battle_commit", self.registry.names())
        step = self.registry.inspect("game_battle_step")
        self.assertEqual(step["inputSchema"]["required"], ["state_hash", "certificate_id", "action"])
        self.assertIn("doubles queue", step["description"])
        self.assertIn("reevaluate", step["doNotUseWhen"][0])
        self.assertIn("state", self.registry.inspect("game_battle_evaluate")["inputSchema"]["properties"])
        self.assertIn("battle_id", self.registry.inspect("game_battle_evaluate")["inputSchema"]["properties"])
        self.assertIn("history_state_hash", self.registry.inspect("game_battle_evaluate")["inputSchema"]["properties"])
        self.assertIn("save_replay_state", self.registry.inspect("game_battle_evaluate")["inputSchema"]["properties"])
        self.assertIn("replay_selected", self.registry.inspect("game_battle_evaluate")["inputSchema"]["properties"])
        capture = self.registry.inspect("game_capture_target")["inputSchema"]
        self.assertIn("nickname", capture["properties"])
        self.assertEqual(capture["properties"]["nickname"]["maxLength"], 10)
        self.assertEqual(capture["required"], ["state_hash", "target_species", "nickname"])
        self.assertNotIn("target_hp_fraction", capture["properties"])

    def test_capture_target_requires_and_passes_nickname_only_to_throw(self):
        state = {
            "frame": 1, "map": {}, "mode": "battle", "ui": {}, "party": {"mons": []},
            "battle": {"active": True, "mons": [
                {"slot": 0, "present": True, "state": {"species": 388}},
                {"slot": 1, "present": True, "state": {"species": 777}},
            ]},
        }
        state_hash = _compact_state(state)["state_hash"]

        class Rom:
            @staticmethod
            def type_chart():
                return {}

        class Adapter:
            _damage_memory = {}

            def observe(self):
                return state

            @staticmethod
            def _poke_ball_quantity(_inventory):
                return 3

            @staticmethod
            def inventory():
                return {}

            @staticmethod
            def rom_data():
                return Rom()

            @staticmethod
            def battle_move_data(_observation):
                return {}

            @staticmethod
            def capture_decision_certificate(*_args, **_kwargs):
                return {"decision": {"kind": "throw_ball"}, "proof": {}}

            @staticmethod
            def throw_poke_ball_hotkey(*, max_frames, nickname):
                return {"outcome": "caught", "max_frames": max_frames, "nickname": nickname}

        with patch("games.run_and_bun.capabilities._with_adapter", side_effect=lambda callback: callback(Adapter())):
            result = _capture_target({
                "state_hash": state_hash, "target_species": 777,
                "nickname": "OhmNomNom", "max_frames": 900,
            })
        self.assertEqual(result["result"]["nickname"], "Ohmnomnom")
        self.assertEqual(result["certificate"]["nickname"], "Ohmnomnom")
        with self.assertRaises(CapabilityError):
            _capture_target({"state_hash": state_hash, "target_species": 777, "nickname": "NO-DASH"})
        with self.assertRaises(CapabilityError):
            _capture_target({"state_hash": state_hash, "target_species": 777})

    def test_legal_battle_actions_are_ram_derived(self):
        state = {
            "battle": {
                "active": True,
                "format": "single",
                "menu": {"state": "command_menu"},
                "mons": [{"slot": 0, "present": True, "state": {"species": 1, "moves": [10, 20, 0, 0], "pp": [2, 0, 0, 0], "current_hp": 8}}],
            },
            "party": {"mons": [
                {"slot": 0, "present": True, "state": {"species": 1, "current_hp": 8}},
                {"slot": 1, "present": True, "state": {"species": 2, "current_hp": 0}},
                {"slot": 2, "present": True, "state": {"species": 3, "current_hp": 9}},
            ]},
        }
        self.assertEqual(
            _legal_battle_actions(state),
            [
                {"kind": "move", "slot": 0, "move_id": 10, "pp": 2},
                {"kind": "switch", "slot": 2, "species": 3, "hp": 9, "status": 0},
            ],
        )

        state["battle"]["menu"]["state"] = "move_menu"
        self.assertIn(
            {"kind": "switch", "slot": 2, "species": 3, "hp": 9, "status": 0},
            _legal_battle_actions(state),
        )

    def test_switch_identity_uses_personality_not_species(self):
        state = {
            "battle": {
                "active": True, "format": "single", "menu": {"state": "party_switch"},
                "mons": [{"slot": 0, "present": True, "state": {"species": 1, "personality": 10, "moves": [], "pp": [], "current_hp": 0}}],
            },
            "party": {"mons": [
                {"slot": 0, "present": True, "state": {"species": 1, "personality": 10, "current_hp": 0}},
                {"slot": 1, "present": True, "state": {"species": 1, "personality": 11, "current_hp": 9}},
            ]},
        }
        self.assertEqual(_legal_battle_actions(state)[0]["slot"], 1)

    def test_double_actions_include_actor_target_and_legal_switches(self):
        state = {
            "battle": {
                "active": True, "format": "double",
                "menu": {"state": "command_menu", "command_battler": 0},
                "mons": [
                    {"slot": 0, "present": True, "state": {"species": 1, "personality": 10, "moves": [10, 20, 0, 0], "pp": [2, 2, 0, 0], "current_hp": 8}},
                    {"slot": 1, "present": True, "state": {"species": 2, "personality": 20, "current_hp": 9}},
                    {"slot": 2, "present": True, "state": {"species": 3, "personality": 30, "current_hp": 8}},
                    {"slot": 3, "present": True, "state": {"species": 4, "personality": 40, "current_hp": 9}},
                ],
            },
            "party": {"mons": [
                {"slot": 0, "present": True, "state": {"species": 1, "personality": 10, "current_hp": 8}},
                {"slot": 1, "present": True, "state": {"species": 3, "personality": 30, "current_hp": 8}},
                {"slot": 2, "present": True, "state": {"species": 5, "personality": 50, "current_hp": 7}},
            ]},
        }
        moves = {
            10: SimpleNamespace(target_flags=0, type_name="Normal"),
            20: SimpleNamespace(target_flags=0x08, type_name="Water"),
        }
        actions = _legal_battle_actions(state, moves)
        selected = [action for action in actions if action.get("move_id") == 10]
        spread = [action for action in actions if action.get("move_id") == 20]
        switches = [action for action in actions if action["kind"] == "switch"]
        self.assertEqual({action["target"] for action in selected}, {1, 3})
        self.assertEqual([(action["actor"], action["target_scope"]) for action in spread], [(0, "both_opponents")])
        self.assertIsNone(spread[0]["target"])
        self.assertEqual([(action["actor"], action["slot"]) for action in switches], [(0, 2)])

    def test_certified_double_step_queues_one_actor_and_requires_reevaluation(self):
        before = {
            "frame": 1, "map": {}, "ui": {},
            "battle": {
                "active": True, "format": "double",
                "menu": {"state": "command_menu", "command_battler": 0},
                "mons": [
                    {"slot": 0, "present": True, "state": {"species": 1, "personality": 10, "moves": [10], "pp": [5], "current_hp": 20}},
                    {"slot": 1, "present": True, "state": {"species": 2, "moves": [20], "pp": [5], "current_hp": 20}},
                    {"slot": 2, "present": True, "state": {"species": 3, "personality": 30, "moves": [30], "pp": [5], "current_hp": 20}},
                    {"slot": 3, "present": True, "state": {"species": 4, "moves": [40], "pp": [5], "current_hp": 20}},
                ],
            },
            "party": {"mons": []},
        }
        immediate = copy.deepcopy(before)
        immediate["frame"] = 2
        immediate["battle"]["menu"]["command_battler"] = 2
        legal = {"kind": "move", "actor": 0, "target": 1, "slot": 0, "move_id": 10, "type": "Normal"}
        compact = _compact_state(before)
        certificate = {
            "certificate_id": "double-cert", "state_hash": compact["state_hash"],
            "legal_actions": [legal], "state": {}, "compact_state": compact,
            "boundary": {"format": "double", "actor": 0},
        }

        class Adapter:
            gba = object()

            @staticmethod
            def select_double_move(actor, slot, *, expected_type, target_slot):
                return {"actor": actor, "slot": slot, "type": expected_type, "target": target_slot}

            @staticmethod
            def observe():
                return immediate

        with patch("games.run_and_bun.capabilities._stable_battle_observation", return_value=(before, compact)), patch(
            "games.run_and_bun.capabilities._battle_certificate", return_value=certificate
        ):
            result = _battle_step({
                "state_hash": compact["state_hash"], "certificate_id": "double-cert", "action": legal,
            }, adapter=Adapter(), persist=False)
        self.assertTrue(result["verified"])
        self.assertEqual(result["actual"]["allied_action_outcome"], "queued")
        self.assertEqual(result["actual"]["resolution"]["next_actor"], 2)

    def test_branch_score_is_lexicographic_and_terminal_is_ram_derived(self):
        state = {
            "battle": {"active": False},
            "party": {"mons": [
                {"present": True, "state": {"species": 1, "current_hp": 10, "held_item": 5, "pp": [3]}},
                {"present": True, "state": {"species": 2, "current_hp": 0, "held_item": 0, "pp": [0]}},
            ]},
        }
        self.assertIsNone(_branch_terminal(state))
        state["text"] = {"last_page": {"text": "GPT defeated Camper Gavi!"}}
        self.assertEqual(_branch_terminal(state), "win")
        state["text"] = {"battle_printers": [{"text": "GPT got ¥272 for winning!"}]}
        self.assertEqual(_branch_terminal(state), "win")
        score = _branch_score(state, outcome="win", depth=2, preserve_species={1})
        self.assertEqual(score[:4], [1, 1, 1, 10])
        state["battle"]["active"] = True
        state["party"]["mons"][0]["state"]["current_hp"] = 0
        self.assertEqual(_branch_terminal(state), "loss")

    def test_branch_terminal_does_not_misclassify_auto_healed_whiteout(self):
        state = {
            "battle": {"active": False},
            "party": {"mons": [{"present": True, "state": {"species": 1, "current_hp": 10}}]},
            "text": {"last_page": {"text": "GPT whited out!"}, "raw": b"\xff"},
        }
        self.assertEqual(_branch_terminal(state), "loss")

    def test_branch_search_rejects_an_unbounded_wall_clock(self):
        with self.assertRaisesRegex(CapabilityError, "invalid branch search limit"):
            _battle_branch_search({"state_hash": "unused", "max_seconds": 0})

    def test_branch_search_prunes_a_semantically_identical_path_state(self):
        adapter = SimpleNamespace(gba=SimpleNamespace(load_state=lambda _path: None))
        observation = {
            "battle": {"active": True},
            "party": {"mons": [{"present": True, "state": {"species": 1, "current_hp": 10}}]},
        }
        counter = {"nodes": 0, "transpositions": 0, "cycles": 0}
        with patch("games.run_and_bun.capabilities._stable_battle_observation", return_value=(observation, {"state_hash": "same"})):
            result = _search_battle_node(
                adapter, Path("unused.state"), Path("."), depth=1,
                max_depth=5, max_nodes=10, preserve_species=set(), counter=counter,
                cache={}, path_states=frozenset({"same"}), deadline=time.monotonic() + 1,
            )
        self.assertEqual((result["outcome"], result["complete"], counter["cycles"]), ("semantic_cycle", True, 1))

    def test_branch_search_wall_clock_limit_is_incomplete(self):
        result = _search_battle_node(
            SimpleNamespace(), Path("unused.state"), Path("."), depth=0,
            max_depth=5, max_nodes=10, preserve_species=set(),
            counter={"nodes": 0, "transpositions": 0, "cycles": 0},
            cache={}, path_states=frozenset(), deadline=time.monotonic() - 1,
        )
        self.assertEqual((result["outcome"], result["complete"]), ("wall_clock_limit", False))

    def test_pp_delta_attribution_never_guesses_first_move(self):
        def state(pp):
            return {"battle": {"mons": [{"slot": 1, "present": True, "state": {"species": 9, "moves": [11, 22, 33, 44], "pp": pp, "current_hp": 10}}]}}

        self.assertEqual(_pp_deltas(state([5, 5, 5, 5]), state([5, 4, 5, 5]), (1,)), [{"battler": 1, "slot": 1, "move_id": 22, "delta": 1}])
        self.assertEqual(_pp_deltas(state([5, 5, 5, 5]), state([5, 5, 5, 5]), (1,)), [])
        self.assertEqual(_battle_signature(state([5, 5, 5, 5]))[0][3], (5, 5, 5, 5))

    def test_selected_move_pp_is_audited_at_stable_boundary(self):
        exact = [{"battler": 0, "slot": 1, "move_id": 523, "delta": 1}]
        self.assertEqual(_audit_selected_move_pp("move", 1, exact), [])
        self.assertTrue(_audit_selected_move_pp("move", 1, []))
        self.assertEqual(_audit_selected_move_pp("move", 1, [], interrupted_before_execution=True), [])
        self.assertTrue(_audit_selected_move_pp("move", 1, exact + [{"battler": 2, "slot": 0, "move_id": 10, "delta": 1}]))

    def test_move_announcement_distinguishes_commit_from_execution(self):
        self.assertFalse(_move_announced("Foe Eelektrik used Shock Wave! Croakatoa fainted!", "Vacuum Wave"))
        self.assertTrue(_move_announced("Croakatoa used\nVacuum Wave!", "Vacuum Wave"))

    def test_opponent_effect_proves_move_execution_when_text_tail_is_truncated(self):
        before = {"species": 77, "current_hp": 13, "status": 0, "stat_stages": [6] * 8}
        after = {**before, "current_hp": 10}
        self.assertTrue(_opponent_effect_observed(before, after))
        self.assertFalse(_opponent_effect_observed(before, before))

    def test_new_sleep_can_prevent_selected_move_before_pp_spend(self):
        foe_pp = [{"battler": 1, "slot": 3, "move_id": 320, "delta": 1}]
        self.assertTrue(_status_prevented_execution(
            "move", [], {"species": 388, "status": 0}, {"species": 388, "status": 3}, foe_pp
        ))

    def test_battle_hash_ignores_ephemeral_field_message_mode(self):
        base = {
            "frame": 10,
            "map": {"group": 0, "number": 25},
            "mode": "dialogue",
            "ui": {"field_message_box_mode": 13, "field_message_box_mode_name": "party_prompt", "battle_command": 0},
            "party": {"mons": []},
            "battle": {"active": True, "kind": None, "menu": {"state": "party_switch"}, "party_switch_required": True, "mons": []},
        }
        other = {**base, "ui": {**base["ui"], "field_message_box_mode": 17, "field_message_box_mode_name": "party_menu"}}
        self.assertEqual(_compact_state(base)["state_hash"], _compact_state(other)["state_hash"])

    def test_battle_boundary_exposes_forced_replacement_cause(self):
        normal = {"battle": {"menu": {"state": "command_menu"}, "party_switch_required": False}}
        forced = {"battle": {"menu": {"state": "party_switch"}, "party_switch_required": True}}
        self.assertEqual(_battle_boundary(normal)["transition_cause"], "normal_decision")
        self.assertEqual(_battle_boundary(forced)["transition_cause"], "forced_replacement")

    def test_native_first_gate_rejects_shell_when_match_exists(self):
        decision = self.registry.authorize_fallback("find a trainer and walk to it", "shell")
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "native_capability_available")
        self.assertEqual(decision["suggestedCapability"], "game_seek_npc")

    def test_map_transition_capabilities_are_registered(self):
        for name in ("game_map_transitions", "game_travel_transition", "game_travel_warp"):
            self.assertIn(name, self.registry.names())
        travel = self.registry.inspect("game_travel_transition")
        properties = travel["inputSchema"]["properties"]
        self.assertIn("direction", properties)
        self.assertIn("destination", properties)

    def test_nearest_equivalent_double_door_warp_is_selected(self):
        warps = [
            {"x": 6, "y": 8, "warp_id": 3},
            {"x": 7, "y": 8, "warp_id": 3},
        ]
        self.assertEqual(_select_destination_warp(warps, (10, 4))["x"], 7)

    def test_scripted_transit_capabilities_are_registered(self):
        for name in ("game_map_transit_options", "game_travel_transit"):
            self.assertIn(name, self.registry.names())
        transit = self.registry.inspect("game_travel_transit")
        self.assertIn("local_id", transit["inputSchema"]["properties"])
        self.assertIn("expected_destination", transit["inputSchema"]["properties"])

    def test_unknown_capability_is_structured(self):
        with self.assertRaisesRegex(Exception, "unknown capability"):
            self.registry.inspect("does_not_exist")


if __name__ == "__main__":
    unittest.main()
