from __future__ import annotations

import unittest

from games.run_and_bun.gavi_policy import GaviPolicy


class GaviPolicyTests(unittest.TestCase):
    @staticmethod
    def certificate(player, opponent, legal, *, forced=False):
        return {
            "state": {"player": player, "opponent": opponent},
            "boundary": {"party_switch_required": forced},
            "legal_actions": legal,
        }

    def test_normal_ohmnomnom_to_ponyta_switches_to_lildozer(self):
        policy = GaviPolicy()
        legal = [
            {"kind": "move", "slot": 0, "move_id": 252},
            {"kind": "move", "slot": 3, "move_id": 209},
            {"kind": "switch", "slot": 1, "species": 231, "hp": 58},
        ]
        certificate = self.certificate(
            {"species": 777, "hp": 37}, {"species": 77, "hp": 49}, legal,
        )
        self.assertEqual(policy.choose(certificate)["species"], 231)

    def test_bibarel_opens_with_one_razor_leaf_before_switching(self):
        policy = GaviPolicy()
        legal = [
            {"kind": "move", "slot": 1, "move_id": 75},
            {"kind": "switch", "slot": 2, "species": 777, "hp": 52},
        ]
        certificate = self.certificate(
            {"species": 388, "hp": 57}, {"species": 400, "hp": 56}, legal,
        )
        action = policy.choose(certificate)
        self.assertEqual(action["move_id"], 75)
        policy.record_verified(certificate, action)
        self.assertEqual(policy.choose(certificate)["species"], 777)

    def test_forced_ponyta_replacement_uses_ohmnomnom_when_lildozer_fainted(self):
        policy = GaviPolicy()
        legal = [
            {"kind": "switch", "slot": 2, "species": 777, "hp": 13},
            {"kind": "switch", "slot": 4, "species": 878, "hp": 56},
            {"kind": "switch", "slot": 5, "species": 453, "hp": 44},
        ]
        certificate = self.certificate(
            {"species": 231, "hp": 0}, {"species": 77, "hp": 5}, legal, forced=True,
        )
        self.assertEqual(policy.choose(certificate)["species"], 777)

    def test_fake_out_requires_fresh_verified_entry(self):
        policy = GaviPolicy(fresh_entry_species=777)
        legal = [
            {"kind": "move", "slot": 0, "move_id": 252},
            {"kind": "move", "slot": 3, "move_id": 209},
        ]
        certificate = self.certificate(
            {"species": 777, "hp": 37}, {"species": 77, "hp": 49}, legal,
        )
        self.assertEqual(policy.choose(certificate)["move_id"], 252)

    def test_low_hp_ohmnomnom_pivots_from_eelektrik_for_endgame(self):
        policy = GaviPolicy(eelektrik_nuzzled=True)
        legal = [
            {"kind": "move", "slot": 3, "move_id": 209},
            {"kind": "switch", "slot": 5, "species": 453, "hp": 36},
        ]
        certificate = self.certificate(
            {"species": 777, "hp": 3}, {"species": 603, "hp": 16}, legal,
        )
        self.assertEqual(policy.choose(certificate)["species"], 453)
        certificate["legal_actions"] = [action for action in legal if action.get("kind") == "move"]
        self.assertEqual(policy.choose(certificate)["move_id"], 209)

    def test_bushtank_takes_only_one_eelektrik_turn_then_returns_to_croakatoa(self):
        policy = GaviPolicy(eelektrik_bushtank_used=True)
        certificate = self.certificate(
            {"species": 388, "hp": 19}, {"species": 603, "hp": 37},
            [{"kind": "move", "slot": 1, "move_id": 75}, {"kind": "switch", "slot": 4, "species": 453, "hp": 44}],
        )
        self.assertEqual(policy.choose(certificate)["species"], 453)

    def test_rampage_is_a_dustox_only_fallback(self):
        policy = GaviPolicy()
        dustox = self.certificate(
            {"species": 231, "hp": 5}, {"species": 269, "hp": 29},
            [{"kind": "switch", "slot": 5, "species": 111, "hp": 59}],
        )
        self.assertEqual(policy.choose(dustox)["species"], 111)
        active = self.certificate(
            {"species": 111, "hp": 59}, {"species": 269, "hp": 29},
            [{"kind": "move", "slot": 1, "move_id": 479}],
        )
        self.assertEqual(policy.choose(active)["move_id"], 479)

    def test_bugatti_uses_controllable_dustox_pivot_before_rollout(self):
        policy = GaviPolicy()
        legal = [{"kind": "move", "slot": 0, "move_id": 205}, {"kind": "switch", "slot": 2, "species": 388, "hp": 26}, {"kind": "switch", "slot": 3, "species": 397, "hp": 46}]
        certificate = self.certificate(
            {"species": 543, "hp": 41}, {"species": 269, "hp": 25}, legal,
        )
        self.assertEqual(policy.choose(certificate)["species"], 397)
        legal.pop()
        self.assertEqual(policy.choose(certificate)["species"], 388)
        legal.pop()
        self.assertEqual(policy.choose(certificate)["move_id"], 205)

    def test_lildozer_finishes_low_dustox_without_switching(self):
        policy = GaviPolicy()
        legal = [
            {"kind": "move", "slot": 0, "move_id": 420},
            {"kind": "move", "slot": 1, "move_id": 523},
            {"kind": "switch", "slot": 5, "species": 543, "hp": 41},
        ]
        certificate = self.certificate(
            {"species": 231, "hp": 13}, {"species": 269, "hp": 6}, legal,
        )
        self.assertEqual(policy.choose(certificate)["move_id"], 420)

    def test_fresh_ohmnomnom_finishes_sunflora_with_fake_out_then_metal_claw(self):
        policy = GaviPolicy(fresh_entry_species=777)
        legal = [
            {"kind": "move", "slot": 0, "move_id": 252},
            {"kind": "move", "slot": 2, "move_id": 232},
        ]
        certificate = self.certificate(
            {"species": 777, "hp": 2}, {"species": 192, "hp": 24}, legal,
        )
        action = policy.choose(certificate)
        self.assertEqual(action["move_id"], 252)
        policy.record_verified(certificate, action)
        self.assertEqual(policy.choose(certificate)["move_id"], 232)

    def test_bugatti_is_the_primary_sunflora_counter(self):
        policy = GaviPolicy()
        forced = self.certificate(
            {"species": 388, "hp": 0}, {"species": 192, "hp": 54},
            [{"kind": "switch", "slot": 4, "species": 453, "hp": 22}, {"kind": "switch", "slot": 5, "species": 543, "hp": 41}],
            forced=True,
        )
        self.assertEqual(policy.choose(forced)["species"], 543)
        active = self.certificate(
            {"species": 543, "hp": 41}, {"species": 192, "hp": 54},
            [{"kind": "move", "slot": 3, "move_id": 342}],
        )
        self.assertEqual(policy.choose(active)["move_id"], 342)
        birdlaw = self.certificate(
            {"species": 397, "hp": 40}, {"species": 192, "hp": 54},
            [{"kind": "move", "slot": 3, "move_id": 332}, {"kind": "switch", "slot": 5, "species": 543, "hp": 41}],
        )
        self.assertEqual(policy.choose(birdlaw)["species"], 543)
        asleep = self.certificate(
            {"species": 543, "hp": 31, "status": 2}, {"species": 192, "hp": 28},
            [{"kind": "move", "slot": 3, "move_id": 342}, {"kind": "switch", "slot": 3, "species": 397, "hp": 46}],
        )
        self.assertEqual(policy.choose(asleep)["species"], 397)
        birdlaw = self.certificate(
            {"species": 397, "hp": 26, "status": 0}, {"species": 192, "hp": 23},
            [{"kind": "move", "slot": 3, "move_id": 332}, {"kind": "switch", "slot": 5, "species": 543, "hp": 18, "status": 4}],
        )
        self.assertEqual(policy.choose(birdlaw)["move_id"], 332)

    def test_birdlaw_priority_finishes_low_eelektrik(self):
        policy = GaviPolicy(eelektrik_nuzzled=True, eelektrik_bushtank_used=True)
        legal = [
            {"kind": "move", "slot": 2, "move_id": 98},
            {"kind": "move", "slot": 3, "move_id": 332},
            {"kind": "switch", "slot": 5, "species": 111, "hp": 59},
        ]
        certificate = self.certificate(
            {"species": 397, "hp": 46}, {"species": 603, "hp": 3}, legal,
        )
        self.assertEqual(policy.choose(certificate)["move_id"], 98)

    def test_low_hp_lildozer_pivots_to_ohmnomnom_instead_of_wasting_ice_shard(self):
        policy = GaviPolicy()
        legal = [
            {"kind": "move", "slot": 0, "move_id": 420},
            {"kind": "move", "slot": 1, "move_id": 523},
            {"kind": "switch", "slot": 2, "species": 777, "hp": 37},
        ]
        certificate = self.certificate(
            {"species": 231, "hp": 9}, {"species": 77, "hp": 13}, legal,
        )
        self.assertEqual(policy.choose(certificate)["species"], 777)
        certificate["state"]["opponent"]["hp"] = 3
        self.assertEqual(policy.choose(certificate)["move_id"], 420)

    def test_low_hp_lildozer_uses_birdlaw_when_ohmnomnom_has_fainted(self):
        policy = GaviPolicy()
        legal = [
            {"kind": "move", "slot": 0, "move_id": 420},
            {"kind": "move", "slot": 1, "move_id": 523},
            {"kind": "switch", "slot": 3, "species": 397, "hp": 46},
            {"kind": "switch", "slot": 4, "species": 453, "hp": 44},
        ]
        certificate = self.certificate(
            {"species": 231, "hp": 11}, {"species": 77, "hp": 5}, legal,
        )
        self.assertEqual(policy.choose(certificate)["species"], 397)
        certificate = self.certificate(
            {"species": 397, "hp": 28}, {"species": 77, "hp": 5},
            [{"kind": "move", "slot": 3, "move_id": 332}],
        )
        self.assertEqual(policy.choose(certificate)["move_id"], 332)

    def test_depleted_eelektrik_endgame_uses_priority_chip_without_exposing_rampage(self):
        policy = GaviPolicy(eelektrik_nuzzled=True, eelektrik_bushtank_used=True)
        phanpy = self.certificate(
            {"species": 231, "hp": 9}, {"species": 603, "hp": 17},
            [{"kind": "move", "slot": 0, "move_id": 420}, {"kind": "switch", "slot": 3, "species": 397, "hp": 27}, {"kind": "switch", "slot": 5, "species": 111, "hp": 59}],
        )
        self.assertEqual(policy.choose(phanpy)["move_id"], 420)
        birdlaw = self.certificate(
            {"species": 397, "hp": 27}, {"species": 603, "hp": 11},
            [{"kind": "move", "slot": 2, "move_id": 98}, {"kind": "switch", "slot": 5, "species": 111, "hp": 59}],
        )
        self.assertEqual(policy.choose(birdlaw)["move_id"], 98)

    def test_croakatoa_finishes_speed_lowered_low_hp_ponyta(self):
        policy = GaviPolicy()
        certificate = self.certificate(
            {"species": 453, "hp": 44}, {"species": 77, "hp": 6},
            [{"kind": "move", "slot": 0, "move_id": 341}, {"kind": "switch", "slot": 3, "species": 397, "hp": 46}],
        )
        self.assertEqual(policy.choose(certificate)["move_id"], 341)

    def test_double_ko_allows_sole_rampage_replacement_after_foe_is_dead(self):
        policy = GaviPolicy()
        certificate = self.certificate(
            {"species": 453, "hp": 0}, {"species": 603, "hp": 0},
            [{"kind": "switch", "slot": 5, "species": 111, "hp": 53}],
            forced=True,
        )
        self.assertEqual(policy.choose(certificate)["species"], 111)

    def test_coppertop_walls_eelektrik_and_sunflora(self):
        policy = GaviPolicy()
        for opponent in (603, 192):
            certificate = self.certificate(
                {"species": 878, "hp": 56, "status": 0}, {"species": opponent, "hp": 54},
                [{"kind": "move", "slot": 3, "move_id": 249}],
            )
            self.assertEqual(policy.choose(certificate)["move_id"], 249)

    def test_rampage_handles_ponyta_and_dustox(self):
        policy = GaviPolicy()
        for opponent in (77, 269):
            certificate = self.certificate(
                {"species": 111, "hp": 59, "status": 0}, {"species": opponent, "hp": 49},
                [{"kind": "move", "slot": 1, "move_id": 479}],
            )
            self.assertEqual(policy.choose(certificate)["move_id"], 479)


if __name__ == "__main__":
    unittest.main()
