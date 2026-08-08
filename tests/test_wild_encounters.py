from __future__ import annotations

import struct
import unittest

from games.run_and_bun.wild_encounters import (
    SPECIES_NAME_COUNT, SPECIES_NAME_STRIDE, WILD_HEADERS_ADDRESS,
    WILD_HEADERS_SENTINEL, WILD_ROM_BLOCK_END, WILD_ROM_BLOCK_START,
    find_catch_locations, parse_primary_wild_encounters,
)


class WildEncounterTests(unittest.TestCase):
    def _fixture(self):
        blob = bytearray(WILD_ROM_BLOCK_END - WILD_ROM_BLOCK_START)
        header = WILD_HEADERS_ADDRESS - WILD_ROM_BLOCK_START
        info_address = WILD_ROM_BLOCK_START + 0x20
        slots_address = WILD_ROM_BLOCK_START + 0x40
        blob[header:header + 4] = bytes((24, 7, 0, 0))
        struct.pack_into("<4I", blob, header + 4, info_address, 0, 0, 0)
        info = info_address - WILD_ROM_BLOCK_START
        blob[info] = 10
        struct.pack_into("<I", blob, info + 4, slots_address)
        slots = slots_address - WILD_ROM_BLOCK_START
        species = (231, 231, 878, 878, 111, 111, 95, 304, 304, 777, 551, 551)
        for slot, species_id in enumerate(species):
            struct.pack_into("<BBH", blob, slots + slot * 4, 8, 8, species_id)
        sentinel = WILD_HEADERS_SENTINEL - WILD_ROM_BLOCK_START
        blob[sentinel:sentinel + 4] = b"\xff\xff\0\0"
        names = bytes(SPECIES_NAME_COUNT * SPECIES_NAME_STRIDE)
        return bytes(blob), names

    def test_parses_and_aggregates_duplicate_land_slots(self):
        encounters = parse_primary_wild_encounters(*self._fixture())
        results = find_catch_locations(
            encounters, species_id=551, visited_maps={(24, 7)}, visited_only=True
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["slots"], [10, 11])
        self.assertEqual(results[0]["slot_percent"], 2)
        self.assertTrue(results[0]["map"]["visited"])

    def test_map_query_lists_every_catchable_species(self):
        encounters = parse_primary_wild_encounters(*self._fixture())
        results = find_catch_locations(encounters, map_id=(24, 7))
        self.assertEqual(
            {item["species_id"] for item in results},
            {95, 111, 231, 304, 551, 777, 878},
        )


if __name__ == "__main__":
    unittest.main()
