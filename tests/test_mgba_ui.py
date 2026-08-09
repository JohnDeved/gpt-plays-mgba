import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest

from client.mgba_clone import disposable_clone


AX_WINDOWS = r'''
on run argv
  set targetPid to (item 1 of argv) as integer
  set outputText to ""
  tell application "System Events"
    tell first application process whose unix id is targetPid
      repeat with w in every window
        set outputText to outputText & (name of w as text) & linefeed
      end repeat
    end tell
  end tell
  return outputText
end run
'''


@unittest.skipUnless(sys.platform == "darwin", "macOS Accessibility regression")
class MGBAWindowTests(unittest.TestCase):
    def test_clone_hides_scripting_before_idle_stop(self):
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
                self.assertIsNotNone(pid)
                try:
                    os.kill(pid, signal.SIGCONT)
                    result = subprocess.run(
                        ["/usr/bin/osascript", "-", str(pid)],
                        input=AX_WINDOWS,
                        text=True,
                        capture_output=True,
                        timeout=5,
                    )
                finally:
                    os.kill(pid, signal.SIGSTOP)

                self.assertEqual(result.returncode, 0, result.stderr)
                windows = [line for line in result.stdout.splitlines() if line]
                self.assertEqual(len(windows), 1, windows)
                self.assertTrue(windows[0].startswith("mGBA"), windows)
                process_state = subprocess.run(
                    ["ps", "-o", "state=", "-p", str(pid)],
                    text=True,
                    capture_output=True,
                    check=True,
                ).stdout.strip()
                self.assertIn("T", process_state)


if __name__ == "__main__":
    unittest.main()
