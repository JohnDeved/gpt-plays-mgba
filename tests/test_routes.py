import unittest

from games.run_and_bun.routes import route101_path


class RouteTests(unittest.TestCase):
    def test_route101_path_uses_static_grid_without_emulator_probing(self):
        path = route101_path((11, 14))

        self.assertEqual(len(path), 30)
        self.assertEqual(path[:8], ["LEFT"] * 4 + ["UP"] * 4)
        self.assertEqual(path[-2:], ["UP", "UP"])

    def test_route101_path_rejects_unreachable_target(self):
        with self.assertRaises(ValueError):
            route101_path((11, 14), target=(0, 0))


if __name__ == "__main__":
    unittest.main()
