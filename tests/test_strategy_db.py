import importlib.util
import json
from pathlib import Path
import tempfile
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
            "influential_strategy_ids": ["pivot"],
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

    def test_two_three_win_trainers_promote_to_reusable_tested(self):
        data = self.data()
        for trainer in ("trainer-1", "trainer-2"):
            for attempt in range(3):
                review = self.review()
                review.update({
                    "review_id": f"{trainer}-{attempt}",
                    "trainer_key": trainer,
                    "opening_state_hash": f"{trainer}-state-{attempt}",
                })
                MODULE.record_review(data, review, [])
        self.assertEqual(data["strategies"][0]["status"], "reusable_tested")

    def test_health_uses_latest_trainer_review_and_exposes_reuse_gap(self):
        data = self.data()
        data["strategies"][0]["status"] = "tested"
        data["strategies"][0]["evidence"]["reproductions"] = [{
            "terminal_win": True, "clean_review": True, "trainer_key": "trainer-1",
        }]
        data["strategies"][0]["evidence"]["trainer_keys"] = ["trainer-1"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = self.review()
            old.pop("agent_review")
            old["created_at"] = "2026-01-01T00:00:00+00:00"
            latest = self.review()
            latest["created_at"] = "2026-01-02T00:00:00+00:00"
            latest["agent_review"].update({
                "schema_version": 1, "reviewed_actions": 4,
                "correct_choices": [], "next_test": "next fight",
            })
            (root / "old.json").write_text(json.dumps(old), encoding="utf-8")
            (root / "latest.json").write_text(json.dumps(latest), encoding="utf-8")
            health = MODULE.system_health(data, root)
        self.assertTrue(health["healthy_to_continue"])
        self.assertEqual(health["reviews"]["legacy_or_incomplete"], 1)
        self.assertEqual(health["reviews"]["latest_blockers"], [])
        self.assertIn("pivot", next(item for item in health["priorities"] if item["kind"] == "cross_trainer_evidence_gap")["strategy_ids"])


if __name__ == "__main__":
    unittest.main()
