import struct
import unittest

from games.run_and_bun.transit import is_transit_text, script_transit_texts


class TransitTests(unittest.TestCase):
    def test_voyage_text_is_classified_as_transit(self):
        self.assertTrue(is_transit_text("Anchors aweigh! Peeko, we're setting sail!"))
        self.assertTrue(is_transit_text("Ahoy! We've made land in Slateport!"))

    def test_unrelated_npc_text_is_not_classified_as_transit(self):
        self.assertFalse(is_transit_text("Dewford Town Pokémon Gym Leader Brawly"))
        self.assertFalse(is_transit_text("The doors are locked."))
        self.assertFalse(is_transit_text("For a guy as macho as me, a port is the perfect setting."))
        self.assertFalse(is_transit_text("Wanted: A sailor capable of sailing in all currents."))

    def test_script_scan_respects_event_boundary(self):
        class ROM:
            def read_range(self, address, length):
                if address == 0x08001000:
                    return (struct.pack("<I", 0x08002000) + struct.pack("<I", 0x08003000))[:length]
                if address == 0x08002000:
                    return b"\xCE\xe3\xe8\x00\xd5\x00\xda\xd9\xe6\xe6\xed\xff" + bytes(length - 12)
                if address == 0x08003000:
                    return b"\xbb\xe2\xd7\xdc\xe3\xe6\xe7\x00\xd5\xeb\xd9\xdd\xdb\xdc\xff" + bytes(length - 15)
                raise AssertionError(hex(address))

        self.assertEqual(script_transit_texts(ROM(), 0x08001000, scan_bytes=4), [])
        self.assertEqual(
            script_transit_texts(ROM(), 0x08001000, scan_bytes=8),
            ["Anchors aweigh"],
        )


if __name__ == "__main__":
    unittest.main()
