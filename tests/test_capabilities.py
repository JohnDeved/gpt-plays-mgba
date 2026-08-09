from __future__ import annotations

import unittest

from games.run_and_bun.capabilities import _compact_state, default_registry


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

    def test_battle_transaction_capabilities_are_registered(self):
        for name in ("game_battle_snapshot", "game_battle_evaluate", "game_battle_commit", "game_battle_verify"):
            self.assertIn(name, self.registry.names())
        commit = self.registry.inspect("game_battle_commit")
        self.assertIn("state_hash", commit["inputSchema"]["properties"])

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

    def test_native_first_gate_rejects_shell_when_match_exists(self):
        decision = self.registry.authorize_fallback("find a trainer and walk to it", "shell")
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "native_capability_available")
        self.assertEqual(decision["suggestedCapability"], "game_seek_npc")

    def test_map_transition_capabilities_are_registered(self):
        for name in ("game_map_transitions", "game_travel_transition"):
            self.assertIn(name, self.registry.names())
        travel = self.registry.inspect("game_travel_transition")
        properties = travel["inputSchema"]["properties"]
        self.assertIn("direction", properties)
        self.assertIn("destination", properties)

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
