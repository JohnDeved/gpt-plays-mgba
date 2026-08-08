from __future__ import annotations

import unittest

from games.run_and_bun.capabilities import default_registry


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

    def test_native_first_gate_rejects_shell_when_match_exists(self):
        decision = self.registry.authorize_fallback("find a trainer and walk to it", "shell")
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "native_capability_available")
        self.assertEqual(decision["suggestedCapability"], "game_seek_npc")

    def test_unknown_capability_is_structured(self):
        with self.assertRaisesRegex(Exception, "unknown capability"):
            self.registry.inspect("does_not_exist")


if __name__ == "__main__":
    unittest.main()
