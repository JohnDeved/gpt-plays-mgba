from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch
import unittest

from games.run_and_bun.battle_review import REVIEW_DIR, REVIEW_STATE_DIR
from games.run_and_bun.capabilities import _battle_action_frame_budget


PATH = Path(__file__).parents[1] / "tools" / "run_battle_policy.py"
SPEC = importlib.util.spec_from_file_location("run_battle_policy", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class BattlePolicyRunnerTests(unittest.TestCase):
    def test_locked_rollout_gets_multi_turn_verification_budget(self):
        self.assertEqual(_battle_action_frame_budget({"kind": "move", "move_id": 205}, 1800), 2400)
        self.assertEqual(_battle_action_frame_budget({"kind": "move", "move_id": 33}, 1800), 1800)

    def test_review_states_use_the_canonical_review_root(self):
        self.assertEqual(REVIEW_STATE_DIR.parent, REVIEW_DIR)
        self.assertEqual(REVIEW_STATE_DIR.name, "states")

    def test_precontact_clone_requires_atomic_trainer_engagement(self):
        overworld = {"battle": {"active": False}}
        self.assertIn("--trainer-local-id", MODULE._clone_start_error(overworld, None))
        self.assertIsNone(MODULE._clone_start_error(overworld, 23))
        self.assertIsNone(MODULE._clone_start_error({"battle": {"active": True}}, None))

    def test_trainer_engagement_waits_for_battle_script_after_dialogue_closes(self):
        adapter = MagicMock()
        adapter.follow_live_path_to_npc.return_value = {
            "reason": "interacted",
            "state": {"mode": "dialogue", "text": {"current": {"text": "Ready?"}}},
        }
        adapter.advance_dialogue.return_value = ["Ready?"]
        adapter.observe.side_effect = [
            {"mode": "overworld", "battle": {"active": False}, "text": {}},
            {"mode": "battle", "battle": {"active": True}, "text": {}},
        ]

        MODULE._engage_trainer(adapter, 23, "map:0:25/local:23", require_ready=False)

        adapter.gba.wait_frames.assert_called_once_with(12)

    def test_live_preflight_rejects_stale_party_fixture(self):
        class Adapter:
            def observe(self):
                return {
                    "map": {"group": 0, "number": 25},
                    "party": {"mons": [
                        {"slot": 0, "state": {"species": 777, "level": 17, "moves": [252]}},
                    ]},
                }

        plan = {
            "fight": {"id": "map:0:25/local:23"},
            "party": [{"slot": 0, "species_id": 111, "level": 17, "moves": [30, 479, 184, 23]}],
        }
        result = MODULE._live_preflight(Adapter(), plan)
        self.assertFalse(result["allowed"])
        self.assertTrue(any("species_id mismatch" in error for error in result["errors"]))

    def test_live_preflight_accepts_matching_map_and_party(self):
        class Adapter:
            def observe(self):
                return {
                    "map": {"group": 0, "number": 25},
                    "party": {"mons": [
                        {"slot": 0, "state": {"species": 111, "level": 17, "moves": (30, 479, 184, 23)}},
                    ]},
                }

        plan = {
            "fight": {"id": "map:0:25/local:23"},
            "party": [{"slot": 0, "species_id": 111, "level": 17, "moves": [30, 479, 184, 23]}],
        }
        result = MODULE._live_preflight(Adapter(), plan)
        self.assertTrue(result["allowed"])

    def test_schema_three_live_preflight_requires_and_checks_exact_fixture(self):
        class Adapter:
            def observe(self):
                return {
                    "map": {"group": 0, "number": 25},
                    "party": {"mons": [{
                        "slot": 0,
                        "state": {
                            "species": 111, "personality": 7, "level": 17,
                            "current_hp": 59, "max_hp": 59, "status": 0,
                            "moves": (30, 479, 184, 23), "pp": (25, 15, 40, 20),
                            "held_item": 522, "ability_num": 1,
                            "checksum": {"valid": True},
                        },
                    }]},
                }

        party = {
            "slot": 0, "species_id": 111, "personality": 7, "level": 17,
            "current_hp": 59, "max_hp": 59, "status": 0,
            "moves": [30, 479, 184, 23], "pp": [25, 15, 40, 20],
            "held_item_id": 522, "ability_num": 1,
        }
        plan = {"schema_version": 3, "fight": {"id": "map:0:25/local:23"}, "party": [party]}
        self.assertTrue(MODULE._live_preflight(Adapter(), plan)["allowed"])
        del party["ability_num"]
        result = MODULE._live_preflight(Adapter(), plan)
        self.assertFalse(result["allowed"])
        self.assertIn("ability_num", result["errors"][0])

    def test_progress_card_flushes_print_without_passing_flush_to_json(self):
        with patch("builtins.print") as output:
            MODULE._emit_progress({"turn": 1, "verified": True})
        output.assert_called_once()
        self.assertEqual(output.call_args.kwargs["flush"], True)
        self.assertIn('"turn":1', output.call_args.args[0])

    def test_policy_certificate_uses_reconstructed_fresh_entry(self):
        policy = MagicMock()
        policy.history.fresh_entry = False
        with patch.object(MODULE, "_battle_certificate", return_value={"certificate_id": "cert"}) as certificate:
            self.assertEqual(MODULE._certificate_for_policy("adapter", {"battle": {}}, policy), {"certificate_id": "cert"})
        certificate.assert_called_once_with("adapter", {"battle": {}}, fresh_entry=False)

    def test_terminal_progress_keeps_review_details_on_disk(self):
        summary = MODULE._review_progress({
            "review_id": "r1", "terminal": "win", "status": "clean",
            "classification": "verified_win", "certified_actions": 20,
            "postmortem": {"prediction_comparisons": list(range(100))},
        })
        self.assertEqual(summary, {
            "review_id": "r1", "terminal": "win", "status": "clean",
            "classification": "verified_win", "certified_actions": 20,
        })

    def test_terminal_feedback_classifies_whiteout_as_loss(self):
        result = {"actual": {"resolution": {"feedback": "GPT is out of usable Pokémon! GPT whited out!"}}}
        self.assertEqual(MODULE._terminal_from_result(result), "loss")

    def test_terminal_feedback_classifies_reward_as_win(self):
        result = {"actual": {"resolution": {"feedback": "You got money for winning!"}}}
        self.assertEqual(MODULE._terminal_from_result(result), "win")

    def test_tool_error_becomes_reviewable_unverified_transition(self):
        certificate = {"certificate_id": "cert", "state_hash": "state", "state": {}, "compact_state": {}, "boundary": {}}
        transition = MODULE._tool_error_transition(certificate, {"policy_id": "p"}, {"kind": "switch", "slot": 1}, RuntimeError("switch_failed"))
        self.assertFalse(transition["verified"])
        self.assertEqual(transition["actual"]["allied_action_outcome"], "tool_error")
        self.assertIn("switch_failed", transition["discrepancies"][0])


if __name__ == "__main__":
    unittest.main()
