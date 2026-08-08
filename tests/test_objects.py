import unittest

from games.run_and_bun.objects import (
    OBJECT_EVENT_COUNT,
    OBJECT_EVENT_STRIDE,
    OBJECT_FLAG_ACTIVE,
    OBJECT_FLAG_IS_PLAYER,
    decode_live_objects,
    object_occupied_edges,
    read_live_event_targets,
    select_live_object,
)


class ObjectTests(unittest.TestCase):
    def test_decodes_runtime_coordinates_and_identity(self):
        raw = bytearray(OBJECT_EVENT_COUNT * OBJECT_EVENT_STRIDE)
        player = memoryview(raw)[:OBJECT_EVENT_STRIDE]
        player[0:4] = (OBJECT_FLAG_ACTIVE | OBJECT_FLAG_IS_PLAYER).to_bytes(4, "little")
        player[4] = 0
        player[5] = 0
        player[8] = 0xFF
        player[9] = 0
        player[10] = 2
        player[11] = 0x53
        player[0x10:0x14] = (15).to_bytes(2, "little") + (10).to_bytes(2, "little")
        player[0x18:0x1A] = (0x32).to_bytes(2, "little")

        npc = memoryview(raw)[OBJECT_EVENT_STRIDE : 2 * OBJECT_EVENT_STRIDE]
        npc[0:4] = OBJECT_FLAG_ACTIVE.to_bytes(4, "little")
        npc[4:8] = bytes((1, 20, 2, 0))
        npc[8:11] = bytes((1, 0, 2))
        npc[11] = 0x33
        npc[0x0C:0x10] = (13).to_bytes(2, "little") + (11).to_bytes(2, "little")
        npc[0x10:0x14] = (14).to_bytes(2, "little") + (12).to_bytes(2, "little")
        npc[0x18:0x1A] = (4).to_bytes(2, "little")

        objects = decode_live_objects(bytes(raw))
        self.assertEqual(len(objects), 2)
        self.assertTrue(objects[0].is_player)
        self.assertEqual(objects[0].position, (8, 3))
        self.assertEqual(objects[0].facing_direction, 2)
        self.assertEqual(objects[1].position, (7, 5))
        self.assertEqual(objects[1].graphics_id, 20)
        self.assertEqual(objects[1].map_id, (2, 0))

    def test_selects_target_by_identity_and_distance(self):
        raw = bytearray(OBJECT_EVENT_COUNT * OBJECT_EVENT_STRIDE)
        for index, (local_id, x) in enumerate(((1, 3), (2, 9))):
            record = memoryview(raw)[index * OBJECT_EVENT_STRIDE : (index + 1) * OBJECT_EVENT_STRIDE]
            record[0:4] = OBJECT_FLAG_ACTIVE.to_bytes(4, "little")
            record[8:11] = bytes((local_id, 0, 2))
            record[11] = 3
            record[0x10:0x14] = (x + 7).to_bytes(2, "little") + (10).to_bytes(2, "little")
        objects = decode_live_objects(bytes(raw))
        target = select_live_object(objects, map_id=(2, 0), graphics_id=None, nearest_to=(4, 3))
        self.assertEqual(target.local_id, 1)
        target = select_live_object(objects, map_id=(2, 0), local_id=2)
        self.assertEqual(target.position, (9, 3))

    def test_occupied_edges_block_entering_npc_tile(self):
        raw = bytearray(OBJECT_EVENT_COUNT * OBJECT_EVENT_STRIDE)
        record = memoryview(raw)[:OBJECT_EVENT_STRIDE]
        record[0:4] = OBJECT_FLAG_ACTIVE.to_bytes(4, "little")
        record[8:11] = bytes((1, 0, 2))
        record[11] = 3
        record[0x10:0x14] = (14).to_bytes(2, "little") + (10).to_bytes(2, "little")
        npc = decode_live_objects(bytes(raw))[0]
        blocked = object_occupied_edges([npc])
        self.assertIn(((6, 3), "RIGHT"), blocked)
        self.assertIn(((8, 3), "LEFT"), blocked)

    def test_decodes_stable_event_template_identity(self):
        class FakeMGBA:
            def read_range(self, address, length):
                if address == 0x020368DC:
                    return b"\0\0\0\0" + (0x02001000).to_bytes(4, "little")
                if address == 0x02001000:
                    return bytes((1, 0, 0, 0)) + (0x08002000).to_bytes(4, "little")
                if address == 0x08002000:
                    raw = bytearray(0x18)
                    raw[0:2] = bytes((7, 54))
                    raw[4:8] = (22).to_bytes(2, "little", signed=True) + (12).to_bytes(2, "little", signed=True)
                    raw[9] = 7
                    raw[0x0C:0x10] = (1).to_bytes(2, "little") + (4).to_bytes(2, "little")
                    raw[0x10:0x14] = (0x08123456).to_bytes(4, "little")
                    raw[0x14:0x16] = (0x345).to_bytes(2, "little")
                    return bytes(raw)
                raise AssertionError((address, length))

        targets = read_live_event_targets(FakeMGBA(), map_id=(0, 24))
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].local_id, 7)
        self.assertEqual(targets[0].script_address, 0x08123456)
        self.assertEqual(targets[0].flag_id, 0x345)
        self.assertEqual(targets[0].as_dict()["source"], "map_event_template")


if __name__ == "__main__":
    unittest.main()
