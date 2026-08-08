from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from games.run_and_bun.trainer_database import lookup_trainer, stable_trainer_key


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


if __name__ == "__main__":
    unittest.main()
