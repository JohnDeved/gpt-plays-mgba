import unittest

from games.run_and_bun.rom_data import BattleRomData, RomProfileError, experience_for_level


class FakeROM:
    def __init__(self):
        self.calls = []

    def read_range(self, address, length):
        self.calls.append((address, length))
        if address == 0x080000A0:
            return b"POKEMON EMERBPEE"
        if address == 0x083ADEE2:
            raw = bytearray(19 * 19 * 2)
            for i in range(19):
                raw[(i * 19 + i) * 2:(i * 19 + i + 1) * 2] = (0x1000).to_bytes(2, "little")
            raw[7 * 2:8 * 2] = b"\0\0"
            return bytes(raw)
        if address == 0x083A4493:
            return bytes((0xCA, 0xE3, 0xE9, 0xE2, 0xD8, 0xFF)) + b"\0" * 7
        if address == 0x083B0C5E + 20:
            # Offset 10 is a flags field whose low nibble can look like a
            # category. The authoritative category is u16 at offset 14.
            return bytes.fromhex("28 00 00 64 23 00 00 00 00 00 32 00 00 00 00 00 64 00 00 00")
        if address == 0x083B7CE0 + 406 * 36:
            return bytes.fromhex(
                "28 1E 23 37 32 46 0C 03 FF 00 38 00 00 00 00 00 "
                "B0 01 7F 14 46 03 0F 0F 1E 00 26 00 66 00 00 83 00 00 0C 00"
            )
        if address == 0x083EC73C + 406 * 4:
            return (0x083C6A30).to_bytes(4, "little")
        if address == 0x083C6A30:
            return (
                bytes.fromhex(
                    "47 00 01 00 7C 00 0A 00 84 01 0F 00 FF FF 00 00"
                ) + bytes(length - 16)
            )
        raise AssertionError((hex(address), length))


class RomDataTests(unittest.TestCase):
    def test_validates_and_caches_type_chart(self):
        fake = FakeROM()
        data = BattleRomData(fake)
        chart = data.type_chart()
        self.assertEqual(chart[0][0], 1.0)
        self.assertEqual(chart[0][7], 0.0)
        self.assertEqual(len([call for call in fake.calls if call[0] == 0x083ADEE2]), 1)

    def test_reads_move_names_from_fixed_slots(self):
        data = BattleRomData(FakeROM())
        self.assertEqual(data.move_name(1), "Pound")
        self.assertEqual(data.move(1).name, "Pound")

    def test_decodes_complete_move_record(self):
        move = BattleRomData(FakeROM()).move(1)
        self.assertEqual((move.power, move.type_id, move.accuracy, move.pp), (40, 0, 100, 35))
        self.assertEqual(move.category, "physical")
        self.assertEqual(move.priority, 0)

    def test_decodes_negative_priority_from_signed_byte(self):
        class NegativePriorityROM(FakeROM):
            def read_range(self, address, length):
                if address == 0x083A4493 + 13:
                    return bytes((0xD0, 0xDD, 0xE8, 0xD5, 0xE0, 0xFF)) + b"\0" * 7
                if address == 0x083B0C5E + 40:
                    raw = bytearray(super().read_range(0x083B0C5E + 20, 20))
                    raw[8] = 0xFF
                    return bytes(raw)
                return super().read_range(address, length)

        self.assertEqual(BattleRomData(NegativePriorityROM()).move(2).priority, -1)

    def test_decodes_expanded_species_record(self):
        species = BattleRomData(FakeROM()).species(406)
        self.assertEqual(species.base_stats, (40, 30, 35, 55, 50, 70))
        self.assertEqual(species.type_names, ("Grass", "Poison"))
        self.assertEqual(species.catch_rate, 255)
        self.assertEqual(species.growth_rate, 3)
        self.assertEqual(species.ability_ids, (30, 38, 102))
        self.assertEqual(species.zero_ev_stat_ranges(17), {
            "hp": (40, 45), "attack": (13, 22), "defense": (14, 24),
            "speed": (20, 30), "sp_attack": (19, 29), "sp_defense": (25, 37),
        })

    def test_decodes_level_up_learnset(self):
        data = BattleRomData(FakeROM())
        self.assertEqual(
            [(entry.move_id, entry.level) for entry in data.level_up_moves(406)],
            [(71, 1), (124, 10), (388, 15)],
        )
        self.assertEqual(
            [entry.move_id for entry in data.level_up_moves(406, through_level=10)],
            [71, 124],
        )

    def test_growth_curves_recover_known_level_17_experience(self):
        self.assertEqual(experience_for_level(0, 17), 4913)
        self.assertEqual(experience_for_level(3, 17), 3120)
        self.assertEqual(BattleRomData(FakeROM()).species(406).level_from_experience(3120), 17)

    def test_rejects_wrong_rom(self):
        class Wrong(FakeROM):
            def read_range(self, address, length):
                if address == 0x080000A0:
                    return b"WRONG ROM\x00\x00\x00\x00\x00\x00\x00"
                return super().read_range(address, length)

        with self.assertRaises(RomProfileError):
            BattleRomData(Wrong()).validate()
