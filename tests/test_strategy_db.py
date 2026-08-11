import importlib.util
from pathlib import Path
import unittest


PATH = Path(__file__).parents[1] / ".agents/skills/develop-runbun-strategies/scripts/strategy_db.py"
SPEC = importlib.util.spec_from_file_location("runbun_strategy_db", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class StrategyEvidenceTests(unittest.TestCase):
    def data(self):
        return {"strategies": [{
            "id": "pivot",
            "status": "candidate",
            "evidence": {
                "reproductions": [], "exhaustive_searches": [],
                "distinct_state_hashes": [], "trainer_keys": [], "counterexamples": [],
            },
        }]}

    def review(self):
        return {
            "review_id": "review-1",
            "terminal": "win",
            "status": "clean",
            "source": "cartridge_clone",
            "opening_state_hash": "state-1",
            "behavior_hash": "bundle-1",
            "trainer_key": "trainer-1",
            "certified_actions": 4,
            "matched_strategy_ids": ["pivot"],
            "agent_review": {"status": "complete", "author": "agent", "findings": []},
        }

    def test_only_completed_clean_reviews_add_deduplicated_wins(self):
        data = self.data()
        review = self.review()
        MODULE.record_review(data, review, [])
        MODULE.record_review(data, review, [])
        strategy = data["strategies"][0]
        self.assertEqual(len(strategy["evidence"]["reproductions"]), 1)
        self.assertEqual(strategy["status"], "tested")

    def test_unrelated_finding_is_not_a_strategy_counterexample(self):
        data = self.data()
        review = self.review()
        review["agent_review"]["findings"] = [{
            "kind": "tooling_mismatch", "action_changing": True,
            "strategy_id": None, "requirements_matched": False,
        }]
        MODULE.record_review(data, review, [])
        self.assertEqual(data["strategies"][0]["evidence"]["counterexamples"], [])

    def test_matching_action_changing_finding_is_a_counterexample(self):
        data = self.data()
        review = self.review()
        review["agent_review"]["findings"] = [{
            "kind": "policy_gap", "summary": "bad pivot", "action_changing": True,
            "strategy_id": "pivot", "requirements_matched": True,
        }]
        MODULE.record_review(data, review, [])
        self.assertEqual(len(data["strategies"][0]["evidence"]["counterexamples"]), 1)
        self.assertEqual(data["strategies"][0]["status"], "candidate")

    def test_fixed_auto_match_uses_format_and_active_moves(self):
        strategy = {"auto_match": {"battle_formats": ["single"], "active_move_ids_any": [252]}}
        self.assertTrue(MODULE.strategy_applicable(strategy, {"battle_format": "single", "active_move_ids": [252, 10]}))
        self.assertFalse(MODULE.strategy_applicable(strategy, {"battle_format": "double", "active_move_ids": [252]}))

    def test_auto_match_requires_a_real_state_discriminator(self):
        data = self.data()
        data["schema_version"] = 2
        data["rom_sha256"] = "a" * 64
        data["strategies"][0].update({
            "title": "Pivot", "tags": [], "intent": "pivot", "scope": {},
            "requirements": [], "line": [], "blockers": [], "abort_rules": [],
            "auto_match": {"battle_format": ["single"]},
        })
        self.assertTrue(any("state discriminator" in error for error in MODULE.validate(data)))

    def test_status_match_does_not_become_a_single_battle_wildcard(self):
        strategy = {"auto_match": {"battle_format": ["single"], "player_status_any": 0x27}}
        self.assertFalse(MODULE.strategy_applicable(strategy, {"battle_format": "single", "player_status": 0}))
        self.assertTrue(MODULE.strategy_applicable(strategy, {"battle_format": "single", "player_status": 0x20}))

    def test_summary_exposes_cross_trainer_reuse_without_reading_raw_evidence(self):
        data = self.data()
        summary = MODULE.summarize(data)
        self.assertEqual(summary["strategy_count"], 1)
        self.assertEqual(summary["automatic_reusable_count"], 0)
        self.assertEqual(summary["strategies"][0]["trainers"], 0)
        self.assertEqual(summary["strategies"][0]["eligible_status"], "candidate")


if __name__ == "__main__":
    unittest.main()
