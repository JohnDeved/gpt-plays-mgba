import unittest

from games.run_and_bun.rom_data import BattleRomData, RomProfileError


class FakeROM:
    def __init__(self):
        self.calls = []

    def read_range(self, address, length):
        self.calls.append((address, length))
        if address == 0x080000A0:
            return b"POKEMON EMERBPEE"
        if address == 0x083ADEE0:
            raw = bytearray(20 * 20 * 2)
            for i in range(20):
                raw[i * 20 * 2 + i * 2:i * 20 * 2 + i * 2 + 2] = (0x1000).to_bytes(2, "little")
            raw[8 * 2:9 * 2] = b"\0\0"
            return bytes(raw)
        if address == 0x083A4493:
            return bytes((0xCA, 0xE3, 0xE9, 0xE2, 0xD8, 0xFF)) + b"\0" * 7
        raise AssertionError((hex(address), length))


class RomDataTests(unittest.TestCase):
    def test_validates_and_caches_type_chart(self):
        fake = FakeROM()
        data = BattleRomData(fake)
        chart = data.type_chart()
        self.assertEqual(chart[0][0], 1.0)
        self.assertEqual(chart[0][8], 0.0)
        self.assertEqual(len([call for call in fake.calls if call[0] == 0x083ADEE0]), 1)

    def test_reads_move_names_from_fixed_slots(self):
        data = BattleRomData(FakeROM())
        self.assertEqual(data.move_name(1), "Pound")
        self.assertEqual(data.move(1).name, "Pound")

    def test_rejects_wrong_rom(self):
        class Wrong(FakeROM):
            def read_range(self, address, length):
                if address == 0x080000A0:
                    return b"WRONG ROM\x00\x00\x00\x00\x00\x00\x00"
                return super().read_range(address, length)

        with self.assertRaises(RomProfileError):
            BattleRomData(Wrong()).validate()
