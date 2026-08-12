import unittest
from types import SimpleNamespace
from unittest.mock import patch

from games.runbun import (
    BATTLE_MONS,
    BATTLE_MON_STRIDE,
    BATTLE_KO_FIELD_MESSAGE_MODES,
    DOUBLE_BATTLE_FIELD_MESSAGE_MODES,
    FIELD_MESSAGE_MODE_NAMES,
    GEN3_CHARSET,
    PLAYER_PARTY,
    RunBunAdapter,
    _berry_heal,
    _berry_triggers_after,
    decode_gen3_text,
    decode_text_observation,
    decode_battle_mon,
    decode_box_mon,
    nickname_target_prompt_verified,
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
    def test_trainer_review_gate_only_blocks_classified_hard_fights(self):
        adapter = RunBunAdapter.__new__(RunBunAdapter)
        observation = {
            "mode": "overworld",
            "battle": {"active": False},
            "party": {"mons": [{"state": {"species": 1, "current_hp": 10, "max_hp": 10}}]},
        }
        target = SimpleNamespace(map_id=(0, 24), local_id=2, graphics_id=21, script_address=0)
        easy = {
            "key": "map:0:24/local:2", "found": False, "trusted": False,
            "record": None,
        }
        with patch.object(adapter, "observe", return_value=observation), \
                patch.object(adapter, "inventory", return_value={"pockets": {}}), \
                patch("games.run_and_bun.trainer_database.lookup_trainer", return_value=easy), \
                patch("games.run_and_bun.trainer_database.is_classified_hard", return_value=False), \
                patch("games.run_and_bun.battle_review.battle_continuation_gate", return_value={"allowed": True}), \
                patch("games.run_and_bun.battle_review.clone_review_gate") as gate:
            report = adapter.trainer_preflight(target)
        self.assertTrue(report["ready"])
        self.assertEqual(report["reason"], "unclassified_trainer_allowed")
        gate.assert_not_called()

    def test_poke_ball_quantity_does_not_count_legacy_berry_entries(self):
        self.assertEqual(
            RunBunAdapter._poke_ball_quantity({
                "pockets": {
                    "poke_balls": [{"item_id": 1, "quantity": 2}],
                    "ui_poke_balls": [{"item_id": 1, "quantity": 3}],
                    "berries": [{"item_id": 1, "quantity": 13}],
                },
            }),
            5,
        )

    def test_nickname_keyboard_plan_uses_shortest_uppercase_grid_path(self):
        name, keys = RunBunAdapter._nickname_keyboard_plan("OhmNomNom")
        self.assertEqual(name, "Ohmnomnom")
        self.assertEqual(keys[-2:], ["START", "A"])
        self.assertEqual(keys.count("A"), len(name) + 1)
        self.assertNotIn("SELECT", keys)

        _, bush_keys = RunBunAdapter._nickname_keyboard_plan("BushTank")
        self.assertIn(["UP", "LEFT", "LEFT", "LEFT", "LEFT", "A"], [bush_keys[i:i + 6] for i in range(len(bush_keys) - 5)])

        with self.assertRaisesRegex(ValueError, "1..10 ASCII letters"):
            RunBunAdapter._nickname_keyboard_plan("TOO-LONG-NAME")

    def test_capture_nickname_prompt_uses_current_field_text_fallback(self):
        self.assertEqual(
            RunBunAdapter._active_field_page_texts({
                "text": {"battle_printers": [], "current": {"text": "Give a nickname?"}}
            }),
            ("Give a nickname?",),
        )

    def test_nickname_target_prompt_accepts_printer_truncation(self):
        self.assertTrue(nickname_target_prompt_verified("Which Pokémon's nickname should\nI chang"))
        self.assertTrue(nickname_target_prompt_verified("Which Pokémon's nickname should\nI chan"))
        self.assertTrue(nickname_target_prompt_verified("Which Pokémon's nickname should I change?"))
        self.assertFalse(nickname_target_prompt_verified("Which Pokémon should I release?"))

    def test_ball_hotkey_closes_move_menu_before_throwing(self):
        class Gba:
            def __init__(self):
                self.presses = []

            def press(self, key, frames=3):
                self.presses.append((key, frames))

            @staticmethod
            def wait_frames(_frames):
                return None

        gba = Gba()
        adapter = RunBunAdapter(gba)
        states = iter([
            {"battle": {"menu": {"state": "move_menu"}}},
            {"battle": {"menu": {"state": "command_menu"}}},
        ])
        adapter.observe = lambda: next(states)
        adapter._pokemon_storage_digest = lambda: "unchanged"
        adapter.inventory = lambda: {}
        adapter._poke_ball_quantity = lambda _inventory: 0
        state = SimpleNamespace(party_count=lambda: 0)
        with patch("games.run_and_bun.state.RunBun", return_value=state):
            with self.assertRaisesRegex(RuntimeError, "no_poke_balls"):
                adapter.throw_poke_ball_hotkey(nickname="Croakatoa")
        self.assertEqual(gba.presses, [("B", 3)])

    def test_party_grid_uses_verified_vertical_cursor_ring(self):
        self.assertEqual(RunBunAdapter._party_grid_keys(0, 2), ["DOWN", "DOWN"])
        self.assertEqual(RunBunAdapter._party_grid_keys(5, 0), ["DOWN", "DOWN"])
        self.assertEqual(RunBunAdapter._party_switch_row(4), 1)
        self.assertEqual(RunBunAdapter._party_switch_row(5), 2)

    def test_selected_trainer_can_be_excluded_from_sight_blocking(self):
        event = SimpleNamespace(
            local_id=4, trainer_type=1, current_x=2, current_y=2,
            trainer_sight_radius=3, position=(2, 2), movement_type=8,
        )
        obj = SimpleNamespace(
            local_id=4, trainer_type=1, is_player=False, map_id=(0, 1),
            position=(2, 2), current_x=2, current_y=2,
            trainer_range_or_berry_id=3, facing_direction=1,
        )
        with patch("games.run_and_bun.objects.read_live_event_targets", return_value=[event]), patch(
            "games.run_and_bun.objects.read_live_objects", return_value=[obj]
        ):
            self.assertTrue(RunBunAdapter._trainer_sight_tiles(None, (0, 1)))
            self.assertEqual(
                RunBunAdapter._trainer_sight_tiles(None, (0, 1), ignored_local_ids={4}),
                set(),
            )

    def test_route_gate_ignores_verified_defeated_trainers(self):
        adapter = RunBunAdapter.__new__(RunBunAdapter)
        adapter.gba = object()
        adapter.enforce_live_trainer_gate = True
        adapter.observe = lambda: {
            "mode": "overworld",
            "battle": {"active": False},
            "map": {"group": 0, "number": 24, "x": 1, "y": 2},
        }
        event = SimpleNamespace(
            local_id=7, trainer_type=1, map_id=(0, 24), current_x=2, current_y=2,
            position=(2, 2), trainer_sight_radius=3, movement_type=8,
        )
        live = SimpleNamespace(
            local_id=7, trainer_type=1, map_id=(0, 24), current_x=2, current_y=2,
            position=(2, 2), trainer_range_or_berry_id=3, facing_direction=3,
            active=True, invisible=False, is_player=False,
        )
        with patch("games.run_and_bun.objects.read_live_event_targets", return_value=[event]), \
                patch("games.run_and_bun.objects.read_live_objects", return_value=[live]), \
                patch.object(adapter, "trainer_preflight", return_value={"ready": False}):
            self.assertFalse(adapter._trainer_route_gate(["RIGHT"])["allowed"])
            self.assertTrue(
                adapter._trainer_route_gate(
                    ["RIGHT"], verified_defeated_trainer_local_ids={7}
                )["allowed"]
            )

    def test_route_gate_allows_explicit_damaged_crossing_only_for_known_easy_trainer(self):
        adapter = RunBunAdapter.__new__(RunBunAdapter)
        adapter.gba = object()
        adapter.enforce_live_trainer_gate = True
        adapter.observe = lambda: {
            "mode": "overworld",
            "battle": {"active": False},
            "map": {"group": 0, "number": 24, "x": 1, "y": 2},
        }
        event = SimpleNamespace(
            local_id=7, trainer_type=1, map_id=(0, 24), current_x=2, current_y=2,
            position=(2, 2), trainer_sight_radius=3, movement_type=8,
        )
        live = SimpleNamespace(
            local_id=7, trainer_type=1, map_id=(0, 24), current_x=2, current_y=2,
            position=(2, 2), trainer_range_or_berry_id=3, facing_direction=3,
            active=True, invisible=False, is_player=False,
        )
        report = {
            "ready": False, "reason": "healing_required", "classification": "easy",
            "trainer": {"found": True, "trusted": True},
        }
        with patch("games.run_and_bun.objects.read_live_event_targets", return_value=[event]), \
                patch("games.run_and_bun.objects.read_live_objects", return_value=[live]), \
                patch.object(adapter, "trainer_preflight", return_value=report):
            self.assertFalse(adapter._trainer_route_gate(["RIGHT"])['allowed'])
            allowed = adapter._trainer_route_gate(
                ["RIGHT"], allow_damaged_trainer_sight_lines=True
            )
        self.assertTrue(allowed["allowed"])
        self.assertEqual(
            allowed["preflights"]["7"]["reason"],
            "damaged_trainer_sightline_explicitly_allowed",
        )

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

    def test_capture_certificate_throws_when_no_further_crit_safe_damage_exists(self):
        report = RunBunAdapter.capture_decision_certificate(
            self._capture_observation(target_hp=9),
            poke_balls=24,
        )
        self.assertEqual(report["decision"], {"kind": "throw_ball", "button": "L"})

    def test_capture_certificate_keeps_weakening_below_old_hp_threshold(self):
        observation = self._capture_observation(target_hp=10, moves=(999, 0, 0, 0))
        observation["battle"]["mons"][1]["state"]["types"] = [0, 0, 9]
        move = SimpleNamespace(
            name="Nibble", power=1, type_id=0, category="physical", secondary_chance=0,
        )
        report = RunBunAdapter.capture_decision_certificate(
            observation, poke_balls=24, move_data={999: move}, type_chart={},
        )
        self.assertEqual(report["decision"]["kind"], "move")
        self.assertTrue(report["decision"]["guaranteed_nonlethal"])

    def test_capture_certificate_sets_status_after_lowest_safe_hp(self):
        observation = self._capture_observation(target_hp=1, moves=(86, 0, 0, 0))
        observation["battle"]["mons"][1]["state"]["types"] = [0, 0, 9]
        move = SimpleNamespace(
            name="Thunder Wave", power=0, type_id=13, category="status", secondary_chance=0,
        )
        report = RunBunAdapter.capture_decision_certificate(
            observation, poke_balls=24, move_data={86: move}, type_chart={},
        )
        self.assertEqual(report["decision"]["capture_status"], "paralysis")
        self.assertEqual(report["proof"]["level"], "expected-best")

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
        self.assertEqual(move["expected_damage_after_accuracy"], 4.8)
        self.assertEqual(report["proof"]["material_uncertainty"]["accuracy_stage"], 4)

    def test_battle_prompt_requires_command_text_not_page_control(self):
        self.assertFalse(RunBunAdapter._battle_command_prompt([{"text": "Chimchar used\nEmber!<0x70>"}]))
        self.assertTrue(RunBunAdapter._battle_command_prompt([{"text": "What will\nChimchar do?"}]))

    def test_battle_printer_decodes_string_ending_at_cursor(self):
        inverse = {value: key for key, value in GEN3_CHARSET.items()}
        encode = lambda value: bytes(inverse[char] for char in value)
        raw = encode("old") + b"\xff" + encode("What will A do?") + b"\xff" + encode("stale") + b"\xff"
        cursor = raw.index(encode("stale"))
        self.assertEqual(RunBunAdapter._current_printer_text(raw, cursor), "What will A do?")

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

    def test_fainted_active_cannot_be_a_command_boundary(self):
        battle = {"mons": [{"slot": 0, "present": True, "state": {"current_hp": 0}}]}
        self.assertTrue(RunBunAdapter._active_player_fainted(battle))
        battle["mons"][0]["state"]["current_hp"] = 1
        self.assertFalse(RunBunAdapter._active_player_fainted(battle))

    def test_command_boundary_waits_through_delayed_residual_hp(self):
        class Gba:
            ticks = 0

            def wait_frames(self, _frames):
                self.ticks += 1

        gba = Gba()
        adapter = RunBunAdapter(gba)

        def observe():
            hp = 10 if gba.ticks < 3 else 5
            return {
                "battle": {
                    "active": True,
                    "menu": {"state": "command_menu"},
                    "mons": [
                        {"slot": 0, "present": True, "state": {"species": 1, "current_hp": hp, "pp": (5,)}},
                        {"slot": 1, "present": True, "state": {"species": 2, "current_hp": 20, "pp": (5,)}},
                    ],
                },
                "text": {"active": False, "battle_printers": []},
                "ui": {"field_message_box_mode": 34},
            }

        adapter.observe = observe
        result = adapter.advance_battle_until_menu(
            after_action=True,
            pre_action_signature=((0, 1, 20, (6,)),),
        )
        self.assertEqual(result["state"], "command_menu")
        self.assertGreaterEqual(gba.ticks, 8)

    def test_inactive_battle_is_terminal_only_after_overworld_returns(self):
        self.assertFalse(RunBunAdapter._inactive_battle_is_terminal({"mode": "dialogue"}))
        self.assertTrue(RunBunAdapter._inactive_battle_is_terminal({"mode": "overworld"}))

    def test_completed_battle_text_is_ready_even_while_printer_is_active(self):
        self.assertTrue(RunBunAdapter._battle_text_ready({"active": True, "current": {"state": 1}}))
        self.assertFalse(RunBunAdapter._battle_text_ready({"active": True, "current": {"state": 0}}))
        self.assertTrue(RunBunAdapter._battle_text_ready({"current": {"state": 0}, "printers": [{"active": 1, "state": 2}]}))

    def test_stale_text_cannot_press_into_returned_command_menu(self):
        class Gba:
            presses = 0

            def press(self, key, frames=2):
                self.assert_key = key
                self.assert_frames = frames
                self.presses += 1

            def wait_frames(self, _frames):
                pass

        gba = Gba()
        adapter = RunBunAdapter(gba)

        def observe():
            return {
                "mode": "dialogue",
                "battle": {
                    "active": True,
                    "menu": {"state": "none" if gba.presses == 0 else "command_menu"},
                    "mons": [
                        {"slot": 0, "present": True, "state": {"species": 1, "current_hp": 10, "pp": (5,)}},
                        {"slot": 1, "present": True, "state": {"species": 2, "current_hp": 20, "pp": (5,)}},
                    ],
                },
                "text": {"active": True, "current": {"state": 1, "start": 1, "end": 2, "cursor": 2, "text": "Foe used a move"}, "battle_printers": []},
                "ui": {"field_message_box_mode": 34},
            }

        adapter.observe = observe
        self.assertEqual(adapter.advance_battle_until_menu(sample_frames=1, max_frames=20)["state"], "command_menu")
        self.assertEqual(gba.presses, 1)
        self.assertEqual(gba.assert_key, "A")
        self.assertEqual(gba.assert_frames, 1)

    def test_identical_locked_move_text_advances_after_hp_progress(self):
        class Gba:
            presses = 0

            def press(self, _key, frames=1):
                self.presses += 1

            def wait_frames(self, _frames):
                pass

        gba = Gba()
        adapter = RunBunAdapter(gba)

        def observe():
            return {
                "mode": "dialogue",
                "battle": {
                    "active": True,
                    "menu": {"state": "command_menu" if gba.presses >= 2 else "battle_text"},
                    "mons": [
                        {"slot": 0, "present": True, "state": {"species": 1, "current_hp": 10, "pp": (4,)}},
                        {"slot": 1, "present": True, "state": {"species": 2, "current_hp": 20 - gba.presses * 5, "pp": (5,)}},
                    ],
                },
                "text": {
                    "active": True,
                    "current": {"state": 1, "start": 1, "end": 2, "cursor": 2, "text": "Used Rollout!"},
                    "battle_printers": [{"printer_address": 1, "current_char": 2, "text": "Used Rollout!"}],
                },
                "ui": {"field_message_box_mode": 34},
            }

        adapter.observe = observe
        result = adapter.advance_battle_until_menu(sample_frames=1, max_frames=30, after_action=True, pre_action_signature=((0, 1, 10, (5,)),))
        self.assertEqual(result["state"], "command_menu")
        self.assertEqual(gba.presses, 2)

    def test_field_mode_54_is_drained_as_post_ko_battle_text(self):
        self.assertIn(54, BATTLE_KO_FIELD_MESSAGE_MODES)

    def test_single_battle_move_field_mode_is_active_battle_ui(self):
        self.assertIn(45, BATTLE_KO_FIELD_MESSAGE_MODES)
        self.assertEqual(FIELD_MESSAGE_MODE_NAMES[45], "battle_move")

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
        self.assertEqual(
            RunBunAdapter._move_learning_page_texts({
                "text": {"battle_printers": [{"text": "However, Onix already\nknows four moves."}]},
            }),
            ("However, Onix already knows four moves.",),
        )
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

    def test_bag_task_is_detected_from_cursor_payload(self):
        self.assertTrue(RunBunAdapter._is_field_bag_task({
            "active": 1,
            "data": [10876, 512, 51237, 2077, 51445, 2077, 4, 4, 0, 8, 12305, 1792, 0, 3],
        }))
        self.assertTrue(RunBunAdapter._is_field_bag_task({
            "active": 1,
            "data": [10876, 512, 51237, 2077, 51445, 2077, 5, 5, 0, 8, 12305, 1792, 0, 4],
        }))
        self.assertFalse(RunBunAdapter._is_field_bag_task({"active": 1, "data": [0] * 16}))

    def test_follow_route_rejects_hidden_bag_task(self):
        fake = FakeMGBA()
        adapter = RunBunAdapter(fake, enforce_live_trainer_gate=False)
        adapter.observe = lambda: {
            "mode": "overworld",
            "battle": {"active": False},
            "ui": {"field_bag_open": True, "field_start_menu_open": False},
        }
        with self.assertRaisesRegex(RuntimeError, "field UI is active"):
            adapter.follow_route(["RIGHT"])

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
        self.assertIsNone(
            RunBunAdapter._npc_interaction_gap(
                (5, 2), target, FakeLiveMap({(5, 2), (7, 2)}), max_gap=2
            )
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
        self.assertEqual(
            RunBunAdapter._npc_interaction_gap(
                (7, 0), target, FakeLiveMap(), max_gap=2, allow_open_gap=True
            ),
            2,
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

    def test_box_decoder_reuses_verified_encrypted_prefix(self):
        mon = decode_box_mon(bytes(80))
        self.assertEqual(mon["species"], 0)
        self.assertTrue(mon["checksum"]["valid"])
        self.assertNotIn("current_hp", mon)

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
        adapter = RunBunAdapter(fake, enforce_live_trainer_gate=False)

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
        adapter = RunBunAdapter(fake, enforce_live_trainer_gate=False)

        adapter.follow_route(["RIGHT", "RIGHT", "RIGHT"], settle_frames=8)

        self.assertEqual(
            fake.sequence_calls[0],
            [
                {"keys": ["RIGHT"], "frames": 44},
                {"keys": [], "frames": 8},
            ],
        )

    def test_npc_path_preflights_full_route_before_first_chunk(self):
        adapter = RunBunAdapter.__new__(RunBunAdapter)
        adapter.gba = FakeMGBA()
        adapter.enforce_live_trainer_gate = True
        adapter.observe = lambda: {
            "mode": "overworld",
            "battle": {"active": False},
            "map": {"group": 0, "number": 24, "x": 1, "y": 1},
        }
        target = SimpleNamespace(
            local_id=2,
            graphics_id=21,
            trainer_type=0,
            current_x=3,
            current_y=1,
            facing_direction=0,
            as_dict=lambda: {"local_id": 2},
        )
        gate_calls = []
        approach_calls = []
        adapter.trainer_preflight = lambda _target: {"ready": True}
        adapter._npc_approach_target = lambda *args, **kwargs: (
            approach_calls.append(kwargs) or ((1, 1), ["RIGHT", "RIGHT"], 1)
        )
        adapter.follow_route = lambda *args, **kwargs: self.fail(
            "movement must not start after a rejected full-path preflight"
        )
        adapter._trainer_route_gate = lambda directions, **kwargs: (
            gate_calls.append(list(directions)) or {"allowed": False, "reason": "test"}
        )
        with patch("games.run_and_bun.objects.read_live_objects", return_value=[target]), \
                patch("games.run_and_bun.objects.select_live_object", return_value=target), \
                patch("games.run_and_bun.live_map.read_live_map", return_value=object()):
            with self.assertRaisesRegex(RuntimeError, "trainer_engagement_blocked"):
                adapter.follow_live_path_to_npc(
                    local_id=2, verified_defeated_trainer_local_ids={4, 6},
                )
        self.assertEqual(gate_calls, [["RIGHT", "RIGHT"]])
        self.assertEqual(approach_calls[0]["ignored_trainer_ids"], {4, 6})

    def test_adaptive_path_preflights_full_route_before_first_chunk(self):
        adapter = RunBunAdapter.__new__(RunBunAdapter)
        adapter.gba = FakeMGBA()
        adapter.enforce_live_trainer_gate = True
        adapter.observe = lambda: {
            "mode": "overworld",
            "battle": {"active": False},
            "map": {"group": 0, "number": 21, "x": 1, "y": 1},
        }
        class FakeLiveMap:
            active_width = 80
            active_height = 20

            @staticmethod
            def walkable(_x, _y):
                return True

            @staticmethod
            def path_to(*_args, **_kwargs):
                return ["RIGHT", "RIGHT"]

        gate_calls = []
        adapter._trainer_route_gate = lambda directions, **kwargs: (
            gate_calls.append(list(directions)) or {"allowed": False, "reason": "test"}
        )
        adapter.follow_route = lambda *args, **kwargs: self.fail(
            "adaptive movement must not start after a rejected full-path preflight"
        )
        with patch("games.run_and_bun.live_map.read_live_map", return_value=FakeLiveMap()), \
                patch("games.run_and_bun.objects.read_live_objects", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "trainer_engagement_blocked"):
                adapter.follow_live_path_adaptive(
                    (3, 1), expected_map=(0, 21), avoid_trainer_sight_lines=False
                )
        self.assertEqual(gate_calls, [["RIGHT", "RIGHT"]])

    def test_adaptive_path_stops_when_script_lock_blocks_every_direction(self):
        adapter = RunBunAdapter.__new__(RunBunAdapter)
        adapter.gba = FakeMGBA()
        adapter.enforce_live_trainer_gate = False
        state = {
            "mode": "overworld", "battle": {"active": False},
            "map": {"group": 9, "number": 8, "x": 12, "y": 6},
        }
        adapter.observe = lambda: state
        adapter.follow_route = lambda *_args, **_kwargs: {"action": {}, "state": state}

        class FakeLiveMap:
            @staticmethod
            def walkable(_x, _y):
                return True

            directions = iter(("RIGHT", "UP", "DOWN", "LEFT"))

            @classmethod
            def path_to(cls, *_args, **_kwargs):
                return [next(cls.directions)]

        with patch("games.run_and_bun.live_map.read_live_map", return_value=FakeLiveMap()), patch(
            "games.run_and_bun.objects.read_live_objects", return_value=[]
        ):
            with self.assertRaisesRegex(RuntimeError, "made no progress.*control may be locked"):
                adapter.follow_live_path_adaptive(
                    (6, 2), expected_map=(9, 8), avoid_trainer_sight_lines=False,
                    blocked_wait_frames=0, max_replans=4,
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
        self.assertEqual(action["action"], "move", action)
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
        self.assertEqual(report["proof"]["level"], "expected_best")
        self.assertEqual(report["chosen"]["move_id"], 16)
        self.assertTrue(report["proof"]["caveat"])

    def test_single_tactical_report_ignores_stale_double_battle_opponent(self):
        observation = {
            "battle": {"active": True, "format": "single", "mons": [
                {"slot": 0, "present": True, "state": {
                    "species": 1, "current_hp": 20, "max_hp": 20, "level": 10,
                    "attack": 20, "defense": 20, "speed": 20, "special_attack": 20, "special_defense": 20,
                    "types": (0,), "moves": (10, 0, 0, 0), "pp": (10, 0, 0, 0),
                }},
                {"slot": 1, "present": True, "state": {
                    "species": 2, "current_hp": 0, "max_hp": 20, "level": 10,
                    "attack": 20, "defense": 20, "speed": 20, "special_attack": 20, "special_defense": 20,
                    "types": (0,), "moves": (10, 0, 0, 0), "pp": (10, 0, 0, 0),
                }},
                {"slot": 3, "present": True, "state": {
                    "species": 404, "current_hp": 20, "max_hp": 20, "level": 10,
                    "attack": 20, "defense": 20, "speed": 20, "special_attack": 20, "special_defense": 20,
                    "types": (13,), "moves": (209, 0, 0, 0), "pp": (10, 0, 0, 0),
                }},
            ]},
            "party": {"mons": []},
        }
        report = RunBunAdapter.explain_battle_action(observation)
        self.assertEqual(report["state"]["opponent"]["species"], 2)

    def test_tactical_report_models_accuracy_priority_and_outcome_set(self):
        observation = {
            "battle": {"active": True, "format": "single", "mons": [
                {"slot": 0, "present": True, "state": {
                    "species": 1, "current_hp": 30, "max_hp": 30,
                    "attack": 30, "defense": 20, "speed": 50,
                    "special_attack": 20, "special_defense": 20, "level": 20,
                    "types": (0,), "moves": (10, 0, 0, 0), "pp": (10, 0, 0, 0),
                    "stat_stages": (6,) * 8,
                }},
                {"slot": 1, "present": True, "state": {
                    "species": 2, "current_hp": 12, "max_hp": 30,
                    "attack": 20, "defense": 20, "speed": 10,
                    "special_attack": 20, "special_defense": 20, "level": 20,
                    "types": (0,), "moves": (20, 0, 0, 0), "pp": (10, 0, 0, 0),
                    "stat_stages": (6,) * 8,
                }},
            ]},
            "party": {"mons": []},
        }
        move = lambda move_id, accuracy, priority: SimpleNamespace(
            move_id=move_id, name=str(move_id), power=80, type_id=0,
            type_name="Normal", accuracy=accuracy, pp=10,
            secondary_chance=0, target_flags=0, priority=priority,
            category="physical",
        )
        report = RunBunAdapter.explain_battle_action(
            observation,
            move_data={10: move(10, 50, 0), 20: move(20, 100, 1)},
        )
        chosen = report["chosen"]
        self.assertEqual(chosen["hit_probability"], 0.5)
        self.assertFalse(chosen["guaranteed_hit"])
        self.assertFalse(chosen["ko_before_hit"])
        self.assertEqual(chosen["order"], "second")
        self.assertEqual(chosen["outcome_set"]["miss_probability"], 0.5)

    def test_guaranteed_priority_ko_beats_slower_higher_damage_ko(self):
        observation = {
            "battle": {"active": True, "format": "single", "mons": [
                {"slot": 0, "present": True, "state": {
                    "species": 231, "current_hp": 54, "max_hp": 58, "level": 17,
                    "attack": 24, "defense": 33, "speed": 22,
                    "special_attack": 19, "special_defense": 20,
                    "types": (4,), "moves": (420, 88, 0, 0), "pp": (30, 13, 0, 0),
                    "stat_stages": (6,) * 8,
                }},
                {"slot": 1, "present": True, "state": {
                    "species": 544, "current_hp": 3, "max_hp": 43, "level": 16,
                    "attack": 27, "defense": 41, "speed": 77,
                    "special_attack": 22, "special_defense": 38,
                    "types": (6, 3), "moves": (474, 0, 0, 0), "pp": (16, 0, 0, 0),
                    "stat_stages": (6,) * 8,
                }},
            ]},
            "party": {"mons": []},
        }
        move = lambda move_id, power, move_type, priority=0: SimpleNamespace(
            move_id=move_id, name=str(move_id), power=power, type_id=move_type,
            type_name=str(move_type), accuracy=100, pp=10, secondary_chance=0,
            target_flags=0, priority=priority, category="physical",
        )
        report = RunBunAdapter.explain_battle_action(
            observation,
            move_data={
                420: move(420, 40, 15, 1),
                88: move(88, 50, 5),
                474: move(474, 65, 3),
            },
        )
        self.assertEqual(report["decision"]["move_id"], 420)
        self.assertTrue(report["chosen"]["ko_before_hit"])

    def test_tactical_report_lists_unmodeled_mechanics_in_one_coverage_envelope(self):
        observation = {
            "battle": {"active": True, "format": "single", "mons": [
                {"slot": 0, "present": True, "state": {
                    "species": 1, "current_hp": 30, "max_hp": 30, "level": 20,
                    "attack": 30, "defense": 20, "speed": 50, "special_attack": 20, "special_defense": 20,
                    "types": (0,), "moves": (10, 0, 0, 0), "pp": (10, 0, 0, 0),
                    "ability": 77, "held_item": 123, "stat_stages": (6,) * 8,
                }},
                {"slot": 1, "present": True, "state": {
                    "species": 2, "current_hp": 30, "max_hp": 30, "level": 20,
                    "attack": 20, "defense": 20, "speed": 10, "special_attack": 20, "special_defense": 20,
                    "types": (0,), "moves": (20, 0, 0, 0), "pp": (10, 0, 0, 0),
                    "ability": 78, "held_item": 124, "stat_stages": (6,) * 8,
                }},
            ]},
            "party": {"mons": []},
        }
        move = lambda move_id, chance: SimpleNamespace(
            move_id=move_id, name=str(move_id), power=40, type_id=0, type_name="Normal",
            accuracy=100, pp=10, secondary_chance=chance, target_flags=0, priority=0,
            category="physical",
        )
        report = RunBunAdapter.explain_battle_action(
            observation, move_data={10: move(10, 30), 20: move(20, 0)},
        )
        gaps = report["proof"]["material_uncertainty"]
        self.assertFalse(report["proof"]["mechanics_complete"])
        self.assertIn("secondary_effect_identity_unmodeled", gaps)
        self.assertIn("attacker_ability_effect_unmodeled:77", gaps)
        self.assertIn("defender_held_item_effect_unmodeled:124", gaps)
        self.assertEqual(report["chosen"]["mechanics_coverage"]["gaps"], report["chosen"]["uncertainties"])

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
        self.assertEqual(chosen["damage_range"], [7.0, 9.0])
        self.assertEqual(chosen["guaranteed_ko_in"], 3)
        self.assertFalse(chosen["ko_before_hit"])
        self.assertEqual(chosen["order"], "tie")
        self.assertEqual(chosen["evidence"], {"kind": "static_model", "legacy_samples_quarantined": 3})

    def test_super_fang_uses_fixed_half_current_hp_damage(self):
        attacker = {"state": {"species": 400}}
        defender = {"state": {"species": 388, "current_hp": 57}}
        self.assertEqual(RunBunAdapter._damage_bounds(162, attacker, defender), (28.0, 28.0))

    def test_reversal_power_scales_from_current_hp(self):
        mankey = {"state": {
            "species": 56, "level": 19, "attack": 41, "types": (1,),
            "current_hp": 44, "max_hp": 44,
        }}
        togedemaru = {"state": {"species": 777, "defense": 36, "types": (13, 8)}}
        reversal = SimpleNamespace(power=1, type_id=1, category="physical")
        self.assertEqual(RunBunAdapter._damage_bounds(179, mankey, togedemaru, move_data={179: reversal}), (14.0, 18.0))
        mankey["state"]["current_hp"] = 1
        self.assertEqual(RunBunAdapter._damage_bounds(179, mankey, togedemaru, move_data={179: reversal}), (108.0, 128.0))

    def test_classifier_avoids_creating_a_lethal_low_hp_reversal(self):
        player = {"slot": 0, "present": True, "state": {
            "species": 777, "current_hp": 39, "max_hp": 59, "level": 21,
            "attack": 42, "defense": 36, "speed": 52, "special_attack": 26, "special_defense": 36,
            "types": (13, 8), "moves": (252, 609, 232, 209), "pp": (4, 20, 35, 19),
            "stat_stages": (6,) * 8,
        }}
        opponent = {"slot": 1, "present": True, "state": {
            "species": 56, "current_hp": 44, "max_hp": 44, "level": 19,
            "attack": 41, "defense": 18, "speed": 40, "special_attack": 16, "special_defense": 22,
            "types": (1,), "moves": (612, 179, 0, 0), "pp": (32, 15, 0, 0),
            "stat_stages": (6,) * 8, "held_item": 481,
        }}
        move = lambda power, type_id: SimpleNamespace(
            power=power, type_id=type_id, category="physical", priority=0, accuracy=100,
            raw_flags=(0,),
        )
        action = RunBunAdapter.choose_battle_action(
            {"battle": {"active": True, "mons": [player, opponent]}, "party": {"mons": []}},
            move_data={
                252: SimpleNamespace(power=40, type_id=0, category="physical", priority=3, accuracy=100, raw_flags=(0,)),
                609: move(20, 13), 232: move(50, 8), 209: move(65, 13),
                612: move(40, 1), 179: move(1, 1),
            },
            fresh_entry=False,
        )
        self.assertEqual((action["move_id"], action["reason"]), (609, "avoid_low_hp_reversal"))

    def test_reversal_guard_still_allows_a_critical_safe_switch(self):
        player = {"slot": 0, "present": True, "state": {
            "species": 777, "current_hp": 39, "max_hp": 59, "level": 21,
            "attack": 42, "defense": 36, "speed": 52, "special_attack": 26, "special_defense": 36,
            "types": (13, 8), "moves": (252, 609, 232, 209), "pp": (4, 20, 35, 19),
            "stat_stages": (6,) * 8,
        }}
        lildozer = {"slot": 1, "present": True, "state": {
            "species": 231, "current_hp": 69, "max_hp": 69, "level": 21,
            "attack": 29, "defense": 39, "speed": 27, "special_attack": 22, "special_defense": 23,
            "types": (4,), "moves": (420, 523, 204, 88), "pp": (30, 20, 5, 15),
            "stat_stages": (6,) * 8,
        }}
        opponent = {"slot": 1, "present": True, "state": {
            "species": 56, "current_hp": 44, "max_hp": 44, "level": 19,
            "attack": 41, "defense": 18, "speed": 40, "special_attack": 16, "special_defense": 22,
            "types": (1,), "moves": (612, 179, 0, 0), "pp": (32, 15, 0, 0),
            "stat_stages": (6,) * 8, "held_item": 481,
        }}
        move = lambda power, type_id, category="physical", priority=0: SimpleNamespace(
            power=power, type_id=type_id, category=category, priority=priority, accuracy=100, raw_flags=(0,),
        )
        action = RunBunAdapter.choose_battle_action(
            {"battle": {"active": True, "mons": [player, opponent]}, "party": {"mons": [player, lildozer]}},
            move_data={
                252: move(40, 0, priority=3), 609: move(20, 13), 232: move(50, 8), 209: move(65, 13),
                420: move(40, 15, priority=1), 523: move(60, 4), 204: move(0, 18, "status"), 88: move(50, 5),
                612: move(40, 1), 179: move(1, 1),
            },
            fresh_entry=False,
        )
        self.assertEqual((action["action"], action["slot"], action["reason"]), ("switch", 1, "critical_survival_switch"))

    def test_switch_must_survive_entry_and_its_next_action_window(self):
        croak = {"slot": 0, "present": True, "state": {
            "species": 453, "current_hp": 38, "max_hp": 54, "level": 21,
            "attack": 36, "defense": 24, "speed": 26, "special_attack": 34, "special_defense": 19,
            "types": (3, 1), "moves": (124, 341, 252, 410), "pp": (20, 15, 5, 30), "stat_stages": (6,) * 8,
        }}
        bushtank = {"slot": 3, "present": True, "state": {
            "species": 388, "current_hp": 69, "max_hp": 69, "level": 21,
            "attack": 46, "defense": 42, "speed": 23, "special_attack": 30, "special_defense": 41,
            "types": (12,), "moves": (44, 75, 71, 590), "pp": (25, 25, 25, 10), "stat_stages": (6,) * 8,
        }}
        meditite = {"slot": 1, "present": True, "state": {
            "species": 307, "current_hp": 46, "max_hp": 46, "level": 19,
            "attack": 27, "defense": 25, "speed": 36, "special_attack": 20, "special_defense": 29,
            "types": (1, 14), "moves": (280, 88, 197, 0), "pp": (24, 15, 5, 0),
            "stat_stages": (6,) * 8, "ability": 74, "held_item": 558,
        }}
        move = lambda power, type_id, category="physical", priority=0: SimpleNamespace(
            power=power, type_id=type_id, category=category, priority=priority, accuracy=100, raw_flags=(0,),
        )
        action = RunBunAdapter.choose_battle_action(
            {"battle": {"active": True, "mons": [croak, meditite]}, "party": {"mons": [croak, bushtank]}},
            move_data={
                124: move(65, 3, "special"), 341: move(55, 4, "special"), 252: move(40, 0, priority=3),
                410: move(40, 1, "special", 1), 44: move(60, 17), 75: move(55, 12),
                71: move(40, 12, "special"), 590: move(0, 0, "status"),
                280: move(75, 1), 88: move(50, 5), 197: move(0, 1, "status", 4),
            },
            fresh_entry=False,
        )
        self.assertEqual((action["action"], action.get("move_id")), ("move", 124), action)

        croak["state"]["current_hp"] = 36
        breloom = {"slot": 1, "present": True, "state": {
            "species": 286, "current_hp": 13, "max_hp": 57, "level": 19,
            "attack": 60, "defense": 41, "speed": 40, "special_attack": 29, "special_defense": 33,
            "types": (12, 1), "moves": (358, 331, 183, 147), "pp": (16, 29, 30, 15),
            "stat_stages": (6,) * 8, "status": 128,
        }}
        action = RunBunAdapter.choose_battle_action(
            {"battle": {"active": True, "mons": [croak, breloom]}, "party": {"mons": [croak, bushtank]}},
            move_data={
                124: move(65, 3, "special"), 341: move(55, 4, "special"), 252: move(40, 0, priority=3),
                410: move(40, 1, "special", 1), 44: move(60, 17), 75: move(55, 12),
                71: move(40, 12, "special"), 590: move(0, 0, "status"),
                358: move(70, 1), 331: move(25, 12), 183: move(40, 1, priority=1),
                147: move(0, 12, "status"),
            },
            damage_memory={
                (453, 124, 286): [36, 44], (453, 341, 286): [5, 6],
                (453, 252, 286): [7, 9], (453, 410, 286): [12, 15],
                (286, 358, 453): [0], (286, 331, 453): [16, 45], (286, 183, 453): [12, 15],
                (286, 358, 388): [5, 10], (286, 331, 388): [5, 10], (286, 183, 388): [5, 10],
                (388, 44, 286): [10, 20], (388, 75, 286): [10, 20], (388, 71, 286): [10, 20],
            },
        )
        self.assertEqual((action["action"], action["move_id"], action["reason"]), (
            "move", 410, "expected_priority_finish_over_free_switch",
        ))

    def test_fresh_fake_out_preempts_lethal_attacks_despite_faster_detect(self):
        croak = {"slot": 0, "present": True, "state": {
            "species": 453, "current_hp": 18, "max_hp": 54, "level": 21,
            "attack": 36, "defense": 24, "speed": 26, "special_attack": 34, "special_defense": 19,
            "types": (3, 1), "moves": (124, 341, 252, 410), "pp": (20, 15, 5, 30), "stat_stages": (6,) * 8,
        }}
        meditite = {"slot": 1, "present": True, "state": {
            "species": 307, "current_hp": 22, "max_hp": 46, "level": 19,
            "attack": 27, "defense": 25, "speed": 36, "special_attack": 20, "special_defense": 29,
            "types": (1, 14), "moves": (280, 88, 197, 0), "pp": (21, 15, 5, 0),
            "stat_stages": (6,) * 8, "ability": 74, "held_item": 558,
        }}
        move = lambda power, type_id, category="physical", priority=0: SimpleNamespace(
            power=power, type_id=type_id, category=category, priority=priority, accuracy=100, raw_flags=(0,),
        )
        action = RunBunAdapter.choose_battle_action(
            {"battle": {"active": True, "mons": [croak, meditite]}, "party": {"mons": [croak]}},
            move_data={
                124: move(65, 3, "special"), 341: move(55, 4, "special"), 252: move(40, 0, priority=3),
                410: move(40, 1, "special", 1), 280: move(75, 1), 88: move(50, 5), 197: move(0, 1, "status", 4),
            },
            fresh_entry=True,
        )
        self.assertEqual((action["move_id"], action["reason"]), (252, "fresh_entry_fake_out_survival"))

    def test_fresh_fake_out_progress_ignores_harmless_higher_priority_protect(self):
        player = {"slot": 0, "present": True, "state": {
            "species": 777, "current_hp": 59, "max_hp": 59, "level": 21,
            "attack": 42, "defense": 36, "speed": 52, "special_attack": 26, "special_defense": 36,
            "types": (13, 8), "moves": (252, 609, 232, 209), "pp": (5, 20, 35, 20), "stat_stages": (6,) * 8,
        }}
        opponent = {"slot": 1, "present": True, "state": {
            "species": 67, "current_hp": 62, "max_hp": 62, "level": 18,
            "attack": 46, "defense": 35, "speed": 26, "special_attack": 26, "special_defense": 31,
            "types": (1,), "moves": (233, 263, 339, 182), "pp": (13, 20, 19, 10),
            "stat_stages": (6, 1, 7, 6, 6, 6, 6, 6), "ability": 62, "held_item": 472,
        }}
        move = lambda power, type_id, category="physical", priority=0: SimpleNamespace(
            power=power, type_id=type_id, category=category, priority=priority, accuracy=100, raw_flags=(0,),
        )
        action = RunBunAdapter.choose_battle_action(
            {"battle": {"active": True, "mons": [player, opponent]}, "party": {"mons": [player]}},
            move_data={
                252: move(40, 0, priority=3), 609: move(20, 13), 232: move(50, 8), 209: move(65, 13),
                233: move(70, 1, priority=-1), 263: move(70, 0), 339: move(0, 1, "status"), 182: move(0, 0, "status", 4),
            },
            fresh_entry=True,
        )
        self.assertEqual((action["move_id"], action["reason"]), (252, "fresh_entry_fake_out_progress"))

    def test_slower_ko_is_not_selected_when_it_faints_before_acting(self):
        croak = {"slot": 0, "present": True, "state": {
            "species": 453, "current_hp": 18, "max_hp": 54, "level": 21,
            "attack": 36, "defense": 24, "speed": 26, "special_attack": 34, "special_defense": 19,
            "types": (3, 1), "moves": (124, 341, 252, 410), "pp": (20, 15, 4, 30), "stat_stages": (6,) * 8,
        }}
        lildozer = {"slot": 1, "present": True, "state": {
            "species": 231, "current_hp": 69, "max_hp": 69, "level": 21,
            "attack": 29, "defense": 39, "speed": 27, "special_attack": 22, "special_defense": 23,
            "types": (4, 15), "moves": (420, 523, 204, 88), "pp": (30, 20, 5, 15), "stat_stages": (6,) * 8,
        }}
        meditite = {"slot": 1, "present": True, "state": {
            "species": 307, "current_hp": 12, "max_hp": 46, "level": 19,
            "attack": 27, "defense": 25, "speed": 36, "special_attack": 20, "special_defense": 29,
            "types": (1, 14), "moves": (280, 88, 197, 0), "pp": (21, 15, 5, 0),
            "stat_stages": (6,) * 8, "ability": 74, "held_item": 558,
        }}
        move = lambda power, type_id, category="physical", priority=0: SimpleNamespace(
            power=power, type_id=type_id, category=category, priority=priority, accuracy=100, raw_flags=(0,),
        )
        action = RunBunAdapter.choose_battle_action(
            {"battle": {"active": True, "mons": [croak, meditite]}, "party": {"mons": [croak, lildozer]}},
            move_data={
                124: move(65, 3, "special"), 341: move(55, 4, "special"), 252: move(40, 0, priority=3),
                410: move(40, 1, "special", 1), 420: move(40, 15, priority=1), 523: move(60, 4),
                204: move(0, 18, "status"), 88: move(50, 5), 280: move(75, 1), 197: move(0, 1, "status", 4),
            },
            fresh_entry=False,
        )
        self.assertEqual((action["action"], action["slot"], action["reason"]), (
            "move", 3, "last_preempting_damage_line",
        ))

    def test_charm_prevents_a_lethal_physical_critical_before_attacking(self):
        lildozer = {"slot": 0, "present": True, "state": {
            "species": 231, "current_hp": 44, "max_hp": 69, "level": 21,
            "attack": 29, "defense": 39, "speed": 27, "special_attack": 22, "special_defense": 23,
            "types": (4,), "moves": (420, 523, 204, 88), "pp": (30, 18, 5, 15), "stat_stages": (6,) * 8,
        }}
        machoke = {"slot": 1, "present": True, "state": {
            "species": 67, "current_hp": 62, "max_hp": 62, "level": 18,
            "attack": 46, "defense": 35, "speed": 26, "special_attack": 26, "special_defense": 31,
            "types": (1,), "moves": (233, 263, 339, 182), "pp": (16, 20, 20, 10),
            "stat_stages": (6,) * 8, "ability": 62, "held_item": 472,
        }}
        move = lambda power, type_id, category="physical", priority=0: SimpleNamespace(
            power=power, type_id=type_id, category=category, priority=priority, accuracy=100, raw_flags=(0,),
        )
        action = RunBunAdapter.choose_battle_action(
            {"battle": {"active": True, "mons": [lildozer, machoke]}, "party": {"mons": [lildozer]}},
            move_data={
                420: move(40, 15, priority=1), 523: move(60, 4), 204: move(0, 18, "status"), 88: move(50, 5),
                233: move(70, 1, priority=-1), 263: move(70, 0), 339: move(0, 1, "status"), 182: move(0, 0, "status", 4),
            },
            fresh_entry=False,
        )
        self.assertEqual((action["move_id"], action["reason"]), (204, "physical_debuff_prevents_critical_ko"))
        lildozer["state"]["speed"] = 25
        action = RunBunAdapter.choose_battle_action(
            {"battle": {"active": True, "mons": [lildozer, machoke]}, "party": {"mons": [lildozer]}},
            move_data={
                420: move(40, 15, priority=1), 523: move(60, 4), 204: move(0, 18, "status"), 88: move(50, 5),
                233: move(70, 1, priority=-1), 263: move(70, 0), 339: move(0, 1, "status"), 182: move(0, 0, "status", 4),
            }, fresh_entry=False,
        )
        self.assertNotEqual(action.get("move_id"), 204)

    def test_charm_cannot_rank_at_the_attack_stage_floor(self):
        lildozer = {"slot": 0, "present": True, "state": {
            "species": 231, "current_hp": 19, "max_hp": 69, "level": 21,
            "attack": 29, "defense": 39, "speed": 27, "special_attack": 22, "special_defense": 23,
            "types": (4,), "moves": (420, 523, 204, 88), "pp": (30, 18, 2, 15), "stat_stages": (6,) * 8,
        }}
        machoke = {"slot": 1, "present": True, "state": {
            "species": 67, "current_hp": 62, "max_hp": 62, "level": 18,
            "attack": 46, "defense": 35, "speed": 26, "special_attack": 26, "special_defense": 31,
            "types": (1,), "moves": (233, 263, 339, 182), "pp": (13, 20, 20, 10),
            "stat_stages": (6, 0, 6, 6, 6, 6, 6, 6), "ability": 62, "held_item": 472,
        }}
        move = lambda power, type_id, category="physical", priority=0: SimpleNamespace(
            power=power, type_id=type_id, category=category, priority=priority, accuracy=100, raw_flags=(0,),
        )
        action = RunBunAdapter.choose_battle_action(
            {"battle": {"active": True, "mons": [lildozer, machoke]}, "party": {"mons": [lildozer]}},
            move_data={
                420: move(40, 15, priority=1), 523: move(60, 4), 204: move(0, 18, "status"), 88: move(50, 5),
                233: move(70, 1, priority=-1), 263: move(70, 0), 339: move(0, 1, "status"), 182: move(0, 0, "status", 4),
            },
            fresh_entry=False,
        )
        self.assertNotEqual(action.get("move_id"), 204)

    def test_single_type_battler_deduplicates_repeated_live_type_bytes(self):
        mon = {"state": {"species": 388, "types": (12, 12, 9)}}
        self.assertEqual(RunBunAdapter._mon_types(mon), (12,))

    def test_current_party_fallback_types_cover_switch_scoring(self):
        self.assertEqual(RunBunAdapter._mon_types({"state": {"species": 453}}), (3, 1))
        self.assertEqual(RunBunAdapter._mon_types({"state": {"species": 551}}), (4, 17))
        self.assertEqual(RunBunAdapter._mon_types({"state": {"species": 777}}), (13, 8))

    def test_ground_is_neutral_into_water_for_bulldoze_estimate(self):
        attacker = {"state": {"species": 231, "level": 17, "attack": 24, "types": (4, 4, 9)}}
        defender = {"state": {"species": 400, "level": 16, "defense": 29, "types": (0, 11, 9)}}
        low, high = RunBunAdapter._damage_bounds(523, attacker, defender)
        self.assertLessEqual(high, 17.0)

    def test_burn_halves_physical_damage_unless_the_attacker_has_guts(self):
        attacker = {"state": {"species": 231, "level": 17, "attack": 24, "types": (4, 4, 9), "status": 16}}
        defender = {"state": {"species": 77, "level": 17, "defense": 28, "types": (10, 10, 9)}}
        self.assertEqual(RunBunAdapter._damage_bounds(523, attacker, defender), (14.0, 18.0))
        attacker["state"]["ability"] = 62
        self.assertEqual(RunBunAdapter._damage_bounds(523, attacker, defender), (32.0, 42.0))
        attacker["state"]["status"] = 8
        self.assertEqual(RunBunAdapter._damage_bounds(523, attacker, defender), (32.0, 42.0))

    def test_huge_and_pure_power_double_physical_attack(self):
        attacker = {"state": {"species": 307, "level": 19, "attack": 27, "types": (1,), "ability": 74}}
        defender = {"state": {"species": 453, "defense": 24, "types": (3, 1)}}
        pure = RunBunAdapter._damage_bounds(280, attacker, defender)
        attacker["state"]["ability"] = 37
        self.assertEqual(RunBunAdapter._damage_bounds(280, attacker, defender), pure)
        attacker["state"]["ability"] = 0
        ordinary = RunBunAdapter._damage_bounds(280, attacker, defender)
        self.assertGreaterEqual(pure[0], ordinary[0] * 1.8)

    def test_coba_berry_halves_super_effective_flying_damage(self):
        attacker = {"state": {"species": 16, "level": 20, "special_attack": 40, "types": (0, 2)}}
        defender = {"state": {"species": 307, "special_defense": 30, "types": (1, 14), "held_item": 558}}
        with_coba = RunBunAdapter._damage_bounds(16, attacker, defender)
        defender["state"]["held_item"] = 0
        without_coba = RunBunAdapter._damage_bounds(16, attacker, defender)
        self.assertLess(with_coba[1], without_coba[0])

    def test_levitate_blocks_ground_moves(self):
        attacker = {"state": {"species": 878, "level": 17, "attack": 33, "types": (8, 8, 9)}}
        defender = {"state": {"species": 603, "ability": 26, "current_hp": 54, "level": 17, "defense": 34, "types": (13, 13, 9)}}
        self.assertEqual(RunBunAdapter._damage_bounds(523, attacker, defender), (0.0, 0.0))

    def test_damage_bounds_models_tirtouga_solid_rock_and_rindo(self):
        tirtouga = {"state": {
            "species": 564, "level": 16, "types": (11, 5), "defense": 42,
            "special_defense": 26, "ability": 116, "held_item": 553,
        }}
        ohmnomnom = {"state": {"species": 777, "level": 17, "types": (13, 8), "attack": 35}}
        bushtank = {"state": {"species": 388, "level": 17, "types": (12,), "attack": 38}}
        chart = {13: {11: 2, 5: 1}, 12: {11: 2, 5: 2}}
        self.assertEqual(RunBunAdapter._damage_bounds(209, ohmnomnom, tirtouga, type_chart=chart), (18.0, 22.0))
        self.assertEqual(RunBunAdapter._damage_bounds(75, bushtank, tirtouga, type_chart=chart), (15.0, 19.0))
        tirtouga["state"]["held_item"] = 0
        self.assertEqual(RunBunAdapter._damage_bounds(75, bushtank, tirtouga, type_chart=chart), (30.0, 39.0))

    def test_damage_bounds_stack_iron_fist_and_muscle_band(self):
        ledian = {"state": {
            "species": 166, "level": 19, "types": (6, 2), "attack": 26,
            "ability": 89, "held_item": 475,
        }}
        croagunk = {"state": {"species": 453, "types": (3, 1), "defense": 24}}
        thunder_punch = SimpleNamespace(power=75, type_id=13, category="physical")
        self.assertEqual(
            RunBunAdapter._damage_bounds(9, ledian, croagunk, move_data={9: thunder_punch}),
            (16.0, 20.0),
        )

    def test_damage_bounds_models_fluffy_contact_reduction(self):
        attacker = {"state": {
            "species": 453, "level": 21, "types": (3, 1), "attack": 36,
            "special_attack": 34,
        }}
        stufful = {"state": {
            "species": 759, "types": (0, 1), "defense": 27,
            "special_defense": 29, "ability": 218,
        }}
        fake_out = SimpleNamespace(power=40, type_id=0, category="physical", raw_flags=(1,))
        vacuum_wave = SimpleNamespace(power=40, type_id=1, category="special", raw_flags=(0,))
        self.assertEqual(RunBunAdapter._damage_bounds(252, attacker, stufful, move_data={252: fake_out}), (5.0, 6.0))
        self.assertEqual(RunBunAdapter._damage_bounds(410, attacker, stufful, move_data={410: vacuum_wave}), (26.0, 32.0))

    def test_damage_bounds_include_both_dual_wingbeat_hits(self):
        farfetchd = {"state": {
            "species": 979, "level": 18, "types": (1,), "attack": 48,
            "special_attack": 27,
        }}
        grotle = {"state": {
            "species": 388, "types": (12,), "defense": 42,
            "special_defense": 41, "ability": 75,
        }}
        dual_wingbeat = SimpleNamespace(
            power=40, type_id=2, category="physical", raw_flags=(51, 2048, 0, 100, 279),
        )
        self.assertEqual(
            RunBunAdapter._damage_bounds(742, farfetchd, grotle, move_data={742: dual_wingbeat}),
            (32.0, 40.0),
        )

    def test_damage_bounds_cover_full_variable_pin_missile_range(self):
        heracross = {"state": {
            "species": 214, "level": 19, "types": (6, 1), "attack": 58,
            "special_attack": 23,
        }}
        togedemaru = {"state": {
            "species": 777, "types": (13, 8), "defense": 36,
            "special_defense": 36, "stat_stages": (6, 6, 5, 6, 6, 6, 6, 6),
        }}
        pin_missile = SimpleNamespace(power=25, type_id=6, category="physical", raw_flags=(50, 0, 0, 100, 19))
        self.assertEqual(
            RunBunAdapter._damage_bounds(
                42, heracross, togedemaru,
                move_data={42: pin_missile}, type_chart={6: {13: 1, 8: 0.5}},
            ),
            (14.0, 45.0),
        )

    def test_damage_bounds_cover_all_three_triple_axel_hits(self):
        buneary = {"state": {
            "species": 427, "level": 19, "types": (0,), "attack": 40, "special_attack": 20,
        }}
        grotle = {"state": {
            "species": 388, "types": (12,), "defense": 42, "special_defense": 41,
        }}
        triple_axel = SimpleNamespace(power=20, type_id=15, category="physical", raw_flags=(0,))
        self.assertEqual(
            RunBunAdapter._damage_bounds(741, buneary, grotle, move_data={741: triple_axel}),
            (4.0, 25.0),
        )

    def test_classifier_uses_fresh_fake_out_only_for_a_safe_threshold(self):
        player = {
            "species": 777, "current_hp": 50, "max_hp": 50, "level": 17,
            "types": (13, 8), "moves": (252, 609, 232, 209), "pp": (5, 20, 35, 20),
            "speed": 44, "attack": 35, "defense": 30, "special_attack": 22, "special_defense": 30,
            "stat_stages": (6,) * 8, "ability": 160, "held_item": 0,
        }
        murkrow = {
            "species": 198, "current_hp": 47, "max_hp": 47, "level": 15,
            "types": (17, 2), "moves": (252, 371, 101, 0), "pp": (5, 10, 15, 0),
            "speed": 39, "attack": 35, "defense": 22, "special_attack": 31, "special_defense": 22,
            "stat_stages": (6,) * 8, "ability": 15, "held_item": 522,
        }
        observation = {
            "battle": {"active": True, "format": "single", "mons": [
                {"slot": 0, "present": True, "state": player},
                {"slot": 1, "present": True, "state": murkrow},
            ]},
            "party": {"mons": []},
        }
        move = lambda move_id, power, type_id, priority=0: SimpleNamespace(
            move_id=move_id, name=str(move_id), power=power, type_id=type_id,
            type_name=str(type_id), accuracy=100, pp=10, secondary_chance=0,
            target_flags=0, priority=priority, category="physical",
        )
        move_data = {
            252: move(252, 40, 0, 3), 609: move(609, 20, 13), 232: move(232, 50, 8),
            209: move(209, 65, 13), 17: move(17, 60, 2), 371: move(371, 50, 17),
            101: move(101, 0, 7),
        }
        chart = {13: {17: 1, 2: 2}, 0: {17: 1, 2: 1}, 8: {17: 1, 2: 1}}
        fresh = RunBunAdapter.choose_battle_action(
            observation, move_data=move_data, type_chart=chart, fresh_entry=True,
        )
        stale = RunBunAdapter.choose_battle_action(
            observation, move_data=move_data, type_chart=chart, fresh_entry=False,
        )
        self.assertEqual((fresh["move_id"], fresh["reason"]), (252, "fresh_entry_fake_out_threshold"))
        self.assertEqual(stale["move_id"], 209)

        murkrow["held_item"] = 523
        murkrow["max_hp"] = 100
        self.assertNotEqual(
            RunBunAdapter.choose_battle_action(
                observation, move_data=move_data, type_chart=chart, fresh_entry=True,
            ).get("move_id"),
            252,
        )
        murkrow["held_item"] = 522
        murkrow["max_hp"] = 47

        move_data[182] = move(182, 0, 0, 4)
        murkrow["moves"] = (17, 182, 0, 0)
        protected = RunBunAdapter.choose_battle_action(
            observation, move_data=move_data, type_chart=chart, fresh_entry=True,
        )
        self.assertEqual((protected["move_id"], protected["reason"]), (252, "fresh_entry_fake_out_progress"))

    def test_iapapa_berry_matches_verified_quarter_threshold_and_half_heal(self):
        kubfu = {"held_item": 528, "current_hp": 21, "max_hp": 60}
        self.assertEqual(_berry_heal(kubfu), 30)
        self.assertTrue(_berry_triggers_after(kubfu, 8))
        kubfu["current_hp"] = 26
        self.assertFalse(_berry_triggers_after(kubfu, 8))

    def test_classifier_takes_free_fresh_fake_out_before_a_critical_switch(self):
        croagunk = {"slot": 0, "present": True, "state": {
            "species": 453, "current_hp": 54, "max_hp": 54, "level": 21,
            "attack": 36, "defense": 24, "speed": 26,
            "special_attack": 34, "special_defense": 19,
            "types": (3, 1, 9), "moves": (124, 341, 252, 410), "pp": (20, 15, 5, 30),
            "stat_stages": (6,) * 8, "ability": 143, "held_item": 0,
        }}
        grotle = {"slot": 3, "present": True, "state": {
            "species": 388, "current_hp": 69, "max_hp": 69, "level": 21,
            "attack": 46, "defense": 42, "speed": 23,
            "special_attack": 30, "special_defense": 41,
            "types": (12, 12, 9), "moves": (44, 75, 71, 590), "pp": (25, 25, 25, 10),
            "stat_stages": (6,) * 8, "ability": 65, "held_item": 0,
        }}
        farfetchd = {"slot": 1, "present": True, "state": {
            "species": 979, "current_hp": 52, "max_hp": 52, "level": 18,
            "attack": 48, "defense": 30, "speed": 30,
            "special_attack": 27, "special_defense": 32,
            "types": (1, 1, 9), "moves": (249, 282, 742, 98), "pp": (24, 20, 10, 30),
            "stat_stages": (6,) * 8, "ability": 113, "held_item": 523,
        }}
        move = lambda power, type_id, category="physical", priority=0: SimpleNamespace(
            power=power, type_id=type_id, category=category, priority=priority, accuracy=100,
        )
        move_data = {
            124: move(65, 3, "special"), 341: move(55, 4, "special"),
            252: move(40, 0, priority=3), 410: move(40, 1, "special", 1),
            249: move(40, 1), 282: move(65, 17), 742: move(40, 2), 98: move(40, 0, priority=1),
            44: move(60, 17), 75: move(55, 12), 71: move(40, 12, "special"),
            590: move(0, 0, "status"),
        }
        action = RunBunAdapter.choose_battle_action(
            {
                "battle": {"active": True, "format": "single", "mons": [croagunk, farfetchd]},
                "party": {"mons": [croagunk, grotle]},
            },
            move_data=move_data,
            fresh_entry=True,
        )
        self.assertEqual((action["action"], action["move_id"], action["reason"]), (
            "move", 252, "fresh_entry_fake_out_survival",
        ))

    def test_electric_is_ineffective_into_ground(self):
        attacker = {"state": {"species": 603, "level": 17, "special_attack": 35, "types": (13, 13, 9)}}
        defender = {"state": {"species": 95, "level": 17, "special_defense": 21, "types": (5, 4, 9)}}
        self.assertEqual(RunBunAdapter._damage_bounds(351, attacker, defender), (0.0, 0.0))

    def test_verified_dual_type_resistances_are_not_treated_as_neutral(self):
        grass = {"state": {"species": 192, "level": 16, "special_attack": 43, "types": (12,)}}
        venipede = {"state": {"species": 543, "level": 17, "special_defense": 21, "types": (6, 3)}}
        electric = {"state": {"species": 603, "level": 17, "special_attack": 35, "types": (13,)}}
        grotle = {"state": {"species": 388, "level": 17, "special_defense": 35, "types": (12,)}}
        energy_ball = SimpleNamespace(power=90, type_id=12, category="special")
        self.assertEqual(RunBunAdapter._damage_bounds(412, grass, venipede, move_data={412: energy_ball}), (9.0, 11.0))
        self.assertEqual(RunBunAdapter._damage_bounds(351, electric, grotle), (6.0, 8.0))

    def test_gavi_known_damage_ranges_match_cartridge_observations(self):
        def move(power, type_id, category):
            return SimpleNamespace(power=power, type_id=type_id, category=category)

        cases = (
            (479, {"species": 111, "level": 17, "attack": 35, "types": (5, 4, 9)}, {"species": 77, "level": 17, "defense": 28, "types": (10, 10, 9)}, move(50, 5, "physical"), {5: {10: 2}}, (30.0, 36.0)),
            (72, {"species": 603, "level": 17, "special_attack": 35, "types": (13, 13, 9)}, {"species": 111, "level": 17, "special_defense": 16, "types": (5, 4, 9)}, move(60, 12, "special"), {12: {5: 2, 4: 2}}, (76.0, 92.0)),
            (412, {"species": 192, "level": 16, "special_attack": 43, "types": (12, 12, 9)}, {"species": 878, "level": 17, "special_defense": 24, "types": (8, 8, 9)}, move(90, 12, "special"), {12: {8: 0.5}}, (16.0, 20.0)),
            (523, {"species": 551, "level": 17, "attack": 30, "types": (4, 17, 9)}, {"species": 192, "level": 16, "defense": 29, "types": (12, 12, 9)}, move(60, 4, "physical"), {4: {12: 0.5}}, (6.0, 8.0)),
        )
        for move_id, attacker, defender, metadata, chart, expected in cases:
            with self.subTest(move_id=move_id):
                self.assertEqual(
                    RunBunAdapter._damage_bounds(move_id, attacker, defender, move_data={move_id: metadata}, type_chart=chart),
                    expected,
                )

    def test_stat_stage_and_speed_tie_are_not_treated_as_first(self):
        state = {"stat_stages": (6, 5, 6, 6, 6)}
        self.assertAlmostEqual(RunBunAdapter._stage_multiplier(state, "attack"), 2 / 3)

    def test_facade_power_doubles_when_the_attacker_is_statused(self):
        move = SimpleNamespace(power=70, type_id=0, category="physical")
        attacker = {"species": 67, "level": 18, "attack": 46, "types": (1,), "status": 0}
        defender = {"species": 453, "defense": 24, "types": (3, 1)}
        healthy = RunBunAdapter._damage_bounds(263, attacker, defender, move_data={263: move})
        attacker["status"] = 8
        statused = RunBunAdapter._damage_bounds(263, attacker, defender, move_data={263: move})
        self.assertGreater(statused[0], healthy[1])

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
        self.assertEqual(action["action"], "move", action)
        self.assertEqual(action["move_id"], 16)

    def test_battle_strategy_ignores_slower_crit_when_priority_hit_cannot_stop_ko(self):
        active = {"slot": 0, "present": True, "state": {
            "species": 777, "current_hp": 53, "max_hp": 59, "level": 21,
            "attack": 42, "defense": 36, "speed": 52, "special_attack": 26, "special_defense": 36,
            "types": (13, 8), "moves": (252, 609, 232, 209), "pp": (4, 20, 35, 20),
            "stat_stages": (6,) * 8,
        }}
        bench = {"slot": 1, "present": True, "state": {
            "species": 231, "current_hp": 69, "max_hp": 69, "level": 21,
            "attack": 29, "defense": 39, "speed": 27, "special_attack": 22, "special_defense": 23,
            "types": (4,), "moves": (420, 523, 204, 88), "pp": (30, 20, 5, 15),
            "stat_stages": (6,) * 8,
        }}
        foe = {"slot": 1, "present": True, "state": {
            "species": 166, "current_hp": 7, "max_hp": 55, "level": 19,
            "attack": 26, "defense": 29, "speed": 43, "special_attack": 27, "special_defense": 52,
            "types": (6, 2), "moves": (409, 183, 0, 0), "pp": (16, 30, 0, 0),
            "stat_stages": (6,) * 8, "ability": 89, "held_item": 475,
        }}
        move = lambda power, type_id, priority=0: SimpleNamespace(
            power=power, type_id=type_id, category="physical", priority=priority, accuracy=100,
            raw_flags=(0,),
        )
        action = RunBunAdapter.choose_battle_action(
            {"battle": {"active": True, "mons": [active, foe]}, "party": {"mons": [active, bench]}},
            move_data={
                252: move(40, 0, 3), 609: move(20, 13), 232: move(50, 8), 209: move(65, 13),
                409: move(75, 1), 183: move(40, 1, 1),
                420: move(40, 15, 1), 523: move(60, 4), 204: SimpleNamespace(power=0, type_id=18, category="status", priority=0, accuracy=100),
                88: move(50, 5),
            },
            fresh_entry=False,
        )
        self.assertEqual((action["action"], action["move_id"], action["reason"]), (
            "move", 209, "survive_priority_and_finish",
        ))

    def test_battle_strategy_rejects_a_sacrificial_matchup_switch(self):
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
        self.assertEqual(action["action"], "move", action)
        self.assertEqual(action.get("move_id"), 267, action)

    def test_battle_strategy_does_not_trade_a_safe_attack_for_matchup_switch(self):
        bushtank = {"slot": 0, "present": True, "state": {
            "species": 388, "current_hp": 57, "max_hp": 69, "level": 21,
            "attack": 46, "defense": 42, "speed": 23,
            "special_attack": 30, "special_defense": 41,
            "types": (12, 12, 9), "moves": (44, 75, 71, 590), "pp": (25, 25, 25, 10),
        }}
        ohmnomnom = {"slot": 2, "present": True, "state": {
            "species": 777, "current_hp": 59, "max_hp": 59, "level": 21,
            "attack": 42, "defense": 36, "speed": 52,
            "special_attack": 26, "special_defense": 36,
            "types": (13, 8, 9), "moves": (252, 609, 232, 209), "pp": (5, 20, 35, 20),
        }}
        riolu = {"slot": 1, "present": True, "state": {
            "species": 447, "current_hp": 47, "max_hp": 47, "level": 18,
            "attack": 35, "defense": 24, "speed": 35,
            "special_attack": 20, "special_defense": 24,
            "types": (1, 1, 9), "moves": (395, 34, 0, 0), "pp": (16, 14, 0, 0),
        }}
        action = RunBunAdapter.choose_battle_action({
            "battle": {"active": True, "format": "single", "mons": [bushtank, riolu]},
            "party": {"mons": [bushtank, ohmnomnom]},
        })
        self.assertEqual((action["action"], action["move_id"]), ("move", 75))

    def test_battle_strategy_uses_a_critical_safe_switch_for_verified_crit_risk(self):
        bushtank = {"slot": 0, "present": True, "state": {
            "species": 388, "current_hp": 29, "max_hp": 69, "level": 21,
            "attack": 46, "defense": 42, "speed": 23,
            "special_attack": 30, "special_defense": 41,
            "types": (12, 12, 9), "moves": (44, 75, 71, 590), "pp": (25, 23, 25, 10),
        }}
        lildozer = {"slot": 1, "present": True, "state": {
            "species": 231, "current_hp": 69, "max_hp": 69, "level": 21,
            "attack": 29, "defense": 39, "speed": 27,
            "special_attack": 22, "special_defense": 23,
            "types": (4, 4, 9), "moves": (420, 523, 204, 88), "pp": (30, 20, 5, 15),
        }}
        stufful = {"slot": 1, "present": True, "state": {
            "species": 759, "current_hp": 56, "max_hp": 56, "level": 17,
            "attack": 42, "defense": 26, "speed": 27,
            "special_attack": 22, "special_defense": 25,
            "types": (0, 1, 9), "moves": (34, 395, 339, 0), "pp": (15, 10, 20, 0),
        }}
        action = RunBunAdapter.choose_battle_action({
            "battle": {"active": True, "format": "single", "mons": [bushtank, stufful]},
            "party": {"mons": [bushtank, lildozer]},
        })
        self.assertEqual((action["action"], action["species"], action["reason"]), (
            "switch", 231, "critical_survival_switch",
        ))

    def test_battle_strategy_rejects_a_switch_that_only_survives_entry(self):
        croagunk = {"slot": 0, "present": True, "state": {
            "species": 453, "current_hp": 30, "max_hp": 54, "level": 21,
            "attack": 36, "defense": 24, "speed": 26,
            "special_attack": 34, "special_defense": 19,
            "types": (3, 1, 9), "moves": (124, 341, 252, 410), "pp": (19, 15, 4, 30),
            "stat_stages": (6, 6, 5, 6, 6, 6, 6, 6), "ability": 143, "held_item": 0,
        }}
        togedemaru = {"slot": 2, "present": True, "state": {
            "species": 777, "current_hp": 39, "max_hp": 59, "level": 21,
            "attack": 42, "defense": 36, "speed": 52,
            "special_attack": 26, "special_defense": 36,
            "types": (13, 8, 9), "moves": (252, 609, 232, 209), "pp": (5, 20, 35, 18),
            "stat_stages": (6,) * 8, "ability": 160, "held_item": 0,
        }}
        heracross = {"slot": 1, "present": True, "state": {
            "species": 214, "current_hp": 42, "max_hp": 65, "level": 19,
            "attack": 58, "defense": 39, "speed": 47,
            "special_attack": 23, "special_defense": 46,
            "types": (6, 1, 9), "moves": (42, 249, 0, 0), "pp": (31, 14, 0, 0),
            "stat_stages": (6,) * 8, "ability": 68, "held_item": 522,
        }}
        move = lambda power, type_id, category="physical", priority=0: SimpleNamespace(
            power=power, type_id=type_id, category=category, priority=priority, accuracy=100,
            raw_flags=(0,),
        )
        action = RunBunAdapter.choose_battle_action(
            {
                "battle": {"active": True, "format": "single", "mons": [croagunk, heracross]},
                "party": {"mons": [croagunk, togedemaru]},
            },
            move_data={
                124: move(65, 3, "special"), 341: move(55, 4, "special"),
                252: move(40, 0, priority=3), 410: move(40, 1, "special", 1),
                42: move(25, 6), 249: move(40, 1), 609: move(20, 13),
                232: move(50, 8), 209: move(65, 13),
            },
            type_chart={
                0: {6: 1, 1: 1}, 1: {6: 0.5, 1: 1}, 3: {6: 1, 1: 1},
                4: {6: 0.5, 1: 1}, 6: {3: 0.5, 1: 0.5, 13: 1, 8: 0.5},
                8: {6: 1, 1: 1}, 13: {6: 1, 1: 1},
            },
            fresh_entry=False,
        )
        self.assertEqual((action["action"], action.get("move_id")), ("move", 410), action)

    def test_battle_strategy_uses_charm_when_it_changes_a_physical_damage_race(self):
        lildozer = {"slot": 0, "present": True, "state": {
            "species": 231, "current_hp": 30, "max_hp": 69, "level": 21,
            "attack": 29, "defense": 39, "speed": 27,
            "special_attack": 22, "special_defense": 23,
            "types": (4, 4, 9), "moves": (420, 523, 204, 88), "pp": (30, 19, 5, 15),
        }}
        stufful = {"slot": 1, "present": True, "state": {
            "species": 759, "current_hp": 38, "max_hp": 56, "level": 17,
            "attack": 35, "defense": 27, "speed": 18,
            "special_attack": 22, "special_defense": 29,
            "types": (0, 1, 9), "moves": (34, 395, 339, 0), "pp": (23, 10, 20, 0),
        }}
        move = lambda power, category, type_id=0: SimpleNamespace(
            power=power, type_id=type_id, category=category, priority=0, accuracy=100,
        )
        action = RunBunAdapter.choose_battle_action(
            {
                "battle": {"active": True, "format": "single", "mons": [lildozer, stufful]},
                "party": {"mons": [lildozer]},
            },
            move_data={
                420: move(40, "physical", 15), 523: move(60, "physical", 4),
                204: move(0, "status", 18), 88: move(50, "physical", 5),
                34: move(85, "physical"), 395: move(60, "physical"), 339: move(0, "status"),
            },
        )
        self.assertEqual((action["move_id"], action["reason"]), (
            204, "physical_debuff_prevents_critical_ko",
        ))

    def test_charm_cannot_make_an_offensive_pivot_crit_safe(self):
        lildozer = {"slot": 0, "present": True, "state": {
            "species": 231, "current_hp": 52, "max_hp": 69, "level": 21,
            "attack": 29, "defense": 39, "speed": 27,
            "special_attack": 22, "special_defense": 23,
            "types": (4, 4, 9), "moves": (420, 523, 204, 88), "pp": (30, 20, 5, 15),
            "stat_stages": (6,) * 8, "ability": 56, "held_item": 0,
        }}
        ohmnomnom = {"slot": 2, "present": True, "state": {
            "species": 777, "current_hp": 59, "max_hp": 59, "level": 21,
            "attack": 42, "defense": 36, "speed": 52,
            "special_attack": 26, "special_defense": 36,
            "types": (13, 8, 9), "moves": (252, 609, 232, 209), "pp": (5, 20, 35, 20),
            "stat_stages": (6,) * 8, "ability": 160, "held_item": 0,
        }}
        farfetchd = {"slot": 1, "present": True, "state": {
            "species": 979, "current_hp": 52, "max_hp": 52, "level": 18,
            "attack": 48, "defense": 30, "speed": 30,
            "special_attack": 27, "special_defense": 32,
            "types": (1, 1, 9), "moves": (249, 282, 742, 98), "pp": (24, 20, 8, 30),
            "stat_stages": (6,) * 8, "ability": 39, "held_item": 523,
        }}
        move = lambda power, type_id, category="physical", priority=0: SimpleNamespace(
            power=power, type_id=type_id, category=category, priority=priority, accuracy=100,
            raw_flags=(0,),
        )
        move_data = {
            420: move(40, 15, priority=1), 523: move(60, 4), 204: move(0, 18, "status"),
            88: move(50, 5), 249: move(40, 1), 282: move(65, 17),
            742: move(40, 2), 98: move(40, 0, priority=1),
            252: move(40, 0, priority=3), 609: move(20, 13), 232: move(50, 8), 209: move(65, 13),
        }
        action = RunBunAdapter.choose_battle_action(
            {
                "battle": {"active": True, "format": "single", "mons": [lildozer, farfetchd]},
                "party": {"mons": [lildozer, ohmnomnom]},
            },
            move_data=move_data,
            fresh_entry=True,
        )
        self.assertEqual((action["action"], action["move_id"], action["reason"]), (
            "move", 523, "best_damage_while_surviving",
        ))

        lildozer["state"]["current_hp"] = 35
        farfetchd["state"]["stat_stages"] = (6, 4, 6, 6, 6, 6, 6, 6)
        action = RunBunAdapter.choose_battle_action(
            {
                "battle": {"active": True, "format": "single", "mons": [lildozer, farfetchd]},
                "party": {"mons": [lildozer, ohmnomnom]},
            },
            move_data=move_data,
            fresh_entry=False,
        )
        self.assertEqual((action["action"], action["move_id"], action["reason"]), (
            "move", 523, "best_damage_while_surviving",
        ))

    def test_battle_strategy_avoids_activating_guts_before_the_reply(self):
        ohmnomnom = {"slot": 0, "present": True, "state": {
            "species": 777, "current_hp": 59, "max_hp": 59, "level": 21,
            "attack": 42, "defense": 36, "speed": 52,
            "special_attack": 26, "special_defense": 36,
            "types": (13, 8, 9), "moves": (252, 609, 232, 209), "pp": (4, 20, 35, 19),
            "stat_stages": (6,) * 8, "ability": 160, "held_item": 0, "status": 0,
        }}
        machoke = {"slot": 1, "present": True, "state": {
            "species": 67, "current_hp": 59, "max_hp": 62, "level": 18,
            "attack": 46, "defense": 33, "speed": 26,
            "special_attack": 25, "special_defense": 28,
            "types": (1, 1, 9), "moves": (233, 263, 339, 182), "pp": (9, 20, 19, 10),
            "stat_stages": (6, 1, 7, 6, 6, 6, 6, 6), "ability": 62,
            "held_item": 472, "status": 0,
        }}
        move = lambda power, type_id, category="physical", priority=0, secondary=0: SimpleNamespace(
            power=power, type_id=type_id, category=category, priority=priority,
            accuracy=100, secondary_chance=secondary, raw_flags=(0,),
        )
        action = RunBunAdapter.choose_battle_action(
            {
                "battle": {"active": True, "format": "single", "mons": [ohmnomnom, machoke]},
                "party": {"mons": [ohmnomnom]},
            },
            move_data={
                252: move(40, 0, priority=3), 609: move(20, 13, secondary=100),
                232: move(50, 8, secondary=10), 209: move(65, 13, secondary=30),
                233: move(70, 1, priority=-1), 263: move(70, 0),
                339: move(0, 1, "status"), 182: move(0, 0, "status", priority=4),
            },
        )
        self.assertEqual((action["action"], action["move_id"], action["reason"]), (
            "move", 232, "avoid_activating_guts_before_reply",
        ))

        machoke["state"]["current_hp"] = 15
        action = RunBunAdapter.choose_battle_action(
            {
                "battle": {"active": True, "format": "single", "mons": [ohmnomnom, machoke]},
                "party": {"mons": [ohmnomnom]},
            },
            move_data={
                252: move(40, 0, priority=3), 609: move(20, 13, secondary=100),
                232: move(50, 8, secondary=10), 209: move(65, 13, secondary=30),
                233: move(70, 1, priority=-1), 263: move(70, 0),
                339: move(0, 1, "status"), 182: move(0, 0, "status", priority=4),
            },
        )
        self.assertEqual((action["action"], action["move_id"]), ("move", 209))

    def test_forced_replacement_returns_a_legal_surviving_party_slot(self):
        fainted = {"slot": 0, "present": True, "state": {
            "species": 1, "current_hp": 0, "max_hp": 30, "level": 15,
            "attack": 20, "defense": 20, "speed": 20, "special_attack": 20,
            "special_defense": 20, "types": (12,), "moves": (10, 0, 0, 0), "pp": (10, 0, 0, 0),
        }}
        fragile = {"slot": 1, "present": True, "state": {
            "species": 2, "current_hp": 10, "max_hp": 30, "level": 15,
            "attack": 20, "defense": 10, "speed": 30, "special_attack": 20,
            "special_defense": 10, "types": (12,), "moves": (252, 0, 0, 0), "pp": (5, 0, 0, 0),
        }}
        sturdy = {"slot": 2, "present": True, "state": {
            "species": 3, "current_hp": 60, "max_hp": 60, "level": 15,
            "attack": 20, "defense": 50, "speed": 10, "special_attack": 20,
            "special_defense": 50, "types": (12,), "moves": (10, 0, 0, 0), "pp": (10, 0, 0, 0),
        }}
        foe = {"slot": 1, "present": True, "state": {
            "species": 4, "current_hp": 40, "max_hp": 40, "level": 15,
            "attack": 35, "defense": 20, "speed": 20, "special_attack": 20,
            "special_defense": 20, "types": (0,), "moves": (10, 0, 0, 0), "pp": (10, 0, 0, 0),
        }}
        move = SimpleNamespace(power=40, type_id=0, category="physical", priority=0)
        action = RunBunAdapter.choose_battle_action(
            {
                "battle": {"active": True, "party_switch_required": True, "menu": {"state": "party_switch"}, "mons": [fainted, foe]},
                "party": {"mons": [fainted, fragile, sturdy]},
            },
            move_data={10: move, 252: move},
        )
        self.assertEqual((action["action"], action["slot"], action["reason"]), (
            "switch", 2, "forced_replacement_best_entry",
        ))

        foe["state"]["current_hp"] = 1
        priority_move = SimpleNamespace(power=40, type_id=0, category="physical", priority=3)
        action = RunBunAdapter.choose_battle_action(
            {
                "battle": {"active": True, "party_switch_required": True, "menu": {"state": "party_switch"}, "mons": [fainted, foe]},
                "party": {"mons": [fainted, fragile, sturdy]},
            },
            move_data={10: move, 252: priority_move},
        )
        self.assertEqual(action["slot"], 1)

    def test_two_turn_finish_is_not_safe_against_higher_priority_damage(self):
        player = {"slot": 0, "present": True, "state": {
            "species": 1, "current_hp": 50, "max_hp": 50, "level": 20,
            "attack": 30, "defense": 20, "speed": 40, "special_attack": 20,
            "special_defense": 20, "types": (0,), "moves": (10, 0, 0, 0), "pp": (10, 0, 0, 0),
        }}
        foe = {"slot": 1, "present": True, "state": {
            "species": 2, "current_hp": 30, "max_hp": 30, "level": 20,
            "attack": 35, "defense": 30, "speed": 20, "special_attack": 20,
            "special_defense": 20, "types": (0,), "moves": (183, 0, 0, 0), "pp": (10, 0, 0, 0),
        }}
        move = lambda power, priority=0: SimpleNamespace(
            power=power, type_id=0, category="physical", priority=priority,
        )
        action = RunBunAdapter.choose_battle_action(
            {"battle": {"active": True, "mons": [player, foe]}, "party": {"mons": [player]}},
            move_data={10: move(40), 183: move(40, 1)},
        )
        self.assertNotEqual(action["reason"], "safe_two_turn_finish")

        player["state"]["current_hp"] = 2
        foe["state"]["current_hp"] = 4
        sturdy = {"slot": 1, "present": True, "state": {
            **player["state"], "species": 3, "current_hp": 50, "max_hp": 50,
        }}
        action = RunBunAdapter.choose_battle_action(
            {"battle": {"active": True, "mons": [player, foe]}, "party": {"mons": [player, sturdy]}},
            move_data={10: move(40), 183: move(40, 1)},
        )
        self.assertEqual(action["action"], "switch")

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
