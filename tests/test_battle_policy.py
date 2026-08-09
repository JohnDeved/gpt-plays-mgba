from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from games.run_and_bun.battle_policy import (
    BattleHistory,
    BattlePolicy,
    PolicyError,
    policy_bundle_hash,
    validate_profile,
)
from games.run_and_bun.battle_review import QualificationLedger, review_episode


def certificate(*, player_species=1, opponent_species=2, fresh=True, move_ids=(10, 11), opponent_hp=20):
    return {
        "state": {
            "player": {"species": player_species, "hp": 30, "max_hp": 30},
            "opponent": {"species": opponent_species, "hp": opponent_hp, "max_hp": 20},
        },
        "compact_state": {
            "battle": {
                "mons": [
                    {"slot": 0, "species": player_species, "hp": 30, "max_hp": 30, "moves": list(move_ids), "pp": [5, 5]},
                    {"slot": 1, "species": opponent_species, "hp": opponent_hp, "max_hp": 20, "moves": [20], "pp": [5]},
                ]
            },
            "party": [
                {"slot": 0, "species": player_species, "hp": 30, "max_hp": 30, "status": 0},
                {"slot": 1, "species": 3, "hp": 25, "max_hp": 25, "status": 0},
            ],
        },
        "boundary": {"party_switch_required": False},
        "legal_actions": [
            {"kind": "move", "slot": 0, "move_id": move_ids[0], "pp": 5},
            {"kind": "move", "slot": 1, "move_id": move_ids[1], "pp": 5},
            {"kind": "switch", "slot": 1, "species": 3, "hp": 25, "status": 0},
        ],
        "alternatives": [
            {"slot": 0, "move_id": move_ids[0], "damage_range": [5, 7], "damage_est": 6, "ko_before_hit": False, "evidence": {"kind": "rom_formula"}},
            {"slot": 1, "move_id": move_ids[1], "damage_range": [3, 4], "damage_est": 3.5, "ko_before_hit": False, "evidence": {"kind": "rom_formula"}},
        ],
        "incoming": {"max_damage_est": 4, "moves": []},
        "proof": {"level": "expected_best"},
    }


