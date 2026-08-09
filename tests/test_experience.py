import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from games.run_and_bun.experience import (
    append_battle_transition,
    contextual_effects,
    load_damage_memory,
    query_damage,
)


def observation(player_hp=30, opponent_hp=40):
    player = {"species": 1, "level": 17, "current_hp": player_hp, "max_hp": 30, "types": [10], "moves": [10], "pp": [9], "attack": 25, "defense": 20, "speed": 20, "special_attack": 20, "special_defense": 20, "stat_stages": [6] * 8, "status": 0, "ability": 1, "held_item": 0}
    opponent = {"species": 2, "level": 17, "current_hp": opponent_hp, "max_hp": 40, "types": [12], "moves": [20], "pp": [9], "attack": 20, "defense": 20, "speed": 19, "special_attack": 20, "special_defense": 20, "stat_stages": [6] * 8, "status": 0, "ability": 2, "held_item": 0}
    return {"party": {"mons": [{"present": True, "state": player}]}, "battle": {"mons": [{"slot": 0, "present": True, "state": player}, {"slot": 1, "present": True, "state": opponent}]}}


class ExperienceTests(unittest.TestCase):
    def test_prevented_move_does_not_create_damage_sample(self):
        effects = contextual_effects(
            observation(), observation(player_hp=20),
            {"kind": "move", "move_id": 10},
            {"allied_action_outcome": "prevented_by_status", "enemy_move_id": 20, "resolution": {"feedback": "is fast asleep"}},
        )
        self.assertEqual([event["source"] for event in effects["damage"]], ["opponent"])

    def test_contextual_transition_records_exact_direct_damage(self):
        effects = contextual_effects(
            observation(),
            observation(opponent_hp=28),
            {"kind": "move", "move_id": 10},
            {"allied_action_outcome": "executed", "enemy_move_id": None, "resolution": {"feedback": "A critical hit!"}},
        )
        damage = effects["damage"][0]
        self.assertEqual(damage["direct_damage"], 12)
        self.assertTrue(damage["exact"])
        self.assertTrue(damage["critical"])

    def test_restoration_and_ko_are_not_exact_damage_samples(self):
        berry = contextual_effects(
            observation(), observation(opponent_hp=35),
            {"kind": "move", "move_id": 10},
            {"allied_action_outcome": "executed", "resolution": {"feedback": "Sitrus Berry restored health!"}},
        )["damage"][0]
        ko = contextual_effects(
            observation(), observation(opponent_hp=0),
            {"kind": "move", "move_id": 10},
            {"allied_action_outcome": "executed", "resolution": {"feedback": "Foe fainted!"}},
        )["damage"][0]
        self.assertIsNone(berry["direct_damage"])
        self.assertTrue(berry["interference"]["berry_or_item_restoration"])
        self.assertIsNone(ko["direct_damage"])
        self.assertTrue(ko["censored_ko"])

    def test_v2_query_retains_context_while_legacy_is_quarantined(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "experience.jsonl"
            path.write_text(json.dumps({"kind": "damage_sample", "key": [1, 10, 2], "damage": 99}) + "\n")
            with patch("games.run_and_bun.experience._path", return_value=path):
                append_battle_transition(
                    observation(), observation(opponent_hp=28),
                    {"kind": "move", "move_id": 10},
                    {"allied_action_outcome": "executed", "enemy_move_id": None, "resolution": {"feedback": "It's super effective!"}},
                    pre_state_hash="before", post_state_hash="after",
                )
                self.assertEqual(load_damage_memory(), {})
                records = query_damage(attacker_species=1, move_id=10, defender_species=2)

        contextual = next(record for record in records if record["schema_version"] == 2)
        legacy = next(record for record in records if record["schema_version"] == 1)
        self.assertEqual(contextual["damage"], 12)
        self.assertEqual(contextual["effects"]["effectiveness"], "super_effective")
        self.assertFalse(contextual["steers_tactical_bounds"])
        self.assertEqual(legacy["proof"], "legacy_context_missing")


if __name__ == "__main__":
    unittest.main()
