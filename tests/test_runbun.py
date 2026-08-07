import unittest

from games.runbun import (
    BATTLE_MONS,
    BATTLE_MON_STRIDE,
    PLAYER_PARTY,
    RunBunAdapter,
    decode_gen3_text,
    decode_text_observation,
    decode_battle_mon,
)


class FakeMGBA:
    def __init__(self):
        self.ranges = {}
        self.sequence_calls = []

    def observe(self, reads=None, screenshot=False, **_kwargs):
        values = {
            "save_block1_ptr": 0x02010000,
            "save_block2_ptr": 0x02011000,
            "pc_storage_ptr": 0x02012000,
            "new_game_cursor": 0,
            "yes_no_cursor": 1,
            "battle_command_cursor": 2,
            "battle_move_cursor": 3,
            "party_count": 1,
            "field_message_box_mode": 0,
        }
        return {
            "frame": 42,
            "title": "POKEMON EMER",
            "code": "BPEE",
            "reads": [
                {"name": item["name"], "value": values[item["name"]]}
                for item in reads
            ],
        }

    def read_range(self, address, length):
        if address == 0x02010000:
            return b"\x05\x00\x07\x00\x02\x03\x04\x00"
        if address == 0x02011000:
            return bytes((0xBB, 0xD7, 0xFF, 0, 0, 0, 0, 0, 0))
        if address == PLAYER_PARTY:
            return bytes(length)
        if address == BATTLE_MONS:
            return bytes(length)
        raise AssertionError((hex(address), length))

    def sequence(self, steps):
        self.sequence_calls.append(steps)
        return {"id": 8, "state": "done", "total_steps": len(steps)}

    def wait_frames(self, frames):
        return {"id": 9, "state": "done", "frames": frames}


class RunBunTests(unittest.TestCase):
    def test_gen3_text_decoder_handles_dialogue_and_page_controls(self):
        raw = bytes((0xBB, 0xD7, 0xB8, 0xFA, 0xC1, 0xE3, 0xFF))
        self.assertEqual(decode_gen3_text(raw), "Ac,<PROMPT_SCROLL>Go")

    def test_battle_offsets_decode_little_endian_fields(self):
        raw = bytearray(BATTLE_MON_STRIDE)
        raw[0x00:0x02] = (987).to_bytes(2, "little")
        raw[0x02:0x04] = (12).to_bytes(2, "little")
        raw[0x0C:0x0E] = (33).to_bytes(2, "little")
        raw[0x20:0x22] = (82).to_bytes(2, "little")
        raw[0x22:0x25] = bytes((17, 1, 255))
        raw[0x25:0x29] = bytes((10, 20, 0, 0))
        raw[0x2A:0x2C] = (13).to_bytes(2, "little")
        raw[0x2C] = 2
        raw[0x2E:0x30] = (13).to_bytes(2, "little")
        raw[0x48:0x4C] = (157).to_bytes(4, "little")

        mon = decode_battle_mon(bytes(raw))

        self.assertEqual(mon.species, 987)
        self.assertEqual(mon.attack, 12)
        self.assertEqual(mon.moves[0], 33)
        self.assertEqual(mon.ability, 82)
        self.assertEqual(mon.types, (17, 1, 255))
        self.assertEqual(mon.pp[:2], (10, 20))
        self.assertEqual(mon.current_hp, 13)
        self.assertEqual(mon.level, 2)
        self.assertEqual(mon.experience, 157)

    def test_adapter_reads_verified_pointers_and_structures(self):
        state = RunBunAdapter(FakeMGBA()).observe()

        self.assertEqual(state["frame"], 42)
        self.assertEqual(state["ui"]["yes_no"], 1)
        self.assertEqual(state["save"]["block1"]["x"], 5)
        self.assertEqual(state["save"]["block1"]["y"], 7)
        self.assertEqual(state["save"]["block1"]["map_group"], 2)
        self.assertEqual(state["party"]["count"], 1)
        self.assertEqual(len(state["battle"]["mons"]), 4)
        self.assertFalse(state["battle"]["active"])
        self.assertEqual(state["player"]["name"], "Ac")

    def test_follow_route_batches_input_and_checks_endpoint(self):
        fake = FakeMGBA()
        adapter = RunBunAdapter(fake)

        result = adapter.follow_route(
            ["RIGHT", "DOWN"],
            expected_map=(2, 3),
            expected_position=(5, 7),
        )

        self.assertEqual(result["map"], (2, 3))
        self.assertEqual(result["position"], (5, 7))
        self.assertEqual(len(fake.sequence_calls), 1)
        self.assertEqual(len(fake.sequence_calls[0]), 4)

    def test_follow_route_compresses_clear_straight_runs(self):
        fake = FakeMGBA()
        adapter = RunBunAdapter(fake)

        adapter.follow_route(["RIGHT", "RIGHT", "RIGHT"], settle_frames=8)

        self.assertEqual(
            fake.sequence_calls[0],
            [
                {"keys": ["RIGHT"], "frames": 44},
                {"keys": [], "frames": 8},
            ],
        )


if __name__ == "__main__":
    unittest.main()
