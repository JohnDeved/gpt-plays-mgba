from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / ".agents/skills/track-runbun-goals-progress/scripts/progress.py"
SPEC = importlib.util.spec_from_file_location("runbun_progress", SCRIPT)
progress = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(progress)


class ProgressCheckpointTests(unittest.TestCase):
    def test_rolling_checkpoint_is_snapshotted_before_recording(self):
        with tempfile.TemporaryDirectory() as temporary:
            rolling = Path(temporary) / "auto-forward-latest.state"
            rolling.write_bytes(b"first")
            record = {"checkpoints": [], "milestones": [], "furthest_checkpoint_sha256": None}
            checkpoint = progress.record_checkpoint(record, SimpleNamespace(
                checkpoint=str(rolling), state_hash="state", map="3,1,7,4",
            ))
            immutable = Path(checkpoint["path"])
            rolling.write_bytes(b"later")
            self.assertEqual(immutable.read_bytes(), b"first")
            self.assertEqual(progress.sha256(immutable), checkpoint["sha256"])
            self.assertNotEqual(immutable, rolling)


if __name__ == "__main__":
    unittest.main()
