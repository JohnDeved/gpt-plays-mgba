import os
from pathlib import Path
import subprocess
import sys
import unittest

from client.mgba_clone import disposable_clone


FRONTMOST = 'tell application "System Events" to get unix id of first application process whose frontmost is true'


@unittest.skipUnless(sys.platform == "darwin", "macOS Accessibility regression")
class MGBAWindowTests(unittest.TestCase):
    def test_clone_stays_background_and_idle_stops(self):
        state = os.environ.get("MGBA_UI_TEST_STATE")
        if not state:
            self.skipTest("set MGBA_UI_TEST_STATE to a disposable savestate fixture")
        protected = tuple(
            Path(path) for path in os.environ.get("MGBA_UI_PROTECTED_STATES", "").split(":") if path
        )

        for attempt in (1, 2):
            with self.subTest(attempt=attempt), disposable_clone(state, protected_paths=protected) as gba:
                gba.info()
                pid = gba._process_pid
                self.assertNotEqual(subprocess.run(
                    ["/usr/bin/osascript", "-e", FRONTMOST], text=True, capture_output=True, check=True,
                ).stdout.strip(), str(pid))
                self.assertIsNotNone(pid)
                process_state = subprocess.run(
                    ["ps", "-o", "state=", "-p", str(pid)],
                    text=True,
                    capture_output=True,
                    check=True,
                ).stdout.strip()
                self.assertIn("T", process_state)


if __name__ == "__main__":
    unittest.main()
