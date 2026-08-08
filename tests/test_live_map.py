import struct
import unittest

from games.run_and_bun.live_map import LiveMap, read_live_map, read_live_warps


class FakeMapGBA:
    def __init__(self, width=21, height=21, grid_ptr=0x02010000):
        self.width = width
        self.height = height
        self.grid_ptr = grid_ptr
        self.words = [0] * (width * height)
        for y in range(1, height - 1):
            for x in range(1, width - 1):
                self.words[x + y * width] = 3 << 12

    def read_range(self, address, length):
        if address == 0x03005DD0:
            return struct.pack("<III", self.width, self.height, self.grid_ptr)
        if address == self.grid_ptr:
            return struct.pack(f"<{len(self.words)}H", *self.words)
        raise AssertionError((hex(address), length))


class FakeWarpGBA:
    def read_range(self, address, length):
        if address == 0x020368DC:
            return struct.pack("<II", 0, 0x08000100)
        if address == 0x08000100:
            return bytes([0, 2, 0, 0, 0, 0, 0, 0]) + struct.pack("<I", 0x08000200)
        if address == 0x08000200:
            return (
                struct.pack("<hhBBBB", 5, 7, 0, 0, 0, 2)
                + struct.pack("<hhBBBB", 14, 6, 0, 0, 4, 2)
            )
        raise AssertionError((hex(address), length))


class LiveMapTests(unittest.TestCase):
    def test_reads_runtime_warp_destinations(self):
        warps = read_live_warps(FakeWarpGBA())
        self.assertEqual([warp.as_dict() for warp in warps], [
            {"x": 5, "y": 7, "warp_id": 0, "map_num": 0, "map_group": 2, "destination": (2, 0)},
            {"x": 14, "y": 6, "warp_id": 0, "map_num": 4, "map_group": 2, "destination": (2, 4)},
        ])

    def test_reads_runtime_grid_and_finds_path(self):
        fake = FakeMapGBA()
        live = read_live_map(fake)
        self.assertEqual(live.grid_ptr, 0x02010000)
        self.assertTrue(live.walkable(1, 1))
        self.assertEqual(live.path_to((1, 1), (2, 1)), ["RIGHT"])

    def test_path_uses_collision_and_elevation_bits(self):
        fake = FakeMapGBA()
        # Active coordinates are translated by the normal seven-tile border;
        # use a directly constructed tiny map to keep this test independent
        # from the production border size.
        words = [3 << 12] * 25
        words[2] = 1 << 10
        live = LiveMap(5, 5, 0x02010000, tuple(words), origin=0, active_width=5, active_height=5)
        path = live.path_to((0, 0), (4, 0))
        self.assertNotEqual(path, ["RIGHT"] * 4)
        self.assertEqual(len(path), 6)

    def test_collision_zero_bridge_elevations_are_walkable(self):
        words = [3 << 12] * 9
        words[1 + 1 * 3] = 1 << 12
        live = LiveMap(3, 3, 0x02010000, tuple(words), origin=0, active_width=3, active_height=3)
        self.assertTrue(live.walkable(1, 1))
        self.assertFalse(live.step_allowed((0, 1), (1, 1)))

    def test_path_does_not_cross_unverified_elevation_layer(self):
        words = [3 << 12] * 9
        words[1 + 1 * 3] = 1 << 12
        live = LiveMap(3, 3, 0x02010000, tuple(words), origin=0, active_width=3, active_height=3)
        path = live.path_to((0, 1), (2, 1))
        self.assertEqual(path, ["UP", "RIGHT", "RIGHT", "DOWN"])

    def test_path_can_avoid_temporarily_blocked_edge(self):
        words = [3 << 12] * 25
        live = LiveMap(5, 5, 0x02010000, tuple(words), origin=0, active_width=5, active_height=5)
        path = live.path_to(
            (0, 0),
            (2, 0),
            blocked_edges={((0, 0), "RIGHT")},
        )
        self.assertEqual(path, ["DOWN", "RIGHT", "UP", "RIGHT"])

    def test_path_prefers_non_grass_route_but_can_cross_grass(self):
        # Direct middle row is grass; the two-step detour is clear.
        words = [3 << 12] * 15
        for x in (1, 2, 3):
            words[x + 1 * 5] |= 0x00D
        live = LiveMap(5, 3, 0x02010000, tuple(words), origin=0, active_width=5, active_height=3)
        path = live.path_to((0, 1), (4, 1))
        self.assertEqual(path, ["UP", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "DOWN"])
        self.assertEqual(len(live.path_to((0, 1), (4, 1), grass_penalty=0)), 4)

    def test_layout_exposes_raw_tile_fields_and_ascii(self):
        words = [3 << 12] * 9
        words[1 + 1 * 3] = (3 << 12) | 0x00D
        live = LiveMap(3, 3, 0x02010000, tuple(words), origin=0, active_width=3, active_height=3)

        tile = live.tile(1, 1)
        self.assertEqual(tile["raw"], (3 << 12) | 0x00D)
        self.assertEqual(tile["metatile_id"], 0x00D)
        self.assertTrue(tile["walkable"])
        self.assertTrue(tile["grass"])

        layout = live.layout(include_tiles=False)
        self.assertEqual(layout["grid_ptr"], 0x02010000)
        self.assertIn('"', layout["ascii"])
        blocked = LiveMap(3, 3, 0, tuple([1 << 10] * 9), origin=0, active_width=3, active_height=3)
        self.assertIn("#", blocked.ascii())


if __name__ == "__main__":
    unittest.main()
