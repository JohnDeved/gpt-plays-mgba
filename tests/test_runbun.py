import unittest
from types import SimpleNamespace

from games.runbun import (
    BATTLE_MONS,
    BATTLE_MON_STRIDE,
    BATTLE_KO_FIELD_MESSAGE_MODES,
    DOUBLE_BATTLE_FIELD_MESSAGE_MODES,
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
            "battle_command_cursor_1": 0,
            "battle_command_cursor_2": 1,
            "battle_command_cursor_3": 3,
            "battle_move_cursor": 3,
            "battle_move_cursor_1": 0,
            "battle_move_cursor_2": 1,
            "battle_move_cursor_3": 2,
            "battle_target": 255,
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
    @staticmethod
    def _capture_observation(*, target_hp=30, target_max_hp=30, moves=(44, 342, 0, 0)):
        return {
            "battle": {
                "active": True,
                "format": "single",
                "kind": "wild",
                "menu": {"state": "command_menu", "command_subject": "Grotle", "command_battler": None},
                "mons": [
                    {
                        "slot": 0,
                        "present": True,
                        "state": {
                            "species": 388,
                            "level": 17,
                            "current_hp": 57,
                            "max_hp": 57,
                            "attack": 32,
                            "defense": 32,
                            "speed": 24,
                            "special_attack": 28,
                            "special_defense": 30,
                            "types": [12, 12, 9],
                            "moves": list(moves),
                            "pp": [25, 25, 0, 0],
                            "stat_stages": [6] * 8,
                            "status": 0,
                        },
                    },
                    {
                        "slot": 1,
                        "present": True,
                        "state": {
                            "species": 95,
                            "level": 8,
                            "current_hp": target_hp,
                            "max_hp": target_max_hp,
                            "attack": 16,
                            "defense": 40,
                            "speed": 18,
                            "special_attack": 12,
                            "special_defense": 18,
                            "types": [5, 4, 9],
                            "moves": [33, 20, 88, 0],
                            "pp": [35, 20, 15, 0],
                            "stat_stages": [6] * 8,
                            "status": 0,
                        },
                    },
                ],
            },
            "party": {"mons": []},
        }

    def test_capture_certificate_weakens_only_with_strict_nonlethal_bound(self):
        report = RunBunAdapter.capture_decision_certificate(
            self._capture_observation(),
            poke_balls=24,
        )
        self.assertEqual(report["decision"]["kind"], "move")
        self.assertEqual(report["decision"]["move_id"], 44)
        self.assertTrue(report["decision"]["guaranteed_nonlethal"])
        self.assertLess(report["decision"]["critical_damage_max"], 30)

    def test_capture_certificate_treats_wild_printer_as_single_despite_stale_double_slots(self):
        observation = self._capture_observation()
        observation["battle"]["format"] = "double"
        report = RunBunAdapter.capture_decision_certificate(observation, poke_balls=24)
        self.assertEqual(report["decision"]["kind"], "move")

    def test_capture_certificate_treats_single_command_owner_as_stronger_than_stale_slots(self):
        observation = self._capture_observation()
        observation["battle"]["format"] = "double"
        observation["battle"]["kind"] = None
        report = RunBunAdapter.capture_decision_certificate(observation, poke_balls=24)
        self.assertEqual(report["decision"]["kind"], "move")

    def test_capture_certificate_excludes_residual_status_ko_risk(self):
        report = RunBunAdapter.capture_decision_certificate(
            self._capture_observation(moves=(342, 0, 0, 0)),
            poke_balls=24,
        )
        self.assertEqual(report["decision"], {"kind": "throw_ball", "button": "L"})
        self.assertTrue(report["legal_actions"]["moves"][0]["excluded_for_residual_risk"])

    def test_capture_certificate_throws_after_hp_threshold(self):
        report = RunBunAdapter.capture_decision_certificate(
            self._capture_observation(target_hp=9),
            poke_balls=24,
        )
        self.assertEqual(report["decision"], {"kind": "throw_ball", "button": "L"})

    def test_capture_certificate_surfaces_full_paralysis_action_failure(self):
        observation = self._capture_observation()
        observation["battle"]["mons"][0]["state"]["status"] = 0x40
        report = RunBunAdapter.capture_decision_certificate(observation, poke_balls=24)
        self.assertEqual(
            report["proof"]["material_uncertainty"]["full_paralysis"],
            "25% action-failure chance",
        )

    def test_capture_certificate_surfaces_accuracy_stage_cost(self):
        observation = self._capture_observation()
        observation["battle"]["mons"][0]["state"]["stat_stages"][6] = 4
        report = RunBunAdapter.capture_decision_certificate(observation, poke_balls=24)
        move = report["decision"]
        self.assertEqual(move["accuracy_stage"], 4)
        self.assertEqual(move["accuracy_multiplier"], 0.6)
        self.assertEqual(move["expected_damage_after_accuracy"], 5.7)
        self.assertEqual(report["proof"]["material_uncertainty"]["accuracy_stage"], 4)

    def test_battle_prompt_requires_command_text_not_page_control(self):
        self.assertFalse(RunBunAdapter._battle_command_prompt([{"text": "Chimchar used\nEmber!<0x70>"}]))
        self.assertTrue(RunBunAdapter._battle_command_prompt([{"text": "What will\nChimchar do?"}]))

    def test_battle_move_prompt_uses_type_and_pp_printer(self):
        self.assertTrue(RunBunAdapter._battle_move_prompt([{"text": "Type/Flying\nPP\n34/35"}]))
        self.assertTrue(RunBunAdapter._battle_move_prompt([{"text": "Type/Dark"}]))
        self.assertFalse(RunBunAdapter._battle_move_prompt([{"text": "Foe Psyduck used\nBubble Beam!"}]))

    def test_battle_move_prompt_details_decode_control_prefixed_type(self):
        details = RunBunAdapter._battle_move_prompt_details(
            [{"text": "<0x40>À   Type/<CTRL_06>Dark\nPP\n24/25"}]
        )
        self.assertEqual(details["type"], "Dark")
        self.assertEqual(details["pp"], 24)
        self.assertEqual(details["max_pp"], 25)

    def test_double_target_menu_is_distinct_from_move_menu(self):
        common = {
            "party_switch_prompt": False,
            "move_prompt": True,
            "command_prompt": False,
            "battle_active": True,
            "battle_format": "double",
        }
        self.assertEqual(
            RunBunAdapter._battle_menu_state(battle_target=255, **common),
            "move_menu",
        )
        self.assertEqual(
            RunBunAdapter._battle_menu_state(battle_target=1, **common),
            "target_menu",
        )

    def test_double_command_subject_and_cursor_path_are_deterministic(self):
        self.assertEqual(
            RunBunAdapter._battle_command_subject(
                [{"text": "<0xA0> What will\nVenipede do?"}]
            ),
            "Venipede",
        )
        self.assertEqual(RunBunAdapter._grid_cursor_keys(0, 3), ["DOWN", "RIGHT"])

    def test_battle_party_switch_prompt_is_distinct_from_move_text(self):
        self.assertTrue(RunBunAdapter._battle_party_switch_prompt([{"text": "Choose a Pokémon."}]))
        self.assertTrue(RunBunAdapter._battle_party_switch_prompt([{"text": "Use next Pokémon?"}]))
        self.assertFalse(RunBunAdapter._battle_party_switch_prompt([{"text": "Aaaa used\nGust!"}]))

    def test_field_mode_54_is_drained_as_post_ko_battle_text(self):
        self.assertIn(54, BATTLE_KO_FIELD_MESSAGE_MODES)

    def test_double_battle_field_modes_are_classified_as_battle_ui(self):
        for mode in (36, 44, 48, 52, 60, 68):
            self.assertIn(mode, BATTLE_KO_FIELD_MESSAGE_MODES)
        self.assertEqual(
            FIELD_MESSAGE_MODE_NAMES[36], "double_battle_party_transition"
        )
        self.assertEqual(FIELD_MESSAGE_MODE_NAMES[60], "double_battle_move")

    def test_stale_double_slots_do_not_promote_single_field_mode(self):
        self.assertEqual(RunBunAdapter._battle_format_for_field_mode(34), "single")
        self.assertEqual(RunBunAdapter._battle_format_for_field_mode(52), "double")
        self.assertIn(52, DOUBLE_BATTLE_FIELD_MESSAGE_MODES)

    def test_health_preflight_requires_every_present_mon_at_full_hp(self):
        observation = {
            "party": {
                "mons": [
                    {"present": True, "state": {"slot": 0, "species": 390, "current_hp": 32, "max_hp": 32}},
                    {"present": True, "state": {"slot": 1, "species": 16, "current_hp": 0, "max_hp": 33}},
                ]
            }
        }

        report = RunBunAdapter.health_preflight(observation)

        self.assertFalse(report["ready"])
        self.assertEqual(report["reason"], "healing_required")
        self.assertEqual(report["fainted"][0]["species"], 16)

    def test_health_preflight_accepts_full_party(self):
        observation = {
            "party": {
                "mons": [
                    {"present": True, "state": {"slot": 0, "species": 390, "current_hp": 32, "max_hp": 32}},
                    {"present": True, "state": {"slot": 1, "species": 16, "current_hp": 33, "max_hp": 33}},
                ]
            }
        }

        report = RunBunAdapter.health_preflight(observation)

        self.assertTrue(report["ready"])
        self.assertEqual(report["reason"], "ready")

    def test_field_item_target_mode_is_named(self):
        self.assertEqual(FIELD_MESSAGE_MODE_NAMES[15], "field_item_target")

    def test_candy_target_screen_modes_are_all_semantically_named(self):
        self.assertEqual(
            {FIELD_MESSAGE_MODE_NAMES[value] for value in (11, 13, 15, 19)},
            {"party_menu", "party_prompt", "field_item_target", "party_target_transition"},
        )

    def test_field_item_cleanup_stops_when_move_learning_owns_ui(self):
        self.assertTrue(RunBunAdapter._field_move_learning_pending({
            "ui": {"field_message_box_mode": 13},
            "text": {"battle_printers": [{"text": "Onix wants to learn the move Dragon Breath."}]},
        }))
        self.assertTrue(RunBunAdapter._field_move_learning_pending({
            "ui": {"field_message_box_mode": 33},
            "text": {},
        }))
        self.assertFalse(RunBunAdapter._field_move_learning_pending({
            "ui": {"field_message_box_mode": 0},
            "text": {"current": {"text": "Onix was elevated to Lv. 11."}},
        }))

    def test_start_menu_owner_is_not_mistaken_for_clean_overworld(self):
        gba = FakeMGBA()
        gba.inspect_tasks = lambda: {"tasks": [{
            "active": 1,
            "function_address": 0x080BD7B9,
        }]}
        self.assertTrue(RunBunAdapter(gba)._field_start_menu_open())

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
        self.assertEqual(state["ui"]["battle_target"], 255)
        self.assertEqual(state["ui"]["battle_command_cursors"], [2, 0, 1, 3])
        self.assertEqual(state["ui"]["battle_move_cursors"], [3, 0, 1, 2])
        self.assertEqual(state["ui"]["field_message_box_mode_name"], "none")
        self.assertEqual(state["save"]["block1"]["x"], 5)
        self.assertEqual(state["save"]["block1"]["y"], 7)
        self.assertEqual(state["save"]["block1"]["map_group"], 2)
        self.assertEqual(state["map"], {"group": 2, "number": 3, "x": 5, "y": 7, "warp_id": 4})
        self.assertEqual(state["party"]["count"], 1)
        self.assertEqual(len(state["battle"]["mons"]), 4)
        self.assertEqual(state["battle"]["format"], "single")
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
        # Legacy samples have no stage metadata; known move IDs use the
        # verified ROM calculation rather than treating those samples as a
        # standalone bound.
        self.assertEqual(chosen["damage_range"], [9.0, 11.0])
        self.assertEqual(chosen["guaranteed_ko_in"], 3)
        self.assertFalse(chosen["ko_before_hit"])
        self.assertEqual(chosen["order"], "tie")

    def test_super_fang_uses_fixed_half_current_hp_damage(self):
        attacker = {"state": {"species": 400}}
        defender = {"state": {"species": 388, "current_hp": 57}}
        self.assertEqual(RunBunAdapter._damage_bounds(162, attacker, defender), (28.0, 28.0))

    def test_single_type_battler_deduplicates_repeated_live_type_bytes(self):
        mon = {"state": {"species": 388, "types": (12, 12, 9)}}
        self.assertEqual(RunBunAdapter._mon_types(mon), (12,))

    def test_ground_is_neutral_into_water_for_bulldoze_estimate(self):
        attacker = {"state": {"species": 231, "level": 17, "attack": 24, "types": (4, 4, 9)}}
        defender = {"state": {"species": 400, "level": 16, "defense": 29, "types": (0, 11, 9)}}
        low, high = RunBunAdapter._damage_bounds(523, attacker, defender)
        self.assertLessEqual(high, 17.0)

    def test_levitate_blocks_ground_moves(self):
        attacker = {"state": {"species": 878, "level": 17, "attack": 33, "types": (8, 8, 9)}}
        defender = {"state": {"species": 603, "ability": 26, "current_hp": 54, "level": 17, "defense": 34, "types": (13, 13, 9)}}
        self.assertEqual(RunBunAdapter._damage_bounds(523, attacker, defender), (0.0, 0.0))

    def test_electric_is_ineffective_into_ground(self):
        attacker = {"state": {"species": 603, "level": 17, "special_attack": 35, "types": (13, 13, 9)}}
        defender = {"state": {"species": 95, "level": 17, "special_defense": 21, "types": (5, 4, 9)}}
        self.assertEqual(RunBunAdapter._damage_bounds(351, attacker, defender), (0.0, 0.0))

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