class BattlePolicyTests(unittest.TestCase):
    def profile(self):
        return {
            "schema_version": 1,
            "id": "fixture-policy",
            "trainer_key": "fixture",
            "roles": {"lead": {"species": 1}, "reserve": {"species": 3}},
            "strategy_ids": [],
            "reserve_objectives": [{"role": "reserve", "for_opponent_species": [9]}],
            "constraints": [
                {
                    "id": "fresh-preference",
                    "when": {"active_role": "lead", "opponent_species": [2], "fresh_entry": True},
                    "directives": [{"kind": "prefer", "action": {"kind": "move", "move_id": 11}}],
                }
            ],
        }

    def test_declarative_preference_is_audited_and_legal(self):
        policy = BattlePolicy(self.profile())
        decision = policy.decide(certificate())
        self.assertEqual(decision["action"]["move_id"], 11)
        self.assertEqual(decision["applied_strategy_ids"], ["fresh-preference"])
        self.assertEqual(decision["action"]["kind"], "move")
        self.assertEqual(decision["behavior_hash"], policy.behavior_hash)
        self.assertTrue(all(item["action"] in certificate()["legal_actions"] for item in decision["candidates"]))

    def test_action_count_and_fresh_entry_reconstruct_from_verified_records(self):
        profile = self.profile()
        profile["constraints"][0]["when"] = {
            "active_role": "lead",
            "opponent_species": [2],
            "action_count": {"role": "lead", "move_id": 11, "equals": 0},
        }
        policy = BattlePolicy(profile)
        cert = certificate()
        first = policy.choose(cert)
        policy.record_verified(cert, first)
        second = policy.choose(cert)
        self.assertEqual(first["move_id"], 11)
        self.assertNotEqual(policy.last_decision["applied_strategy_ids"], ["fresh-preference"])
        self.assertEqual(policy.history.snapshot()["verified_actions"], 1)
        self.assertFalse(policy.history.fresh_entry)

    def test_history_restarts_from_verified_jsonl(self):
        cert = certificate()
        record = {"verified": True, "certificate": cert, "action": {"kind": "move", "slot": 1, "move_id": 11}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "battle.jsonl"
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            history = BattleHistory.from_jsonl(path)
        self.assertEqual(history.snapshot()["verified_actions"], 1)
        self.assertFalse(history.fresh_entry)

    def test_behavior_hash_excludes_prose_and_evidence(self):
        profile = self.profile()
        other = copy.deepcopy(profile)
        other["intent"] = "different prose"
        other["evidence"] = {"losses": 99}
        self.assertEqual(policy_bundle_hash(profile), policy_bundle_hash(other))
        changed = copy.deepcopy(profile)
        changed["constraints"][0]["directives"][0]["action"]["move_id"] = 10
        self.assertNotEqual(policy_bundle_hash(profile), policy_bundle_hash(changed))

    def test_unsupported_predicate_is_rejected(self):
        profile = self.profile()
        profile["constraints"][0]["when"]["arbitrary_expression"] = "true"
        self.assertTrue(any("unsupported" in error for error in validate_profile(profile)))
        with self.assertRaises(PolicyError):
            BattlePolicy(profile)


class BattleReviewTests(unittest.TestCase):
    def transition(self, *, uncertainty=False):
        decision = {
            "policy_id": "fixture-policy",
            "behavior_hash": "b" * 64,
            "action": {"kind": "move", "slot": 0, "move_id": 10},
            "selected": {"action": {"kind": "move", "slot": 0, "move_id": 10}, "score": [1, 1, 0, 0]},
            "candidates": [
                {"action": {"kind": "move", "slot": 0, "move_id": 10}, "score": [1, 1, 0, 0]},
                {"action": {"kind": "switch", "slot": 1, "species": 3}, "score": [1, 1, 0, 0]},
            ],
            "uncertainties": {"action_changing": ["tie"] if uncertainty else [], "other": []},
        }
        return {
            "action_id": "a1",
            "pre_state_hash": "state-1",
            "policy_decision": decision,
            "actual": {"allied_action_outcome": "executed"},
            "discrepancies": [],
        }

    def test_clean_win_qualifies_only_without_action_changing_findings(self):
        review = review_episode([self.transition()], terminal="win", source="cartridge_clone", opening_state_hash="open", behavior_hash="b" * 64, policy_id="fixture")
        self.assertEqual(review["status"], "clean")
        with tempfile.TemporaryDirectory() as directory:
            ledger = QualificationLedger(Path(directory) / "qualification.json")
            for _ in range(2):
                state = ledger.record(review)
            self.assertFalse(state["ready"])
            state = ledger.record(review)
            self.assertTrue(state["ready"])

    def test_uncertainty_creates_deduplicated_counterfactual_queue(self):
        review = review_episode([self.transition(uncertainty=True), self.transition(uncertainty=True)], terminal="win", source="cartridge_clone", opening_state_hash="open", behavior_hash="b" * 64, policy_id="fixture")
        self.assertEqual(review["status"], "needs_improvement")
        self.assertEqual(len(review["counterfactuals"]), 1)

    def test_loss_resets_qualification_and_records_cause(self):
        review = review_episode([self.transition()], terminal="loss", source="cartridge_clone", opening_state_hash="open", behavior_hash="b" * 64, policy_id="fixture")
        self.assertEqual(review["classification"], "preparation_team_failure")
        with tempfile.TemporaryDirectory() as directory:
            ledger = QualificationLedger(Path(directory) / "qualification.json")
            ledger.record(review)
            self.assertEqual(ledger.state["streak"], 0)


if __name__ == "__main__":
    unittest.main()
