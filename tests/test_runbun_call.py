import contextlib
import io
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tools.runbun_call import _brief_result, main


class RunbunCallTest(unittest.TestCase):
    def test_brief_battle_output_keeps_decision_evidence_without_duplicate_state(self):
        brief = _brief_result("game_battle_evaluate", {
            "state_hash": "state", "certificate_id": "cert", "state": {"player": {"hp": 1}},
            "compact_state": {"move_data": {"huge": "duplicate"}},
            "legal_actions": [{"kind": "move", "slot": 0, "move_id": 1, "pp": 10}],
            "decision": {"reason": "safe"},
            "behavior_hash": "behavior",
            "policy_decision": {
                "policy_id": "policy", "classification": "heuristic",
                "action": {"kind": "move", "slot": 0, "move_id": 1},
                "candidates": [{
                    "action": {"kind": "move", "slot": 0, "move_id": 1},
                    "score": [1, 2], "safe": True, "evidence": "modeled",
                    "uncertainties": ["hidden AI"], "forbidden_by": [],
                }],
                "uncertainties": {"action_changing": ["hidden AI"]},
                "applied_strategy_ids": ["strategy"],
            },
            "chosen": {"move_id": 1, "move": "Hit", "damage_range": [4, 5], "mechanics_coverage": {"huge": "duplicate"}},
            "alternatives": [{"move_id": 2, "move": "Other", "damage_range": [1, 2]}],
            "incoming": {"max_damage_est": 3, "moves": [{"move_id": 3, "damage_range": [2, 3]}]},
            "proof": {"level": "minimax_visible", "claim": "safe", "mechanics_coverage": {"huge": "duplicate"}},
        })
        self.assertEqual(brief["chosen"]["damage_range"], [4, 5])
        self.assertEqual(brief["incoming"]["moves"][0]["move_id"], 3)
        self.assertEqual(brief["uncertainties"], ["hidden AI"])
        self.assertNotIn("compact_state", brief)
        self.assertNotIn("mechanics_coverage", brief["chosen"])
        self.assertEqual(brief["behavior_hash"], "behavior")
        self.assertEqual(brief["policy_decision"]["action"]["move_id"], 1)
        self.assertEqual(brief["policy_decision"]["candidates"][0]["uncertainty_ids"], [0])
        self.assertNotIn("legal_actions", brief)
        self.assertNotIn("alternatives", brief)

    def test_brief_battle_step_keeps_post_opponent_hp(self):
        brief = _brief_result("game_battle_step", {
            "verified": True,
            "actual": {"resolution": {"feedback": "x" * 500}},
            "observation": {"party": [{"slot": 0, "species": 1, "hp": 2, "pp": [9]}], "battle": {"active": True, "mons": [
                {"slot": 1, "species": 307, "hp": 21, "max_hp": 46, "pp": [22], "stat_stages": [6] * 8},
            ]}},
        })
        self.assertEqual(brief["post_state"]["battle"]["mons"][0]["hp"], 21)
        self.assertNotIn("pp", brief["post_state"]["party"][0])
        self.assertEqual(len(brief["actual"]["resolution"]["feedback"]), 320)

    def test_brief_observation_removes_full_stats_and_empty_ui(self):
        brief = _brief_result("game_observe", {
            "state_hash": "state", "mode": "overworld", "map": {"x": 2, "y": 3},
            "party": [{"slot": 0, "species": 1, "hp": 10, "attack": 99}],
            "battle": {"active": False, "mons": [{"species": 99, "hp": 0}]},
            "ui": {"field_start_menu_open": False, "field_message_box_mode": 0, "battle_command_cursors": [0, 0]},
        })
        self.assertEqual(brief["party"], [{"slot": 0, "species": 1, "hp": 10}])
        self.assertEqual(brief["battle"], {"active": False})
        self.assertNotIn("ui", brief)

    def test_clone_routes_child_call_to_clone_port(self):
        response = '{"jsonrpc":"2.0","id":1,"result":{"structuredContent":{"ok":true}}}\n'
        with patch("client.mgba_clone.disposable_clone", return_value=contextlib.nullcontext(SimpleNamespace(port=54321))), \
             patch("tools.runbun_call.subprocess.run", return_value=subprocess.CompletedProcess([], 0, response, "")) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["game_observe", "--clone-state", "fixture.state"]), 0)
        self.assertEqual(run.call_args.kwargs["env"]["MGBA_RPC_PORT"], "54321")
        self.assertIn("MGBA_RUNTIME_DIR", run.call_args.kwargs["env"])


if __name__ == "__main__":
    unittest.main()
