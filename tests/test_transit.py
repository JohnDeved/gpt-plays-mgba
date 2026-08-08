import unittest

from games.run_and_bun.transit import is_transit_text


class TransitTests(unittest.TestCase):
    def test_voyage_text_is_classified_as_transit(self):
        self.assertTrue(is_transit_text("Anchors aweigh! Peeko, we're setting sail!"))
        self.assertTrue(is_transit_text("Ahoy! We've made land in Slateport!"))

    def test_unrelated_npc_text_is_not_classified_as_transit(self):
        self.assertFalse(is_transit_text("Dewford Town Pokémon Gym Leader Brawly"))
        self.assertFalse(is_transit_text("The doors are locked."))
        self.assertFalse(is_transit_text("For a guy as macho as me, a port is the perfect setting."))


if __name__ == "__main__":
    unittest.main()
