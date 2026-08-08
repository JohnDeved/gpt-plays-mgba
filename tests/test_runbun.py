import unittest
from types import SimpleNamespace

from games.runbun import (
    BATTLE_MONS,
    BATTLE_MON_STRIDE,
    BATTLE_KO_FIELD_MESSAGE_MODES,
    FIELD_MESSAGE_MODE_NAMES,
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

    def sequence(self, steps, wait=True, timeout=5.0):
        self.sequence_calls.append(steps)
        return {"id": 8, "state": "done", "total_steps": len(steps)}

    def wait_frames(self, frames):
        return {"id": 9, "state": "done", "frames": frames}


class RunBunTests(unittest.TestCase):
    def test_battle_prompt_requires_command_text_not_page_control(self):
        self.assertFalse(RunBunAdapter._battle_command_prompt([{"text": "Chimchar used\nEmber!<0x70>"}]))
        self.assertTrue(RunBunAdapter._battle_command_prompt([{"text": "What will\nChimchar do?"}]))

    def test_battle_move_prompt_uses_type_and_pp_printer(self):
        self.assertTrue(RunBunAdapter._battle_move_prompt([{"text": "Type/Flying\nPP\n34/35"}]))
        self.assertFalse(RunBunAdapter._battle_move_prompt([{"text": "Foe Psyduck used\nBubble Beam!"}]))

    def test_battle_party_switch_prompt_is_distinct_from_move_text(self):
        self.assertTrue(RunBunAdapter._battle_party_switch_prompt([{"text": "Choose a Pokémon."}]))
        self.assertTrue(RunBunAdapter._battle_party_switch_prompt([{"text": "Use next Pokémon?"}]))
        self.assertFalse(RunBunAdapter._battle_party_switch_prompt([{"text": "Aaaa used\nGust!"}]))

    def test_field_mode_54_is_drained_as_post_ko_battle_text(self):
        self.assertIn(54, BATTLE_KO_FIELD_MESSAGE_MODES)

    def test_field_item_target_mode_is_named(self):
        self.assertEqual(FIELD_MESSAGE_MODE_NAMES[15], "field_item_target")

    def test_npc_interaction_gap_supports_counter_service_range(self):
        class FakeLiveMap:
            def __init__(self, walkable):
                self._walkable = set(walkable)

            def walkable(self, x, y):
                return (x, y) in self._walkable

        target = SimpleNamespace(current_x=7, current_y=2)
        live = FakeLiveMap({(7, 2), (7, 4)})

        self.assertEqual(
            RunBunAdapter._npc_interaction_gap((7, 4), target, live, max_gap=2),
            2,
        )
        self.assertEqual(
            RunBunAdapter._npc_interaction_gap((7, 3), target, live, max_gap=2),
            1,
        )

    def test_npc_interaction_gap_rejects_open_floor_range(self):
        class FakeLiveMap:
            def walkable(self, _x, _y):
                return True

        target = SimpleNamespace(current_x=7, current_y=2)
        self.assertIsNone(
            RunBunAdapter._npc_interaction_gap(
                (7, 4), target, FakeLiveMap(), max_gap=2
            )
        )

    def test_gen3_text_decoder_handles_dialogue_and_page_controls(self):
        raw = bytes((0xBB, 0xD7, 0xB8, 0xFA, 0xC1, 0xE3, 0xFF))
        self.assertEqual(decode_gen3_text(raw), "Ac,<PROMPT_SCROLL>Go")

    def test_battle_offsets_decode_little_endian_fields(self):
        raw = bytearray(BATTLE_MON_STRIDE)
        raw[0x00:0x02] = (987).to_bytes(2, "little")
        raw[0x02:0x04] = (12).to_bytes(2, "little")
        raw[0x0C:0x0E] = (33).to_bytes(2, "little")
        raw[0x18:0x20] = bytes((6, 5, 7, 6, 8, 6, 6, 6))
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
        self.assertEqual(mon.stat_stages, (6, 5, 7, 6, 8, 6, 6, 6))

    def test_adapter_reads_verified_pointers_and_structures(self):
        state = RunBunAdapter(FakeMGBA()).observe()

        self.assertEqual(state["frame"], 42)
        self.assertEqual(state["ui"]["yes_no"], 1)
        self.assertEqual(state["ui"]["field_message_box_mode_name"], "none")
        self.assertEqual(state["save"]["block1"]["x"], 5)
        self.assertEqual(state["save"]["block1"]["y"], 7)
        self.assertEqual(state["save"]["block1"]["map_group"], 2)
        self.assertEqual(state["map"], {"group": 2, "number": 3, "x": 5, "y": 7, "warp_id": 4})
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

    def test_battle_strategy_avoids_flash_fire(self):
        observation = {
            "battle": {
                "active": True,
                "mons": [
                    {
                        "slot": 0,
                        "present": True,
                        "state": {
                            "species": 390,
                            "current_hp": 32,
                            "max_hp": 32,
                            "moves": (10, 43, 52, 0),
                            "pp": (20, 10, 20, 0),
                        },
                    },
                    {
                        "slot": 1,
                        "present": True,
                        "state": {
                            "species": 850,
                            "current_hp": 20,
                            "max_hp": 20,
                            "types": (10, 6, 9),
                            "ability": 18,
                        },
                    },
                ],
            },
            "party": {"mons": []},
        }
        action = RunBunAdapter.choose_battle_action(observation)
        self.assertEqual(action["action"], "move")
        self.assertEqual(action["slot"], 0)

    def test_battle_strategy_prefers_mach_punch_over_neutral_scratch(self):
        observation = {
            "battle": {
                "active": True,
                "mons": [
                    {"slot": 0, "present": True, "state": {
                        "species": 390, "current_hp": 32, "max_hp": 32,
                        "types": (10, 10, 9), "moves": (10, 43, 52, 183),
                        "pp": (20, 10, 20, 30),
                    }},
                    {"slot": 1, "present": True, "state": {
                        "species": 54, "current_hp": 26, "max_hp": 26,
                        "types": (11, 11, 9), "ability": 0,
                    }},
                ],
            },
            "party": {"mons": []},
        }
        action = RunBunAdapter.choose_battle_action(observation)
        self.assertEqual(action["slot"], 3)

    def test_tactical_report_exposes_choice_and_uncertainty(self):
        observation = {
            "battle": {"active": True, "mons": [
                {"slot": 0, "present": True, "state": {
                    "species": 16, "current_hp": 12, "max_hp": 33,
                    "attack": 19, "defense": 15, "speed": 21,
                    "special_attack": 17, "special_defense": 16, "level": 12,
                    "types": (0, 2, 9), "moves": (16, 28, 33, 0),
                    "pp": (35, 5, 35, 0),
                }},
                {"slot": 1, "present": True, "state": {
                    "species": 193, "current_hp": 15, "max_hp": 33,
                    "attack": 19, "defense": 15, "speed": 26,
                    "special_attack": 18, "special_defense": 15, "level": 9,
                    "types": (6, 2, 9), "moves": (512, 49, 0, 0),
                    "pp": (20, 20, 0, 0),
                }},
            ]},
            "party": {"mons": []},
        }
        report = RunBunAdapter.explain_battle_action(observation)
        self.assertEqual(report["decision"]["action"], "move")
        self.assertEqual(report["decision"]["move_id"], 16)
        self.assertEqual(report["proof"]["level"], "best_estimate")
        self.assertEqual(report["chosen"]["move_id"], 16)
        self.assertTrue(report["proof"]["caveat"])

    def test_learned_damage_is_reported_as_bounds_not_a_forced_max_ko(self):
        observation = {
            "battle": {"active": True, "mons": [
                {"slot": 0, "present": True, "state": {
                    "species": 390, "current_hp": 32, "max_hp": 32,
                    "attack": 22, "defense": 14, "speed": 20,
                    "special_attack": 22, "special_defense": 20, "level": 12,
                    "types": (10, 10, 9), "moves": (10, 0, 0, 0), "pp": (10, 0, 0, 0),
                    "stat_stages": (6, 6, 6, 6, 6, 6, 6, 6),
                }},
                {"slot": 1, "present": True, "state": {
                    "species": 54, "current_hp": 19, "max_hp": 19,
                    "attack": 12, "defense": 12, "speed": 20,
                    "special_attack": 12, "special_defense": 12, "level": 10,
                    "types": (11, 11, 9), "moves": (0, 0, 0, 0), "pp": (0, 0, 0, 0),
                    "stat_stages": (6, 6, 6, 6, 6, 6, 6, 6),
                }},
            ]},
            "party": {"mons": []},
        }
        report = RunBunAdapter.explain_battle_action(
            observation,
            damage_memory={(390, 10, 54): [17, 18, 19]},
        )
        chosen = report["chosen"]
        self.assertEqual(chosen["damage_range"], [17.0, 19.0])
        self.assertEqual(chosen["guaranteed_ko_in"], 2)
        self.assertFalse(chosen["ko_before_hit"])
        self.assertEqual(chosen["order"], "tie")

    def test_stat_stage_and_speed_tie_are_not_treated_as_first(self):
        state = {"stat_stages": (6, 5, 6, 6, 6)}
        self.assertAlmostEqual(RunBunAdapter._stage_multiplier(state, "attack"), 2 / 3)

    def test_battle_strategy_keeps_a_finisher_against_faster_threat(self):
        observation = {
            "battle": {"active": True, "mons": [
                {"slot": 0, "present": True, "state": {
                    "species": 16, "current_hp": 23, "max_hp": 33,
                    "attack": 19, "defense": 15, "speed": 21,
                    "special_attack": 17, "special_defense": 16, "level": 12,
                    "types": (0, 2, 9), "moves": (16, 28, 33, 0), "pp": (35, 5, 35, 0),
                }},
                {"slot": 1, "present": True, "state": {
                    "species": 193, "current_hp": 15, "max_hp": 33,
                    "attack": 19, "defense": 15, "speed": 26,
                    "special_attack": 18, "special_defense": 15, "level": 9,
                    "types": (6, 2, 9), "moves": (512, 49, 0, 0), "pp": (20, 17, 0, 0),
                }},
            ]},
            "party": {"mons": []},
        }
        action = RunBunAdapter.choose_battle_action(observation)
        self.assertEqual(action["action"], "move")
        self.assertEqual(action["move_id"], 16)

    def test_battle_strategy_switches_to_matchup_before_forced_faint(self):
        seedot = {"slot": 0, "present": True, "state": {
            "species": 273, "current_hp": 10, "max_hp": 34,
            "attack": 16, "defense": 18, "speed": 10,
            "special_attack": 12, "special_defense": 14, "level": 12,
            "types": (12, 12, 9), "moves": (117, 267, 0, 0), "pp": (9, 20, 0, 0),
        }}
        pidgey = {"slot": 1, "present": True, "state": {
            "species": 16, "current_hp": 23, "max_hp": 33,
            "attack": 19, "defense": 15, "speed": 21,
            "special_attack": 17, "special_defense": 16, "level": 12,
            "types": (0, 2, 9), "moves": (16, 28, 33, 0), "pp": (35, 5, 35, 0),
        }}
        observation = {
            "battle": {"active": True, "mons": [seedot, {"slot": 1, "present": True, "state": {
                "species": 193, "current_hp": 15, "max_hp": 33,
                "attack": 19, "defense": 15, "speed": 26,
                "special_attack": 18, "special_defense": 15, "level": 9,
                "types": (6, 2, 9), "moves": (512, 49, 0, 0), "pp": (20, 17, 0, 0),
            }}]},
            "party": {"mons": [seedot, pidgey]},
        }
        action = RunBunAdapter.choose_battle_action(observation)
        self.assertEqual(action["action"], "switch")
        self.assertEqual(action["species"], 16)

    def test_battle_strategy_reuses_observed_super_effective_damage(self):
        seedot = {"slot": 0, "present": True, "state": {
            "species": 273, "current_hp": 10, "max_hp": 34,
            "attack": 16, "defense": 18, "speed": 10,
            "special_attack": 12, "special_defense": 14, "level": 12,
            "types": (12, 12, 9), "moves": (117, 267, 0, 0), "pp": (9, 20, 0, 0),
        }}
        chimchar = {"slot": 1, "present": True, "state": {
            "species": 390, "current_hp": 10, "max_hp": 32,
            "attack": 22, "defense": 14, "speed": 23,
            "special_attack": 22, "special_defense": 20, "level": 12,
            "types": (10, 10, 9), "moves": (10, 43, 52, 183), "pp": (35, 10, 25, 30),
        }}
        pidgey = {"slot": 2, "present": True, "state": {
            "species": 16, "current_hp": 33, "max_hp": 33,
            "attack": 19, "defense": 15, "speed": 21,
            "special_attack": 17, "special_defense": 16, "level": 12,
            "types": (0, 2, 9), "moves": (16, 28, 33, 0), "pp": (35, 5, 35, 0),
        }}
        krabby = {"slot": 1, "present": True, "state": {
            "species": 98, "current_hp": 7, "max_hp": 27,
            "attack": 26, "defense": 25, "speed": 14,
            "special_attack": 12, "special_defense": 12, "level": 9,
            "types": (11, 11, 9), "moves": (453, 23, 341, 0), "pp": (31, 15, 14, 0),
        }}
        observation = {"battle": {"active": True, "mons": [seedot, krabby]},
                       "party": {"mons": [seedot, chimchar, pidgey]}}
        action = RunBunAdapter.choose_battle_action(
            observation,
            damage_memory={(98, 453, 390): [19], (98, 23, 273): [10]},
        )
        self.assertEqual(action["action"], "switch")
        self.assertNotEqual(action.get("species"), 390)

    def test_battle_strategy_switches_from_critical_hp(self):
        observation = {
            "battle": {
                "active": True,
                "mons": [
                    {
                        "slot": 0,
                        "present": True,
                        "state": {
                            "species": 390,
                            "current_hp": 2,
                            "max_hp": 32,
                            "moves": (10, 43, 52, 0),
                            "pp": (20, 10, 20, 0),
                        },
                    },
                    {
                        "slot": 1,
                        "present": True,
                        "state": {
                            "species": 736,
                            "current_hp": 20,
                            "max_hp": 20,
                            "types": (6, 6, 9),
                            "ability": 0,
                        },
                    },
                ],
            },
            "party": {
                "mons": [
                    {"slot": 0, "present": True, "state": {"current_hp": 2, "level": 12}},
                    {"slot": 1, "present": True, "state": {"current_hp": 18, "level": 8}},
                ],
            },
        }
        action = RunBunAdapter.choose_battle_action(observation)
        self.assertEqual(action, {"action": "switch", "slot": 1, "reason": "active_hp_low"})


if __name__ == "__main__":
    unittest.main()
