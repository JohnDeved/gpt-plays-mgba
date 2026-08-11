from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from games.run_and_bun.trainer_database import (
    DEFAULT_DATABASE,
    RomImage,
    is_classified_hard,
    lookup_trainer,
    mark_hard_fight,
    profile_from_npc_script,
    stable_trainer_key,
)


class TrainerDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "trainers.json"
        self.path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "trainers": {
                        "map:0:24/local:7": {
                            "overworld": {
                                "map_group": 0,
                                "map_number": 24,
                                "local_id": 7,
                                "graphics_id": 54,
                                "script_address": "0x08223e3a",
                            },
                            "battle": {"hard_fight": True},
                            "roster": [{"species_id": 619}],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_stable_key_uses_map_and_local_id(self):
        self.assertEqual(stable_trainer_key(0, 24, 7), "map:0:24/local:7")

    def test_default_database_prefers_versioned_repo_copy(self):
        self.assertTrue(DEFAULT_DATABASE.exists())
        self.assertTrue(str(DEFAULT_DATABASE).endswith(".agents/skills/prepare-runbun-hard-fight/references/trainers.json"))

    def test_lookup_verifies_secondary_npc_identity(self):
        result = lookup_trainer(
            map_group=0,
            map_number=24,
            local_id=7,
            graphics_id=54,
            script_address=0x08223E3A,
            path=self.path,
        )
        self.assertTrue(result["found"])
        self.assertTrue(result["trusted"])
        self.assertEqual(result["record"]["roster"][0]["species_id"], 619)

    def test_lookup_rejects_identity_drift(self):
        result = lookup_trainer(
            map_group=0,
            map_number=24,
            local_id=7,
            graphics_id=99,
            path=self.path,
        )
        self.assertTrue(result["found"])
        self.assertFalse(result["trusted"])
        self.assertTrue(result["identity_mismatches"])

    def test_loss_classification_is_durable_and_additive(self):
        path = Path(self.temporary.name) / "hard-fights.json"
        key = stable_trainer_key(3, 4, 5)
        self.assertFalse(is_classified_hard(key, path))
        mark_hard_fight(key, review_id="loss-1", path=path)
        mark_hard_fight(key, review_id="loss-2", path=path)
        self.assertTrue(is_classified_hard(key, path))
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["hard_fights"][key]["first_loss_review_id"], "loss-1")

    def test_rom_npc_decoder_builds_exact_profile_skeleton(self):
        rom_path = Path(__file__).parents[1] / "runtime/run-bun/Pokemon Run & Bun (v1.07).gba"
        if not rom_path.is_file():
            self.skipTest("local Run & Bun ROM fixture is unavailable")
        profile = profile_from_npc_script(
            RomImage.from_path(rom_path),
            script_address=0x082238BB,
            map_group=0,
            map_number=21,
            local_id=4,
            graphics_id=55,
        )
        self.assertEqual(profile["overworld"]["trainer_id"], 340)
        self.assertEqual(profile["name"], "Dale")
        self.assertEqual([mon["species_id"] for mon in profile["roster"]], [557, 769, 303, 446])
        self.assertEqual(profile["roster"][0]["held_item_name"], "Berry Juice")
        self.assertEqual(profile["roster"][0]["moves"], [450, 350, 282, 564])
        self.assertFalse(profile["battle"]["roster_complete"])


if __name__ == "__main__":
    unittest.main()
