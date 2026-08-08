import struct
import unittest

from games.run_and_bun.inventory import (
    FLAGS_LENGTH,
    POCKETS,
    SAVE_BLOCK1_PTR,
    SAVE_BLOCK2_ENCRYPTION_KEY_OFFSET,
    SAVE_BLOCK2_PTR,
    read_inventory,
    read_progress,
)


class FakeInventoryGBA:
    def __init__(self):
        self.save1 = 0x02010000
        self.save2 = 0x02011000
        self.key = 0x12341DAD
        self.block1 = bytearray(0x1600)
        self.block2 = bytearray(0xB0)
        self.block2[SAVE_BLOCK2_ENCRYPTION_KEY_OFFSET : SAVE_BLOCK2_ENCRYPTION_KEY_OFFSET + 4] = self.key.to_bytes(4, "little")
        for pocket_offset, count in POCKETS.values():
            for slot in range(count):
                self.block1[pocket_offset + slot * 4 + 2 : pocket_offset + slot * 4 + 4] = (self.key & 0xFFFF).to_bytes(2, "little")
        for slot in range(20):
            self.block1[0x4E8 + slot * 4 + 2 : 0x4E8 + slot * 4 + 4] = (self.key & 0xFFFF).to_bytes(2, "little")
        money = 199 ^ self.key
        self.block1[0x490 : 0x494] = money.to_bytes(4, "little")
        # One normal item and one berry-pocket entry.
        self.block1[0x560 : 0x564] = struct.pack("<HH", 13, 3 ^ (self.key & 0xFFFF))
        self.block1[0x4E8 : 0x4EC] = struct.pack("<HH", 1, 25 ^ (self.key & 0xFFFF))
        self.block1[0x740 + 25 * 4 : 0x740 + 25 * 4 + 4] = struct.pack("<HH", 1, 25 ^ (self.key & 0xFFFF))
        self.block1[0x900 : 0x904] = struct.pack("<HH", 520, 34 ^ (self.key & 0xFFFF))
        self.block1[0xA08 : 0xA0C] = struct.pack("<HH", 28, 1 ^ (self.key & 0xFFFF))
        self.block1[0x1220] = 0x05
        self.block1[0x1340 : 0x1342] = (7).to_bytes(2, "little")

    def read32(self, address):
        if address == SAVE_BLOCK1_PTR:
            return self.save1
        if address == SAVE_BLOCK2_PTR:
            return self.save2
        raise AssertionError(hex(address))

    def read_range(self, address, length):
        if address == self.save1 + 0x490:
            return bytes(self.block1[0x490 : 0x490 + length])
        if address == self.save1 + 0x560:
            return bytes(self.block1[0x560 : 0x560 + length])
        if address == self.save1 + 0x4E8:
            return bytes(self.block1[0x4E8 : 0x4E8 + length])
        if address == self.save1 + 0x1220:
            return bytes(self.block1[0x1220 : 0x1220 + length])
        if address == self.save1 + 0x1340:
            return bytes(self.block1[0x1340 : 0x1340 + length])
        if address == self.save2 + SAVE_BLOCK2_ENCRYPTION_KEY_OFFSET:
            return bytes(self.block2[SAVE_BLOCK2_ENCRYPTION_KEY_OFFSET : SAVE_BLOCK2_ENCRYPTION_KEY_OFFSET + length])
        raise AssertionError((hex(address), length))


class InventoryTests(unittest.TestCase):
    def test_decodes_encrypted_quantities_and_money(self):
        inventory = read_inventory(FakeInventoryGBA())
        self.assertEqual(inventory["money"], 199)
        self.assertEqual(inventory["pockets"]["items"], [{"slot": 0, "item_id": 13, "quantity": 3, "address": 0x02010560}])
        self.assertEqual(inventory["pockets"]["berries"][0]["quantity"], 25)
        self.assertEqual(inventory["pockets"]["runbun_berries"], [{"slot": 0, "item_id": 520, "quantity": 34, "address": 0x02010900}])
        self.assertEqual(inventory["pockets"]["runbun_medicine"], [{"slot": 2, "item_id": 28, "quantity": 1, "address": 0x02010A08}])
        self.assertEqual(inventory["pockets"]["ui_items"][0]["item_id"], 13)
        self.assertEqual(inventory["pockets"]["ui_medicine"][0]["item_id"], 28)
        self.assertEqual(inventory["pockets"]["live_items"], [{"slot": 0, "item_id": 1, "quantity": 25, "address": 0x020104E8}])

    def test_reads_flags_and_nonzero_vars(self):
        progress = read_progress(FakeInventoryGBA())
        self.assertEqual(progress["set_flag_ids"], [0, 2])
        self.assertEqual(progress["variables"], {0: 7})


if __name__ == "__main__":
    unittest.main()
