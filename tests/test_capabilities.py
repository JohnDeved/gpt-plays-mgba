from __future__ import annotations

import unittest
from unittest.mock import patch

from games.run_and_bun.capabilities import (
    CapabilityError,
    _audit_selected_move_pp,
    _battle_branch_search,
    _battle_boundary,
    _battle_signature,
    _branch_score,
    _branch_terminal,
    _compact_state,
    _capture_target,
    _legal_battle_actions,
    _pokecenter_service,
    _pp_deltas,
    _progress_snapshot,
    _storage_snapshot_data,
    _select_destination_warp,
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
            "game_party_reorder", "game_heal_party", "game_pokemon_build_options",
            "game_pokecenter_service_catalog", "game_pokecenter_service",
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

    def test_branch_search_fails_fast_while_cycle_pruning_is_unverified(self):
        with self.assertRaisesRegex(CapabilityError, "semantic cycle pruning") as raised:
            _battle_branch_search({"state_hash": "unused"})
        self.assertEqual(raised.exception.code, "SEARCH_DISABLED")

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
