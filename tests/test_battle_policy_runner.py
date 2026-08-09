from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch
import unittest


PATH = Path(__file__).parents[1] / "tools" / "run_battle_policy.py"
SPEC = importlib.util.spec_from_file_location("run_battle_policy", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class BattlePolicyRunnerTests(unittest.TestCase):
    def test_progress_card_flushes_print_without_passing_flush_to_json(self):
        with patch("builtins.print") as output:
            MODULE._emit_progress({"turn": 1, "verified": True})
        output.assert_called_once()
        self.assertEqual(output.call_args.kwargs["flush"], True)
        self.assertIn('"turn":1', output.call_args.args[0])

    def test_terminal_feedback_classifies_whiteout_as_loss(self):
        result = {"actual": {"resolution": {"feedback": "GPT is out of usable Pokémon! GPT whited out!"}}}
        self.assertEqual(MODULE._terminal_from_result(result), "loss")

    def test_terminal_feedback_classifies_reward_as_win(self):
        result = {"actual": {"resolution": {"feedback": "You got money for winning!"}}}
        self.assertEqual(MODULE._terminal_from_result(result), "win")


if __name__ == "__main__":
    unittest.main()
