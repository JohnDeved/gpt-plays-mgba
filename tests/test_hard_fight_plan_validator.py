from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

from games.run_and_bun.battle_policy import load_profile, load_strategies, policy_bundle_hash


PATH = Path(__file__).parents[1] / ".agents/skills/prepare-runbun-hard-fight/scripts/validate_plan.py"
SPEC = importlib.util.spec_from_file_location("hard_fight_validate_plan", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class HardFightPromotionTests(unittest.TestCase):
    def test_requires_three_consecutive_same_artifact_clone_wins(self):
        policy = "games/run_and_bun/gavi_policy.py"
        digest = MODULE.hashlib.sha256((MODULE.REPO_ROOT / policy).read_bytes()).hexdigest()
        evidence = {
            "executable_policy": policy,
            "policy_sha256": digest,
            "opening_checkpoint_sha256": "a" * 64,
            "reproductions": [
                {"terminal_win": True, "source": "clone", "policy_sha256": digest, "opening_checkpoint_sha256": "a" * 64}
                for _ in range(2)
            ],
        }
        self.assertTrue(MODULE._promotion_errors(evidence))
        evidence["reproductions"].append(dict(evidence["reproductions"][-1]))
        self.assertEqual(MODULE._promotion_errors(evidence), [])

    def test_v2_requires_three_clean_wins_for_behavior_bundle(self):
        profile = "games/run_and_bun/policy_profiles/gavi.json"
        digest = policy_bundle_hash(load_profile(profile), load_strategies())
        opening = "b" * 64
        evidence = {
            "policy_profile": profile,
            "behavior_hash": digest,
            "opening_checkpoint_sha256": opening,
            "reproductions": [
                {
                    "terminal_win": True,
                    "clean_review": True,
                    "source": "clone",
                    "behavior_hash": digest,
                    "opening_checkpoint_sha256": opening,
                }
                for _ in range(3)
            ],
        }
        self.assertEqual(MODULE._promotion_errors(evidence), [])
        evidence["reproductions"][2]["clean_review"] = False
        self.assertTrue(MODULE._promotion_errors(evidence))


if __name__ == "__main__":
    unittest.main()
