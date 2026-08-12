from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from games.run_and_bun.battle_policy import (
    BattleHistory,
    BattlePolicy,
    PolicyError,
    _battle_state,
    _critical_damage_bound,
    _survival_margin_after_hits,
    _switch_projection,
    generate_battle_profile,
    load_profile,
    load_strategies,
    policy_bundle_hash,
    strategy_applicable,
    validate_profile,
)
from games.run_and_bun.battle_review import (
    QualificationLedger,
    agent_review_complete,
    battle_continuation_gate,
    clone_review_gate,
    live_qualification_gate,
    persist_review,
    review_episode,
    write_review_artifact,
)
from tools.create_battle_review import build_review, resolve_findings


def certificate(*, player_species=1, opponent_species=2, fresh=True, move_ids=(10, 11), opponent_hp=20):
    return {
        "state": {
            "player": {"species": player_species, "hp": 30, "max_hp": 30},
            "opponent": {"species": opponent_species, "hp": opponent_hp, "max_hp": 20},
        },
        "compact_state": {
            "battle": {
                "mons": [
                    {"slot": 0, "species": player_species, "hp": 30, "max_hp": 30, "moves": list(move_ids), "pp": [5, 5]},
                    {"slot": 1, "species": opponent_species, "hp": opponent_hp, "max_hp": 20, "moves": [20], "pp": [5]},
                ]
            },
            "party": [
                {"slot": 0, "species": player_species, "hp": 30, "max_hp": 30, "status": 0},
                {"slot": 1, "species": 3, "hp": 25, "max_hp": 25, "status": 0},
            ],
        },
        "boundary": {"party_switch_required": False},
        "legal_actions": [
            {"kind": "move", "slot": 0, "move_id": move_ids[0], "pp": 5},
            {"kind": "move", "slot": 1, "move_id": move_ids[1], "pp": 5},
            {"kind": "switch", "slot": 1, "species": 3, "hp": 25, "status": 0},
        ],
        "alternatives": [
            {"slot": 0, "move_id": move_ids[0], "damage_range": [5, 7], "damage_est": 6, "ko_before_hit": False, "evidence": {"kind": "rom_formula"}},
            {"slot": 1, "move_id": move_ids[1], "damage_range": [3, 4], "damage_est": 3.5, "ko_before_hit": False, "evidence": {"kind": "rom_formula"}},
        ],
        "incoming": {"max_damage_est": 4, "moves": []},
        "proof": {"level": "expected_best"},
    }


class BattlePolicyTests(unittest.TestCase):
    def profile(self):
        return {
            "schema_version": 1,
            "id": "fixture-policy",
            "trainer_key": "fixture",
            "roles": {"lead": {"species": 1}, "reserve": {"species": 3}},
            "strategy_ids": [],
            "reserve_objectives": [{"role": "reserve", "for_opponent_species": [9]}],
            "constraints": [
                {
                    "id": "fresh-preference",
                    "when": {"active_role": "lead", "opponent_species": [2], "fresh_entry": True},
                    "directives": [{"kind": "prefer", "action": {"kind": "move", "move_id": 11}}],
                }
            ],
        }

    def generated_profile(self):
        return {
            "schema_version": 2,
            "id": "generated-fixture",
            "trainer_key": "map:1:2/local:3",
            "party_bindings": {"member_0": {"species": 1}, "member_1": {"species": 3}},
            "matchup_assignments": [{
                "target": {"roster_position": 1, "species": 2},
                "primary": "member_0",
                "backups": ["member_1"],
            }],
            "strategy_manifest": {
                "clone_trial": [],
                "blind_live_soft": [],
                "full_live": [],
            },
            "exceptions": [],
        }

    def test_declarative_preference_is_audited_and_legal(self):
        policy = BattlePolicy(self.profile())
        decision = policy.decide(certificate())
        self.assertEqual(decision["action"]["move_id"], 11)
        self.assertEqual(decision["applied_strategy_ids"], ["fresh-preference"])
        self.assertEqual(decision["action"]["kind"], "move")
        self.assertEqual(decision["behavior_hash"], policy.behavior_hash)
        self.assertTrue(all(item["action"] in certificate()["legal_actions"] for item in decision["candidates"]))

    def test_switch_projection_uses_certificate_rom_move_data(self):
        cert = certificate()
        cert["compact_state"]["battle"]["mons"][1].update({
            "level": 17, "attack": 37, "speed": 37, "types": [1],
            "moves": [37], "pp": [10],
        })
        cert["compact_state"]["party"][1].update({
            "level": 21, "defense": 36, "speed": 52, "types": [13, 8],
            "moves": [209], "pp": [20], "attack": 42,
        })
        cert["move_data"] = {
            "37": {"power": 120, "type_id": 0, "category": "physical", "priority": 0, "accuracy": 100, "raw_flags": [1]},
            "209": {"power": 65, "type_id": 13, "category": "physical", "priority": 0, "accuracy": 100, "raw_flags": [1]},
        }
        projection = _switch_projection(
            cert, cert["legal_actions"][-1], cert["compact_state"]["battle"]["mons"][1],
            forced_switch=False,
        )
        self.assertLess(projection["survival_margin"], 25)

    def test_reusable_strategy_matches_verified_tactical_context(self):
        strategy = {
            "id": "ability-safe-tempo",
            "status": "reusable_proven",
            "auto_match": {
                "battle_format": ["single"],
                "active_move_ids_any": [11],
                "opponent_abilities_any": [77],
                "survives_critical": True,
            },
            "executable": {"rules": [{
                "id": "tempo",
                "when": {"active_move_ids_any": [11], "survives_critical": True},
                "directives": [{"kind": "prefer", "action": {"kind": "move", "move_id": 11}}],
            }]},
        }
        cert = certificate()
        cert["boundary"]["format"] = "single"
        cert["compact_state"]["battle"]["mons"][1]["ability"] = 77
        cert["incoming"]["critical_max_damage_est"] = 10
        decision = BattlePolicy(self.profile(), strategies=[strategy]).decide(cert)
        self.assertEqual(decision["action"]["move_id"], 11)
        self.assertIn("strategy:ability-safe-tempo/rule:tempo", decision["applied_strategy_ids"])

        cert["compact_state"]["battle"]["mons"][1]["ability"] = 78
        decision = BattlePolicy(self.profile(), strategies=[strategy]).decide(cert)
        self.assertFalse(any(item.startswith("strategy:ability-safe-tempo") for item in decision["applied_strategy_ids"]))

    def test_existing_executable_strategy_runs_against_recorded_non_gavi_trainer_without_engine_code(self):
        database = json.loads(Path(
            ".agents/skills/develop-runbun-strategies/references/strategies.json"
        ).read_text(encoding="utf-8"))
        strategy = copy.deepcopy(next(
            item for item in database["strategies"] if item["id"] == "fresh-entry-fake-out-tempo"
        ))
        strategy["status"] = "reusable_proven"  # Promotion is isolated to this regression fixture.
        profile = self.profile()
        profile.update({"id": "dale-generic-regression", "trainer_key": "map:0:21/local:4", "constraints": [], "reserve_objectives": []})
        cert = certificate(player_species=1, opponent_species=557, move_ids=(252, 10))
        cert["boundary"]["format"] = "single"
        decision = BattlePolicy(profile, strategies=[strategy]).decide(cert)
        self.assertIn("strategy:fresh-entry-fake-out-tempo/rule:fresh-prefer", decision["applied_strategy_ids"])
        self.assertEqual(decision["action"]["move_id"], 252)

    def test_exact_prefer_forbid_conflict_is_rejected_before_execution(self):
        profile = self.profile()
        profile["constraints"].append({
            "id": "contradiction",
            "when": copy.deepcopy(profile["constraints"][0]["when"]),
            "directives": [{"kind": "forbid", "action": {"kind": "move", "move_id": 11}}],
        })
        with self.assertRaisesRegex(PolicyError, "contradictory prefer/forbid"):
            BattlePolicy(profile)

    def test_action_count_and_fresh_entry_reconstruct_from_verified_records(self):
        profile = self.profile()
        profile["constraints"][0]["when"] = {
            "active_role": "lead",
            "opponent_species": [2],
            "action_count": {"role": "lead", "move_id": 11, "equals": 0},
        }
        policy = BattlePolicy(profile)
        cert = certificate()
        first = policy.choose(cert)
        policy.record_verified(cert, first)
        second = policy.choose(cert)
        self.assertEqual(first["move_id"], 11)
        self.assertNotEqual(policy.last_decision["applied_strategy_ids"], ["fresh-preference"])
        self.assertEqual(policy.history.snapshot()["verified_actions"], 1)
        self.assertFalse(policy.history.fresh_entry)

    def test_enemy_forced_identity_change_is_a_fresh_entry(self):
        history = BattleHistory()
        cert = certificate()
        history.record(cert, {
            "action": {"kind": "move", "slot": 0, "move_id": 10},
            "actual": {"enemy_move_id": 525},
            "observation": {"battle": {"mons": [
                {"slot": 0, "species": 4, "personality": 44, "hp": 20},
                {"slot": 1, "species": 2, "personality": 22, "hp": 20},
            ]}},
        })
        self.assertTrue(history.fresh_entry)

    def test_double_history_tracks_fresh_entry_and_switches_per_allied_battler(self):
        history = BattleHistory()
        cert = certificate()
        cert["boundary"].update({"format": "double", "actor": 0})
        cert["compact_state"]["battle"]["mons"].append({
            "slot": 2, "species": 4, "personality": 44, "hp": 20, "moves": [252], "pp": [5],
        })
        history.record(cert, {"action": {"kind": "move", "actor": 0, "slot": 0, "move_id": 10}})
        cert["boundary"]["actor"] = 2
        self.assertTrue(history.fresh_entry_for(cert))
        self.assertEqual(history.consecutive_switches_for(cert), 0)
        history.record(cert, {"action": {"kind": "move", "actor": 2, "slot": 0, "move_id": 252}})
        self.assertFalse(history.fresh_entry_for(cert))

    def test_history_restarts_from_verified_jsonl(self):
        cert = certificate()
        record = {"verified": True, "certificate": cert, "action": {"kind": "move", "slot": 1, "move_id": 11}, "actual": {"enemy_move_id": 611}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "battle.jsonl"
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            history = BattleHistory.from_jsonl(path)
        self.assertEqual(history.snapshot()["verified_actions"], 1)
        self.assertFalse(history.fresh_entry)
        self.assertTrue(history.switch_forbidden)

    def test_history_constrains_only_the_forced_second_thrash_turn(self):
        history = BattleHistory()
        cert = certificate(opponent_species=56)
        history.record(cert, {"action": {"kind": "switch", "slot": 1, "species": 3}, "actual": {"enemy_move_id": 37}})
        self.assertEqual(history.enemy_move_constraint(56), {37})
        history.record(cert, {"action": {"kind": "move", "slot": 0, "move_id": 10}, "actual": {"enemy_move_id": 37}})
        self.assertIsNone(history.enemy_move_constraint(56))

    def test_history_removes_fake_out_after_the_opponents_entry_turn(self):
        history = BattleHistory()
        cert = certificate(opponent_species=296)
        opponent = cert["compact_state"]["battle"]["mons"][1]
        opponent["moves"] = [252, 418]
        self.assertIsNone(history.enemy_move_constraint(296, opponent=opponent))
        history.record(cert, {"action": {"kind": "move", "slot": 0, "move_id": 10}})
        self.assertEqual(history.enemy_move_constraint(296, opponent=opponent), {418})

    def test_history_replay_stops_before_target_state(self):
        cert = certificate()
        records = [
            {"battle_id": "fight", "pre_state_hash": "before", "verified": True, "certificate": cert, "action": {"kind": "move", "slot": 1, "move_id": 11}},
            {"battle_id": "fight", "pre_state_hash": "target", "verified": True, "certificate": cert, "action": {"kind": "switch", "slot": 1, "species": 3}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "battle.jsonl"
            path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
            history = BattleHistory.from_jsonl(path, battle_id="fight", before_state_hash="target")
        self.assertEqual(history.snapshot()["verified_actions"], 1)
        self.assertEqual(history.snapshot()["recent_switch_edges"], [])

    def test_repeated_switch_reversal_is_vetoed_but_damage_remains_legal(self):
        policy = BattlePolicy(self.profile())
        first = certificate(player_species=1)
        policy.record_verified(first, {"kind": "switch", "slot": 1, "species": 3})
        second = certificate(player_species=3)
        second["compact_state"]["party"].append({"slot": 2, "species": 4, "hp": 25, "max_hp": 25, "status": 0})
        policy.record_verified(second, {"kind": "switch", "slot": 2, "species": 4})
        third = certificate(player_species=4)
        reversal = next(action for action in third["legal_actions"] if action["kind"] == "switch")
        decision = policy.decide(third)
        candidate = next(item for item in decision["candidates"] if item["action"] == reversal)
        self.assertIn("history:immediate_switch_reversal", candidate["forbidden_by"])
        self.assertEqual(decision["action"]["kind"], "move")

    def test_return_after_switch_target_acts_is_not_a_no_action_reversal(self):
        policy = BattlePolicy(self.profile())
        first = certificate(player_species=1)
        policy.record_verified(first, {"kind": "switch", "slot": 1, "species": 3})
        active = certificate(player_species=3)
        policy.record_verified(active, {"kind": "move", "slot": 0, "move_id": 10})
        active["compact_state"]["party"][1]["species"] = 1
        active["legal_actions"][-1]["species"] = 1
        decision = policy.decide(active)
        switch = next(item for item in decision["candidates"] if item["action"]["kind"] == "switch")
        self.assertNotIn("history:immediate_switch_reversal", switch["forbidden_by"])

    def test_second_unplanned_switch_is_vetoed_until_the_new_active_acts(self):
        policy = BattlePolicy(self.profile())
        policy.record_verified(certificate(player_species=1), {"kind": "switch", "slot": 1, "species": 3})
        decision = policy.decide(certificate(player_species=3))
        switch = next(item for item in decision["candidates"] if item["action"]["kind"] == "switch")
        self.assertIn("history:switch_stall_after_one", switch["forbidden_by"])
        self.assertEqual(decision["action"]["kind"], "move")

    def test_forced_replacement_does_not_count_as_voluntary_switch_stall(self):
        policy = BattlePolicy(self.profile())
        forced = certificate(player_species=1)
        forced["boundary"]["party_switch_required"] = True
        policy.record_verified(forced, {"kind": "switch", "slot": 1, "species": 3})
        decision = policy.decide(certificate(player_species=3))
        switch = next(item for item in decision["candidates"] if item["action"]["kind"] == "switch")
        self.assertNotIn("history:switch_stall_after_one", switch["forbidden_by"])

    def test_safe_return_switch_can_escape_when_every_attack_is_unsafe(self):
        policy = BattlePolicy(self.profile())
        policy.record_verified(certificate(player_species=1), {"kind": "switch", "slot": 1, "species": 3})
        cert = certificate(player_species=3)
        cert["state"]["player"].update({"hp": 3, "max_hp": 30})
        cert["compact_state"]["battle"]["mons"][0].update({"hp": 3, "max_hp": 30})
        cert["compact_state"]["party"][1].update({"species": 1, "hp": 30, "max_hp": 30})
        cert["legal_actions"][-1].update({"species": 1, "hp": 30})
        cert["incoming"].update({"max_damage_est": 4, "critical_max_damage_est": 6})

        decision = policy.decide(cert)
        switch = next(item for item in decision["candidates"] if item["action"]["kind"] == "switch")
        self.assertEqual(decision["action"]["kind"], "switch")
        self.assertEqual(switch["forbidden_by"], [])
        self.assertIn("history:switch_stall_after_one", switch["relaxed_vetoes"])

    def test_profile_preference_cannot_authorize_switch_spam(self):
        profile = self.profile()
        profile["constraints"].append({
            "id": "preferred-reversal",
            "when": {"active_role": "reserve", "opponent_species": [2]},
            "directives": [{"kind": "prefer", "action": {"kind": "switch", "role": "lead"}}],
        })
        policy = BattlePolicy(profile)
        policy.record_verified(certificate(player_species=1), {"kind": "switch", "slot": 1, "species": 3})
        cert = certificate(player_species=3)
        cert["compact_state"]["party"][1]["species"] = 1
        cert["legal_actions"][-1]["species"] = 1
        decision = policy.decide(cert)
        switch = next(item for item in decision["candidates"] if item["action"]["kind"] == "switch")
        self.assertIn("preferred-reversal", decision["applied_strategy_ids"])
        self.assertIn("history:switch_stall_after_one", switch["forbidden_by"])
        self.assertEqual(decision["action"]["kind"], "move")

    def test_verified_infestation_forbids_only_voluntary_switches(self):
        profile = self.profile()
        policy = BattlePolicy(profile)
        cert = certificate(player_species=1, opponent_species=2, move_ids=(10, 11))
        policy.record_verified(cert, {"kind": "move", "slot": 0, "move_id": 10}, {"actual": {"enemy_move_id": 611}})
        decision = policy.decide(cert)
        self.assertEqual(decision["action"]["kind"], "move")
        switch = next(item for item in decision["candidates"] if item["action"]["kind"] == "switch")
        self.assertIn("history:verified_volatile_trap", switch["forbidden_by"])

    def test_infestation_tracks_post_switch_identity(self):
        history = BattleHistory()
        before = certificate(player_species=1)
        history.record(
            before,
            {
                "action": {"kind": "switch", "slot": 1, "species": 3},
                "actual": {"enemy_move_id": 611},
                "observation": {"battle": {"mons": [{"slot": 0, "present": True, "state": {"species": 3}}]}},
            },
        )
        self.assertEqual(history.snapshot()["trapped_identity"], "species:3")
        self.assertFalse(history.switch_forbidden_for(before))
        self.assertTrue(history.switch_forbidden_for(certificate(player_species=3)))

    def test_behavior_hash_excludes_prose_and_evidence(self):
        profile = self.profile()
        other = copy.deepcopy(profile)
        other["intent"] = "different prose"
        other["evidence"] = {"losses": 99}
        self.assertEqual(policy_bundle_hash(profile), policy_bundle_hash(other))
        changed = copy.deepcopy(profile)
        changed["constraints"][0]["directives"][0]["action"]["move_id"] = 10
        self.assertNotEqual(policy_bundle_hash(profile), policy_bundle_hash(changed))

    def test_reserve_matchup_rewards_switching_into_the_current_answer(self):
        policy = BattlePolicy(self.profile())
        action = {"kind": "switch", "slot": 1, "species": 3, "hp": 25}
        self.assertEqual(policy._reserve_score(action, certificate(opponent_species=9)), 1)

    def test_generated_matchup_preserves_answer_without_rewarding_switch_spam(self):
        policy = BattlePolicy(self.generated_profile())
        action = {"kind": "switch", "slot": 0, "species": 1, "hp": 25}
        self.assertEqual(policy._reserve_score(action, certificate(opponent_species=2)), 0)

    def test_generated_forced_replacement_maximizes_unknown_roster_coverage(self):
        policy = BattlePolicy(self.generated_profile())
        cert = certificate(player_species=1, opponent_species=2)
        cert["boundary"]["party_switch_required"] = True
        cert["compact_state"]["battle"]["mons"][1]["hp"] = 0
        first = {"kind": "switch", "slot": 0, "species": 1, "hp": 25}
        backup = {"kind": "switch", "slot": 1, "species": 3, "hp": 25}
        self.assertGreater(policy._reserve_score(first, cert), policy._reserve_score(backup, cert))

    def test_generated_forced_replacement_uses_ranked_known_matchup_backup(self):
        policy = BattlePolicy(self.generated_profile())
        cert = certificate(player_species=9, opponent_species=2)
        cert["boundary"]["party_switch_required"] = True
        cert["compact_state"]["party"][0]["species"] = 1
        primary = {"kind": "switch", "slot": 0, "species": 1, "hp": 25}
        backup = {"kind": "switch", "slot": 1, "species": 3, "hp": 25}
        self.assertGreater(policy._reserve_score(primary, cert), policy._reserve_score(backup, cert))

    def test_candidate_one_control_move_runs_once_after_entry_tempo(self):
        strategy = {
            "id": "one-control", "status": "candidate",
            "executable": {"rules": [{
                "id": "once", "when": {
                    "fresh_entry": False, "active_move_ids_all": [252, 609],
                    "survives_critical": True,
                    "action_count": {"move_id": 609, "equals": 0},
                },
                "directives": [{"kind": "prefer", "action": {"kind": "move", "move_id": 609}, "priority": 2}],
            }]},
        }
        profile = self.generated_profile()
        profile["strategy_manifest"]["clone_trial"] = ["one-control"]
        cert = certificate(move_ids=(609, 209))
        cert["compact_state"]["battle"]["mons"][0]["moves"] = [252, 609, 209]
        cert["compact_state"]["battle"]["mons"][0]["pp"] = [5, 20, 20]
        policy = BattlePolicy(profile, strategies=[strategy], activation_mode="clone_trial")
        policy.history._fresh_by_identity["species:1"] = False
        first = policy.decide(cert)
        self.assertEqual(first["action"]["move_id"], 609)
        policy.record_verified(cert, first["action"])
        self.assertNotIn("strategy:one-control/rule:once", policy.decide(cert)["applied_strategy_ids"])

    def test_auto_matched_strategy_rules_have_unique_audit_ids(self):
        strategy = {
            "id": "fresh-tempo",
            "status": "reusable_proven",
            "auto_match": {"battle_formats": ["single"], "active_move_ids_any": [10]},
            "executable": {"rules": [
                {"id": "first", "when": {}, "directives": [{"kind": "prefer", "action": {"kind": "move", "move_id": 10}}]},
                {"id": "second", "when": {}, "directives": [{"kind": "prefer", "action": {"kind": "move", "move_id": 11}}]},
            ]},
        }
        cert = certificate()
        cert["boundary"]["format"] = "single"
        decision = BattlePolicy(self.profile(), strategies=[strategy]).decide(cert)
        self.assertEqual(
            [item for item in decision["applied_strategy_ids"] if item.startswith("strategy:")],
            ["strategy:fresh-tempo/rule:first", "strategy:fresh-tempo/rule:second"],
        )

    def test_candidate_strategy_is_suggested_but_never_auto_executed(self):
        strategy = {
            "id": "unproven",
            "status": "candidate",
            "auto_match": {"battle_formats": ["single"], "active_move_ids_any": [10]},
            "executable": {"when": {}, "directives": [
                {"kind": "prefer", "action": {"kind": "move", "move_id": 10}},
            ]},
        }
        decision = BattlePolicy(self.profile(), strategies=[strategy]).decide(certificate())
        self.assertFalse(any(item.startswith("strategy:unproven") for item in decision["applied_strategy_ids"]))

    def test_candidate_strategy_activates_only_in_clone_trial_manifest(self):
        strategy = {
            "id": "trial", "status": "candidate",
            "executable": {"rules": [{
                "id": "prefer-second", "when": {},
                "directives": [{"kind": "prefer", "action": {"kind": "move", "move_id": 11}}],
            }]},
        }
        profile = self.generated_profile()
        profile["strategy_manifest"]["clone_trial"] = ["trial"]
        clone = BattlePolicy(profile, strategies=[strategy], activation_mode="clone_trial").decide(certificate())
        live = BattlePolicy(profile, strategies=[strategy], activation_mode="blind_live").decide(certificate())
        self.assertEqual(clone["action"]["move_id"], 11)
        self.assertFalse(any(item.startswith("strategy:trial") for item in live["applied_strategy_ids"]))

    def test_strategy_is_not_reported_applied_when_no_action_matches(self):
        strategy = {
            "id": "bench-only", "status": "candidate",
            "executable": {"when": {}, "directives": [
                {"kind": "prefer", "action": {"kind": "move", "move_id": 252}},
            ]},
        }
        profile = self.generated_profile()
        profile["strategy_manifest"]["clone_trial"] = ["bench-only"]
        decision = BattlePolicy(profile, strategies=[strategy], activation_mode="clone_trial").decide(certificate())
        self.assertFalse(decision["applied_strategy_ids"])

    def test_reusable_tested_is_soft_only_in_blind_live_mode(self):
        profile = self.generated_profile()
        profile["strategy_manifest"]["blind_live_soft"] = ["soft"]
        strategy = {
            "id": "soft", "status": "reusable_tested",
            "executable": {"rules": [{
                "id": "preference", "when": {},
                "directives": [
                    {"kind": "prefer", "action": {"kind": "move", "move_id": 11}},
                    {"kind": "forbid", "action": {"kind": "move", "move_id": 10}},
                ],
            }]},
        }
        decision = BattlePolicy(profile, strategies=[strategy], activation_mode="blind_live").decide(certificate())
        self.assertEqual(decision["action"]["move_id"], 11)
        self.assertEqual(decision["vetoes"], {})
        self.assertEqual(decision["influential_strategy_ids"], ["soft"])

    def test_profile_veto_cannot_remove_lexicographically_safer_action(self):
        profile = self.generated_profile()
        profile["exceptions"] = [{
            "id": "bad-veto", "when": {},
            "directives": [{"kind": "forbid", "action": {"kind": "move", "move_id": 10}}],
        }]
        cert = certificate(opponent_hp=5)
        decision = BattlePolicy(profile).decide(cert)
        self.assertEqual(decision["action"]["move_id"], 10)
        chosen = next(item for item in decision["candidates"] if item["action"]["move_id"] == 10)
        self.assertIn("bad-veto", chosen["relaxed_vetoes"])

    def test_semantic_selector_matches_guaranteed_ko_without_species_rule(self):
        profile = self.profile()
        profile["strategy_ids"] = ["secure-ko"]
        strategy = {
            "id": "secure-ko", "status": "tested",
            "executable": {"rules": [{
                "id": "finish", "when": {},
                "directives": [{"kind": "prefer", "action": {"kind": "move", "guaranteed_ko": True}}],
            }]},
        }
        cert = certificate(opponent_hp=4)
        decision = BattlePolicy(profile, strategies=[strategy]).decide(cert)
        self.assertEqual(decision["action"]["move_id"], 10)
        self.assertIn("strategy:secure-ko/rule:finish", decision["applied_strategy_ids"])

    def test_generated_profile_selects_candidates_without_handwritten_constraints(self):
        party = [
            {"slot": 0, "species": 1, "personality": 10, "ot_id": 1, "types": [10], "moves": [101]},
            {"slot": 1, "species": 3, "personality": 30, "ot_id": 1, "types": [11], "moves": [252]},
        ]
        trainer = {
            "key": "map:1:2/local:3", "battle": {"format": "single", "roster_complete": True},
            "roster": [{"send_out_position": 1, "species_id": 9, "types": [12], "moves": [201]}],
            "uncertainties": [],
        }
        strategy = {
            "id": "trial", "status": "candidate", "preconditions": {"party_move_ids_any": [252]},
            "executable": {"when": {"fresh_entry": True}, "directives": [{"kind": "prefer", "action": {"kind": "move", "move_id": 252}}]},
        }
        profile = generate_battle_profile(party, trainer, [strategy], state_hash="state")
        self.assertEqual(profile["schema_version"], 2)
        self.assertEqual(profile["exceptions"], [])
        self.assertEqual(profile["strategy_manifest"]["clone_trial"], ["trial"])
        self.assertEqual(profile["generation"]["party_state_hash"], "state")
        self.assertFalse(validate_profile(profile))

    def test_generated_profile_respects_fixed_ability_immunity(self):
        class Rom:
            @staticmethod
            def type_chart():
                return {4: {5: 2.0, 14: 1.0}, 17: {5: 1.0, 14: 2.0}}

            @staticmethod
            def move(move_id):
                return SimpleNamespace(type_id={1: 4, 2: 17}[move_id], power={1: 100, 2: 60}[move_id], category="physical")

        party = [
            {"species": 1, "personality": 10, "ot_id": 1, "moves": [1]},
            {"species": 2, "personality": 20, "ot_id": 1, "moves": [2]},
        ]
        trainer = {
            "key": "map:1:2/local:3", "battle": {"format": "single", "roster_complete": True},
            "roster": [{"send_out_position": 1, "species_id": 337, "types": [5, 14], "ability_id": 26}],
        }
        profile = generate_battle_profile(party, trainer, state_hash="state", rom=Rom())
        self.assertEqual(profile["matchup_assignments"][0]["primary"], "member_1")

    def test_generated_profile_knows_thousand_arrows_hits_flying(self):
        class Rom:
            @staticmethod
            def type_chart():
                return {4: {0: 1.0, 2: 0.0, 4: 1.0}, 2: {16: 1.0, 4: 1.0}, 15: {16: 2.0, 4: 2.0}}

            @staticmethod
            def move(move_id):
                move_type, power = {10: (2, 60), 11: (15, 40), 614: (4, 90)}[move_id]
                return SimpleNamespace(type_id=move_type, power=power, category="physical")

        party = [
            {"species": 1, "personality": 10, "ot_id": 1, "types": [0, 2], "moves": [10]},
            {"species": 2, "personality": 20, "ot_id": 1, "types": [4], "moves": [11]},
        ]
        trainer = {
            "key": "map:1:2/local:3", "battle": {"format": "single", "roster_complete": True},
            "roster": [{"send_out_position": 1, "species_id": 1164, "types": [16, 4], "moves": [614]}],
        }
        profile = generate_battle_profile(party, trainer, state_hash="state", rom=Rom())
        self.assertEqual(profile["matchup_assignments"][0]["primary"], "member_1")

    def test_reserve_directive_penalizes_protected_role(self):
        profile = self.profile()
        profile["strategy_ids"] = ["keep-reserve"]
        strategy = {
            "id": "keep-reserve",
            "executable": {"rules": [{
                "id": "protect",
                "when": {},
                "directives": [{"kind": "reserve", "role": "reserve", "priority": 3}],
            }]},
        }
        decision = BattlePolicy(profile, strategies=[strategy]).decide(certificate())
        switch = next(item for item in decision["candidates"] if item["action"]["kind"] == "switch")
        self.assertEqual(next(iter(decision["reservations"].values())), 3)
        self.assertLess(switch["score"][5], 0)

    def test_burned_reserve_can_pivot_when_it_survives_hit_and_residual(self):
        cert = certificate()
        cert["compact_state"]["party"][1]["status"] = 16
        cert["legal_actions"][-1]["status"] = 16
        decision = BattlePolicy(self.profile()).decide(cert)
        switch = next(item for item in decision["candidates"] if item["action"]["kind"] == "switch")
        self.assertTrue(switch["safe"])
        self.assertNotIn("status:major_status_voluntary_switch", switch["forbidden_by"])
        self.assertLess(switch["score"][5], 0)

    def test_unmodeled_status_move_requires_explicit_strategy(self):
        cert = certificate()
        cert["alternatives"][1].update({
            "damage_range": [0, 0], "damage_est": 0,
            "uncertainties": ["status_move_effect_unmodeled"],
        })
        profile = self.profile()
        profile["constraints"] = []
        decision = BattlePolicy(profile).decide(cert)
        status = next(item for item in decision["candidates"] if item["action"].get("move_id") == 11)
        self.assertIn("mechanics:unmodeled_status_move_requires_strategy", status["forbidden_by"])

    def test_unsafe_fallback_prefers_higher_survival_margin(self):
        cert = certificate()
        cert["state"]["player"]["hp"] = 20
        cert["compact_state"]["battle"]["mons"][0]["hp"] = 20
        cert["compact_state"]["battle"]["mons"][1].update({
            "moves": [351],
            "pp": [5],
            "types": [13, 13, 9],
            "level": 12,
            "special_attack": 20,
        })
        cert["compact_state"]["party"][0]["hp"] = 20
        cert["compact_state"]["party"][1].update({
            "hp": 10,
            "types": [0, 2, 9],
            "defense": 15,
            "special_defense": 15,
            "level": 12,
        })
        cert["legal_actions"][-1]["hp"] = 10
        cert["incoming"]["max_damage_est"] = 20
        cert["incoming"]["critical_max_damage_est"] = 40
        profile = self.profile()
        profile["constraints"].append({
            "id": "bad-unsafe-pivot",
            "when": {"active_role": "lead", "opponent_species": [2]},
            "directives": [{"kind": "prefer", "action": {"kind": "switch", "role": "reserve"}}],
        })
        policy = BattlePolicy(profile)
        policy.history.record(
            certificate(player_species=777, opponent_species=192),
            {"kind": "switch", "slot": 4, "species": 543},
        )
        decision = policy.decide(cert)
        self.assertEqual(decision["action"]["kind"], "move")
        move = next(item for item in decision["candidates"] if item["action"]["kind"] == "move")
        switch = next(item for item in decision["candidates"] if item["action"]["kind"] == "switch")
        self.assertLess(move["survival_margin"], 0)
        self.assertLess(switch["survival_margin"], move["survival_margin"])
        self.assertIn("safety:unsafe_voluntary_switch", switch["forbidden_by"])

    def test_slower_switch_must_survive_entry_and_next_hit_before_acting(self):
        cert = certificate()
        cert["compact_state"]["battle"]["mons"][1].update({
            "level": 12, "speed": 20, "attack": 20, "types": [0, 0, 9], "moves": [33], "pp": [5],
        })
        cert["compact_state"]["party"][1].update({
            "level": 12, "speed": 10, "hp": 25, "defense": 20, "types": [0, 0, 9], "moves": [33], "pp": [5],
        })
        cert["legal_actions"][-1]["hp"] = 25
        profile = self.profile()
        profile["constraints"] = []
        decision = BattlePolicy(profile).decide(cert)
        switch = next(item for item in decision["candidates"] if item["action"]["kind"] == "switch")
        self.assertFalse(switch["safe"])
        self.assertIn("safety:unsafe_voluntary_switch", switch["forbidden_by"])
        self.assertEqual(decision["action"]["kind"], "move")

    def test_gavi_bugatti_stays_in_before_birdlaw_dustox(self):
        cert = certificate(player_species=543, opponent_species=269, move_ids=(205, 450), opponent_hp=52)
        cert["compact_state"]["battle"]["mons"] = [
            {"slot": 0, "species": 543, "level": 17, "hp": 31, "max_hp": 41, "speed": 27,
             "attack": 21, "defense": 28, "special_defense": 21, "types": [6, 3, 9],
             "moves": [205, 450], "pp": [20, 17]},
            {"slot": 1, "species": 269, "level": 17, "hp": 52, "max_hp": 52, "speed": 32,
             "special_attack": 27, "defense": 37, "special_defense": 40, "types": [6, 3, 9],
             "moves": [474, 611, 355, 92], "pp": [16, 20, 5, 10]},
        ]
        cert["compact_state"]["party"] = [
            {"slot": 0, "species": 543, "level": 17, "hp": 31, "max_hp": 41, "status": 0},
            {"slot": 5, "species": 397, "level": 17, "hp": 46, "max_hp": 46, "speed": 36,
             "attack": 31, "defense": 24, "special_defense": 23, "types": [0, 2, 9],
             "moves": [283, 45, 98, 332], "pp": [5, 10, 30, 20], "status": 0},
        ]
        cert["legal_actions"] = [
            {"kind": "move", "slot": 0, "move_id": 205, "pp": 20},
            {"kind": "switch", "slot": 5, "species": 397, "hp": 46, "status": 0},
        ]
        cert["alternatives"] = [
            {"slot": 0, "move_id": 205, "damage_range": [6, 8], "damage_est": 7,
             "ko_before_hit": False, "evidence": {"kind": "rom_formula"}},
        ]
        cert["incoming"] = {"max_damage_est": 11, "critical_max_damage_est": 22}
        profile = load_profile(Path("games/run_and_bun/policy_profiles/gavi_bugatti.json"))
        decision = BattlePolicy(profile, strategies=load_strategies()).decide(cert)
        switch = next(item for item in decision["candidates"] if item["action"]["kind"] == "switch")
        self.assertIn("bugatti-rollout-before-birdlaw-dustox", switch["forbidden_by"])
        self.assertEqual(decision["action"]["kind"], "move")
        self.assertEqual(decision["action"]["move_id"], 205)

    def test_unmodeled_rollout_lock_requires_explicit_strategy(self):
        cert = certificate(player_species=543, opponent_species=269, move_ids=(205, 450), opponent_hp=52)
        cert["alternatives"][0].update({"move_id": 205, "damage_range": [4, 5], "damage_est": 4.5})
        decision = BattlePolicy(self.profile()).decide(cert)
        rollout = next(item for item in decision["candidates"] if item["action"].get("move_id") == 205)
        self.assertIn("mechanics:unmodeled_locked_move_requires_strategy", rollout["forbidden_by"])

    def test_gavi_low_bibarel_finish_does_not_spend_birdlaw(self):
        cert = certificate(player_species=777, opponent_species=400, move_ids=(252, 609), opponent_hp=11)
        cert["compact_state"]["battle"]["mons"][0].update({"personality": 3325952935, "hp": 25, "max_hp": 50})
        cert["compact_state"]["party"] = [
            {"slot": 0, "species": 777, "personality": 3325952935, "hp": 25, "max_hp": 50, "status": 0},
            {"slot": 5, "species": 397, "personality": 1813210531, "hp": 46, "max_hp": 46, "status": 0},
        ]
        cert["legal_actions"] = [
            {"kind": "move", "slot": 0, "move_id": 252, "pp": 5},
            {"kind": "move", "slot": 1, "move_id": 609, "pp": 20},
            {"kind": "move", "slot": 2, "move_id": 232, "pp": 35},
            {"kind": "move", "slot": 3, "move_id": 209, "pp": 19},
            {"kind": "switch", "slot": 5, "species": 397, "personality": 1813210531, "hp": 46, "status": 0},
        ]
        cert["alternatives"] = [{
            "slot": 3, "move_id": 209, "damage_range": [32, 37], "damage_est": 34.5,
            "ko_before_hit": False, "evidence": {"kind": "rom_formula"},
        }]
        cert["incoming"] = {"max_damage_est": 50, "critical_max_damage_est": 100, "moves": []}
        decision = BattlePolicy(load_profile("games/run_and_bun/policy_profiles/gavi_bugatti.json"), strategies=load_strategies()).decide(cert)
        self.assertEqual(decision["action"]["move_id"], 209)
        self.assertIn("ohmnomnom-finish-low-bibarel-with-spark", decision["applied_strategy_ids"])

    def test_gavi_forced_elektrik_finisher_preserves_lildozer(self):
        cert = certificate(player_species=388, opponent_species=603, opponent_hp=16)
        cert["boundary"]["party_switch_required"] = True
        cert["compact_state"]["party"] = [
            {"slot": 2, "species": 777, "personality": 3325952935, "hp": 17, "max_hp": 50, "status": 0},
            {"slot": 3, "species": 231, "personality": 1744238455, "hp": 58, "max_hp": 58, "status": 0},
        ]
        cert["legal_actions"] = [
            {"kind": "switch", "slot": 2, "species": 777, "personality": 3325952935, "hp": 17, "status": 0},
            {"kind": "switch", "slot": 3, "species": 231, "personality": 1744238455, "hp": 58, "status": 0},
        ]
        decision = BattlePolicy(load_profile("games/run_and_bun/policy_profiles/gavi_bugatti.json"), strategies=load_strategies()).decide(cert)
        self.assertEqual(decision["action"]["species"], 777)
        self.assertIn("forced-ohmnomnom-finisher-after-bushtank-elektrik", decision["applied_strategy_ids"])

    def test_gavi_forced_replacement_uses_bugatti_for_dustox_rollout(self):
        cert = certificate(player_species=397, opponent_species=269)
        cert["boundary"]["party_switch_required"] = True
        cert["compact_state"]["party"] = [
            {"slot": 1, "species": 453, "personality": 506087644, "hp": 46, "max_hp": 46,
             "level": 17, "speed": 22, "special_attack": 28, "special_defense": 16,
             "types": [1, 3, 9], "moves": [124, 341, 252, 410], "pp": [20, 15, 5, 30], "status": 0},
            {"slot": 4, "species": 543, "personality": 2609223149, "hp": 41, "max_hp": 41,
             "level": 17, "speed": 27, "attack": 21, "defense": 28, "special_defense": 21,
             "types": [6, 3, 9], "moves": [205, 182, 450, 342], "pp": [20, 10, 20, 25], "status": 0},
        ]
        cert["compact_state"]["battle"]["mons"][1].update({
            "level": 17, "speed": 32, "special_attack": 27, "defense": 37,
            "special_defense": 40, "types": [6, 3, 9],
            "moves": [474, 611, 355, 92], "pp": [16, 20, 5, 10],
        })
        cert["legal_actions"] = [
            {"kind": "switch", "slot": 1, "species": 453, "hp": 46, "status": 0, "personality": 506087644},
            {"kind": "switch", "slot": 4, "species": 543, "hp": 41, "status": 0, "personality": 2609223149},
        ]
        profile = load_profile(Path("games/run_and_bun/policy_profiles/gavi_bugatti.json"))
        decision = BattlePolicy(profile, strategies=load_strategies()).decide(cert)
        self.assertEqual(decision["action"]["species"], 543)
        self.assertIn("forced-bugatti-rollout-dustox", decision["applied_strategy_ids"])

    def test_gavi_bugatti_commits_only_before_sunflora_attrition(self):
        profile = load_profile("games/run_and_bun/policy_profiles/gavi_bugatti.json")
        policy = BattlePolicy(profile, strategies=load_strategies())
        cert = certificate(player_species=543, opponent_species=192)
        cert["compact_state"]["battle"]["mons"][0]["personality"] = 2609223149
        cert["compact_state"]["party"][0]["personality"] = 2609223149

        _preferences, full_hp_vetoes, _reservations, applied = policy._directive_effects(cert)
        self.assertIn("bugatti-commit-bug-bite-sunflora", applied)
        self.assertTrue(full_hp_vetoes)

        cert["state"]["player"]["hp"] = 25
        cert["compact_state"]["battle"]["mons"][0]["hp"] = 25
        _preferences, worn_vetoes, _reservations, applied = policy._directive_effects(cert)
        self.assertNotIn("bugatti-commit-bug-bite-sunflora", applied)
        self.assertFalse(worn_vetoes)

    def test_verified_defeat_releases_one_reserve_and_uses_current_matchup_answer(self):
        policy = BattlePolicy(load_profile("games/run_and_bun/policy_profiles/gavi_bugatti.json"), strategies=load_strategies())
        defeated = certificate(player_species=453, opponent_species=603)
        defeated["compact_state"]["battle"]["mons"][0]["personality"] = 506087644
        policy.record_verified(defeated, {"kind": "move", "slot": 0, "move_id": 124}, {
            "observation": {"battle": {"mons": [{"slot": 1, "state": {"species": 192, "current_hp": 54}}]}},
        })
        self.assertIn(603, policy.history.snapshot()["defeated_species"])

        sunflora = certificate(player_species=543, opponent_species=192)
        sunflora["compact_state"]["battle"]["mons"][0].update({"personality": 2609223149, "status": 3})
        sunflora["compact_state"]["party"] = [
            {"slot": 0, "species": 543, "personality": 2609223149, "hp": 30, "status": 3},
            {"slot": 1, "species": 453, "personality": 506087644, "hp": 17, "status": 0},
            {"slot": 2, "species": 397, "personality": 1813210531, "hp": 46, "status": 0},
        ]
        sunflora["legal_actions"] = [
            {"kind": "switch", "slot": 1, "species": 453, "personality": 506087644, "hp": 17, "status": 0},
            {"kind": "switch", "slot": 2, "species": 397, "personality": 1813210531, "hp": 46, "status": 0},
        ]
        decision = policy.decide(sunflora)
        self.assertEqual(decision["action"]["species"], 397)

    def test_flattened_post_observation_does_not_invent_an_opponent_defeat(self):
        policy = BattlePolicy(self.profile())
        cert = certificate(opponent_species=2)
        policy.record_verified(cert, {"kind": "move", "slot": 0, "move_id": 10}, {
            "observation": {"battle": {"mons": [
                {"slot": 0, "species": 1, "hp": 26},
                {"slot": 1, "species": 2, "hp": 15},
            ]}},
        })
        self.assertNotIn(2, policy.history.snapshot()["defeated_species"])

    def test_forced_replacement_prefers_priority_progress_over_zero_action_sack(self):
        cert = certificate(player_species=543, opponent_species=269)
        cert["boundary"]["party_switch_required"] = True
        cert["compact_state"]["battle"]["mons"][1].update({
            "level": 17, "speed": 32, "defense": 30, "special_attack": 35, "special_defense": 30,
            "types": [6, 3, 9], "moves": [474], "pp": [5],
        })
        cert["compact_state"]["party"] = [
            {"slot": 0, "species": 543, "hp": 0, "max_hp": 41, "status": 0},
            {"slot": 1, "species": 388, "hp": 9, "max_hp": 57, "level": 17, "speed": 19, "special_defense": 35, "moves": [44], "pp": [25], "status": 0},
            {"slot": 2, "species": 453, "hp": 19, "max_hp": 46, "level": 17, "speed": 22, "special_attack": 28, "special_defense": 16, "types": [1, 3, 9], "moves": [410], "pp": [30], "status": 0},
        ]
        cert["legal_actions"] = [
            {"kind": "switch", "slot": 1, "species": 388, "hp": 9, "status": 0},
            {"kind": "switch", "slot": 2, "species": 453, "hp": 19, "status": 0},
        ]
        profile = load_profile(Path("games/run_and_bun/policy_profiles/gavi.json"))
        decision = BattlePolicy(profile, strategies=load_strategies()).decide(cert)
        self.assertEqual(decision["action"]["species"], 453)
        self.assertTrue(decision["selected"]["progress_before_faint"])

    def test_fixed_damage_is_not_doubled_as_critical_bound(self):
        self.assertEqual(_critical_damage_bound(162, 26), 26)
        self.assertEqual(_critical_damage_bound(351, 26), 52)

    def test_two_hit_switch_survival_recomputes_super_fang_current_hp(self):
        attacker = {
            "species": 603, "level": 17, "special_attack": 35,
            "types": (13,), "moves": (162, 351),
        }
        defender = {
            "species": 388, "current_hp": 57, "level": 17,
            "special_defense": 35, "types": (12,),
        }
        self.assertGreater(_survival_margin_after_hits((162, 351), attacker, defender, 2), 0)

    def test_definite_sleep_can_escape_profile_switch_veto(self):
        cert = certificate(player_species=543, opponent_species=192, move_ids=(450, 342), opponent_hp=27)
        cert["compact_state"]["battle"]["mons"][0].update({
            "status": 1, "level": 17, "speed": 27, "attack": 21, "types": [6, 3],
        })
        cert["compact_state"]["battle"]["mons"][1].update({
            "level": 16, "speed": 19, "special_attack": 43, "types": [12],
            "moves": [188], "pp": [20],
        })
        cert["compact_state"]["party"][1].update({
            "species": 453, "hp": 46, "max_hp": 46, "level": 17, "speed": 22,
            "special_attack": 28, "special_defense": 16, "types": [3, 1], "moves": [410], "pp": [30], "status": 16,
        })
        cert["legal_actions"][2].update({"species": 453, "hp": 46, "status": 16})
        profile = self.profile()
        profile["roles"] = {"bugatti": {"species": 543}, "converter": {"species": 453}}
        profile["reserve_objectives"] = []
        profile["constraints"] = [{
            "id": "commit-while-awake",
            "when": {"active_role": "bugatti", "opponent_species": [192]},
            "directives": [
                {"kind": "prefer", "action": {"kind": "move", "move_id": 450}},
                {"kind": "forbid", "action": {"kind": "switch", "role": "converter"}},
            ],
        }]
        decision = BattlePolicy(profile).decide(cert)
        self.assertEqual(decision["action"]["kind"], "switch")
        self.assertEqual(decision["action"]["species"], 453)
        self.assertNotIn("status:major_status_voluntary_switch", decision["selected"]["forbidden_by"])
        self.assertEqual(next(item for item in decision["candidates"] if item["action"]["kind"] == "move")["evidence"], "status_prevents_next_action")

    def test_forced_switch_avoids_sleeping_target_when_teammate_can_act(self):
        cert = certificate()
        cert["boundary"]["party_switch_required"] = True
        cert["compact_state"]["party"] = [
            {"slot": 1, "species": 3, "hp": 25, "status": 4},
            {"slot": 2, "species": 4, "hp": 20, "status": 0},
        ]
        cert["legal_actions"] = [
            {"kind": "switch", "slot": 1, "species": 3, "hp": 25, "status": 4},
            {"kind": "switch", "slot": 2, "species": 4, "hp": 20, "status": 0},
        ]
        decision = BattlePolicy(self.profile()).decide(cert)
        sleeping = next(item for item in decision["candidates"] if item["action"]["species"] == 3)
        self.assertIn("status:incapacitated_switch_target", sleeping["forbidden_by"])
        self.assertEqual(decision["action"]["species"], 4)

    def test_guaranteed_priority_chip_beats_damage_that_cannot_execute(self):
        cert = certificate()
        cert["legal_actions"] = cert["legal_actions"][:2]
        cert["alternatives"][0].update({"damage_range": [10, 12], "damage_est": 11, "acts_first": False})
        cert["alternatives"][1].update({"damage_range": [3, 4], "damage_est": 3.5, "acts_first": True})
        cert["incoming"].update({"max_damage_est": 30, "critical_max_damage_est": 60})
        profile = self.profile()
        profile["constraints"] = []
        self.assertEqual(BattlePolicy(profile).decide(cert)["action"]["move_id"], 11)

    def test_lethal_priority_chip_beats_slower_nominal_ko(self):
        cert = certificate(player_species=453, opponent_species=603, move_ids=(124, 410), opponent_hp=9)
        cert["alternatives"] = [
            {"slot": 0, "move_id": 124, "damage_range": [12, 13], "damage_est": 12.5,
             "acts_first": False, "ko_before_hit": False, "evidence": {"kind": "rom_formula"}},
            {"slot": 1, "move_id": 410, "damage_range": [7, 8], "damage_est": 7.5,
             "acts_first": True, "ko_before_hit": False, "evidence": {"kind": "rom_formula"}},
        ]
        cert["legal_actions"] = cert["legal_actions"][:2]
        cert["state"]["player"]["hp"] = 16
        cert["compact_state"]["battle"]["mons"][0]["hp"] = 16
        cert["incoming"] = {"max_damage_est": 34, "critical_max_damage_est": 68}
        profile = self.profile()
        profile["constraints"] = []
        decision = BattlePolicy(profile).decide(cert)
        self.assertEqual(decision["action"]["move_id"], 410)
        self.assertTrue(decision["selected"]["progress_before_faint"])

    def test_safe_attack_does_not_block_a_stronger_safe_matchup_switch(self):
        cert = certificate()
        cert["compact_state"]["battle"]["mons"][1].update({"level": 10, "attack": 10, "defense": 10, "types": [0, 0, 9]})
        cert["compact_state"]["party"][1].update({"level": 20, "attack": 100, "defense": 30, "types": [0, 0, 9], "moves": [98], "pp": [30]})
        profile = self.profile()
        profile["constraints"] = []
        profile["reserve_objectives"] = []
        self.assertEqual(BattlePolicy(profile).decide(cert)["action"]["kind"], "switch")

    def test_future_switch_speed_does_not_beat_stronger_immediate_safe_damage(self):
        cert = certificate()
        cert["alternatives"][0].update({"damage_range": [32, 35], "damage_est": 33.5})
        cert["compact_state"]["party"][1].update({
            "speed": 99, "attack": 20, "special_attack": 20, "moves": [98], "pp": [30],
        })
        profile = self.profile()
        profile["constraints"] = []
        profile["reserve_objectives"] = []
        decision = BattlePolicy(profile).decide(cert)
        self.assertEqual(decision["action"]["kind"], "move")
        self.assertEqual(decision["action"]["move_id"], 10)

    def test_unsupported_predicate_is_rejected(self):
        profile = self.profile()
        profile["constraints"][0]["when"]["arbitrary_expression"] = "true"
        self.assertTrue(any("unsupported" in error for error in validate_profile(profile)))
        with self.assertRaises(PolicyError):
            BattlePolicy(profile)

    def test_switch_scorer_fills_party_types_from_species_when_ram_omits_them(self):
        state = _battle_state({"species": 878, "types": None, "hp": 34, "max_hp": 54})
        self.assertTrue(state["types"])

    def test_gavi_croakatoa_fake_out_is_allowed_only_on_fresh_entry(self):
        profile = load_profile(Path("games/run_and_bun/policy_profiles/gavi.json"))
        policy = BattlePolicy(profile, strategies=load_strategies())
        cert = certificate(player_species=453, opponent_species=269, move_ids=(252, 124))
        first = policy.decide(cert)
        fresh_fake_out = next(item for item in first["candidates"] if item["action"].get("move_id") == 252)
        self.assertFalse(fresh_fake_out["forbidden_by"])
        policy.record_verified(cert, {"kind": "move", "slot": 1, "move_id": 124})
        second = policy.decide(cert)
        fake_out = next(item for item in second["candidates"] if item["action"].get("move_id") == 252)
        self.assertIn("mechanics:fake_out_requires_fresh_entry", fake_out["forbidden_by"])

    def test_fake_out_is_generically_forbidden_after_any_action(self):
        profile = self.profile()
        profile["constraints"] = []
        profile["reserve_objectives"] = []
        policy = BattlePolicy(profile)
        cert = certificate(player_species=1, opponent_species=2, move_ids=(252, 209), opponent_hp=3)
        policy.record_verified(cert, {"kind": "move", "slot": 1, "move_id": 209})
        decision = policy.decide(cert)
        fake_out = next(item for item in decision["candidates"] if item["action"].get("move_id") == 252)
        self.assertIn("mechanics:fake_out_requires_fresh_entry", fake_out["forbidden_by"])
        self.assertEqual(decision["action"]["move_id"], 209)

    def test_gavi_forbids_bushtank_bite_into_ponyta(self):
        profile = load_profile(Path("games/run_and_bun/policy_profiles/gavi.json"))
        policy = BattlePolicy(profile, strategies=load_strategies())
        decision = policy.decide(certificate(player_species=388, opponent_species=77, move_ids=(44, 75)))
        bite = next(item for item in decision["candidates"] if item["action"].get("move_id") == 44)
        self.assertIn("avoid-bushtank-bite-into-ponyta-flame-body", bite["forbidden_by"])
        self.assertNotEqual(decision["action"].get("move_id"), 44)

    def test_gavi_fake_out_is_preferred_once_on_fresh_ohmnomnom_entry(self):
        profile = load_profile(Path("games/run_and_bun/policy_profiles/gavi.json"))
        policy = BattlePolicy(profile, strategies=load_strategies())
        cert = certificate(player_species=777, opponent_species=192, move_ids=(252, 232))
        first = policy.choose(cert)
        policy.record_verified(cert, first)
        second = policy.choose(cert)
        self.assertEqual(first["move_id"], 252)
        self.assertEqual(second["move_id"], 232)

    def test_fresh_gavi_croakatoa_fake_out_is_safe_against_modeled_reply(self):
        profile = load_profile(Path("games/run_and_bun/policy_profiles/gavi.json"))
        policy = BattlePolicy(profile, strategies=load_strategies())
        decision = policy.decide(certificate(player_species=453, opponent_species=603, move_ids=(252, 124)))
        self.assertEqual(decision["action"]["move_id"], 252)
        selected = next(item for item in decision["candidates"] if item["action"] == decision["action"])
        self.assertEqual(selected["evidence"], "fresh_entry_flinch")

    def test_fresh_fake_out_guarantees_the_next_decision_without_a_return_target(self):
        profile = self.profile()
        profile["constraints"] = []
        profile["reserve_objectives"] = []
        cert = certificate(player_species=1, opponent_species=2, move_ids=(252, 10))
        cert["compact_state"]["battle"]["mons"][0]["hp"] = 4
        cert["compact_state"]["party"] = [cert["compact_state"]["party"][0]]
        cert["legal_actions"] = cert["legal_actions"][:2]
        cert["incoming"].update({"max_damage_est": 6, "critical_max_damage_est": 12})
        fake_out = next(
            item for item in BattlePolicy(profile).decide(cert)["candidates"]
            if item["action"].get("move_id") == 252
        )
        self.assertTrue(fake_out["safe"])
        self.assertTrue(fake_out["continuation"]["next_action_reachable"])
        self.assertEqual(fake_out["evidence"], "fresh_entry_flinch")

    def test_shield_dust_does_not_certify_fake_out_flinch_safety(self):
        profile = load_profile(Path("games/run_and_bun/policy_profiles/gavi_coppertop.json"))
        cert = certificate(player_species=453, opponent_species=269, move_ids=(252, 410))
        cert["compact_state"]["battle"]["mons"][1]["ability"] = 19
        cert["incoming"].update({"max_damage_est": 30, "critical_max_damage_est": 60})
        decision = BattlePolicy(profile, strategies=load_strategies()).decide(cert)
        fake_out = next(item for item in decision["candidates"] if item["action"].get("move_id") == 252)
        self.assertFalse(fake_out["safe"])
        self.assertNotEqual(fake_out["evidence"], "fresh_entry_flinch")

    def test_inner_focus_does_not_certify_fake_out_flinch_safety(self):
        profile = load_profile(Path("games/run_and_bun/policy_profiles/gavi_coppertop.json"))
        cert = certificate(player_species=453, opponent_species=269, move_ids=(252, 410))
        cert["compact_state"]["battle"]["mons"][1]["ability"] = 39
        cert["incoming"].update({"max_damage_est": 30, "critical_max_damage_est": 60})
        decision = BattlePolicy(profile, strategies=load_strategies()).decide(cert)
        fake_out = next(item for item in decision["candidates"] if item["action"].get("move_id") == 252)
        self.assertFalse(fake_out["safe"])
        self.assertNotEqual(fake_out["evidence"], "fresh_entry_flinch")

    def test_gavi_low_dustox_forced_finish_uses_ohmnomnom(self):
        cert = certificate(player_species=397, opponent_species=269, opponent_hp=8)
        cert["boundary"]["party_switch_required"] = True
        cert["compact_state"]["party"] = [
            {"slot": 0, "species": 397, "hp": 0, "status": 0},
            {"slot": 1, "species": 777, "hp": 16, "status": 0},
            {"slot": 2, "species": 388, "hp": 57, "status": 0},
            {"slot": 3, "species": 453, "hp": 44, "status": 0},
            {"slot": 4, "species": 878, "hp": 56, "status": 0},
        ]
        cert["legal_actions"] = [
            {"kind": "switch", "slot": mon["slot"], "species": mon["species"], "hp": mon["hp"], "status": 0}
            for mon in cert["compact_state"]["party"][1:]
        ]
        profile = load_profile(Path("games/run_and_bun/policy_profiles/gavi_coppertop.json"))
        self.assertEqual(BattlePolicy(profile, strategies=load_strategies()).decide(cert)["action"]["species"], 777)


class BattleReviewTests(unittest.TestCase):
    @staticmethod
    def complete_agent_review(review):
        review["agent_review"] = {
            "status": "complete",
            "author": "agent",
            "reviewed_actions": review["certified_actions"],
            "findings": [],
            "correct_choices": ["verified transaction sequence reviewed"],
            "next_test": "retain the reusable line and monitor the next fight",
        }
        return review

    def transition(self, *, uncertainty=False):
        decision = {
            "policy_id": "fixture-policy",
            "behavior_hash": "b" * 64,
            "action": {"kind": "move", "slot": 0, "move_id": 10},
            "selected": {"action": {"kind": "move", "slot": 0, "move_id": 10}, "score": [1, 1, 0, 0]},
            "candidates": [
                {"action": {"kind": "move", "slot": 0, "move_id": 10}, "score": [1, 1, 0, 0]},
                {"action": {"kind": "switch", "slot": 1, "species": 3}, "score": [1, 1, 0, 0]},
            ],
            "uncertainties": {"action_changing": ["tie"] if uncertainty else [], "other": []},
        }
        return {
            "action_id": "a1",
            "pre_state_hash": "state-1",
            "policy_decision": decision,
            "actual": {"allied_action_outcome": "executed"},
            "discrepancies": [],
        }

    def test_clean_win_qualifies_only_without_action_changing_findings(self):
        review = review_episode([self.transition()], terminal="win", source="cartridge_clone", opening_state_hash="open", behavior_hash="b" * 64, policy_id="fixture")
        self.assertEqual(review["status"], "clean")
        self.assertEqual(review["postmortem"]["what_went_wrong"], [])
        self.assertTrue(any(item["kind"] == "no_action_changing_defect" for item in review["postmortem"]["what_could_improve"]))
        self.assertTrue(all("severity" in item for item in review["postmortem"]["prioritized_items"]))
        with tempfile.TemporaryDirectory() as directory:
            ledger = QualificationLedger(Path(directory) / "qualification.json")
            self.assertTrue(ledger.record(review)["pending_review"])
            for _ in range(2):
                review = self.complete_agent_review(review_episode([self.transition()], terminal="win", source="cartridge_clone", opening_state_hash="open", behavior_hash="b" * 64, policy_id="fixture"))
                state = ledger.record(review)
            self.assertFalse(state["ready"])
            review = self.complete_agent_review(review_episode([self.transition()], terminal="win", source="cartridge_clone", opening_state_hash="open", behavior_hash="b" * 64, policy_id="fixture"))
            self.assertTrue(ledger.record(review)["ready"])

    def test_uncertainty_creates_deduplicated_counterfactual_queue(self):
        review = review_episode([self.transition(uncertainty=True), self.transition(uncertainty=True)], terminal="win", source="cartridge_clone", opening_state_hash="open", behavior_hash="b" * 64, policy_id="fixture")
        self.assertEqual(review["status"], "needs_improvement")
        self.assertEqual(len(review["counterfactuals"]), 1)

    def test_vetoed_candidates_are_not_reviewed_as_missed_actions(self):
        transition = self.transition()
        decision = transition["policy_decision"]
        decision["history"] = {"fresh_entry": True}
        decision["applied_strategy_ids"] = ["strategy:fixture"]
        decision["candidates"].append({
            "action": {"kind": "move", "slot": 0, "move_id": 252},
            "score": [9, 9, 9, 9],
            "forbidden_by": ["strategy:fixture"],
        })
        review = review_episode([transition], terminal="win", source="cartridge_clone", opening_state_hash="open", behavior_hash="b" * 64, policy_id="fixture")
        self.assertEqual(review["status"], "clean")
        self.assertFalse(any(item["kind"] in {"tactical_error", "suboptimal_action"} for item in review["findings"]))

    def test_persist_review_writes_one_compact_artifact(self):
        transition = self.transition()
        transition["policy_decision"].update({
            "applied_strategy_ids": ["strategy:fixture"],
            "influential_strategy_ids": ["fixture"],
        })
        review = review_episode([transition], terminal="win", source="cartridge_clone", opening_state_hash="open", behavior_hash="b" * 64, policy_id="fixture")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = persist_review(review, root / "reviews")
            self.assertEqual(artifact.name, f"{review['review_id']}.json")
            stored = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(stored["review_id"], review["review_id"])
            self.assertNotIn("findings", stored)
            self.assertNotIn("postmortem", stored)
            self.assertNotIn("counterfactuals", stored)
            self.assertNotIn("observations", stored)
            self.assertEqual(stored["influential_strategy_ids"], ["fixture"])

    def test_create_battle_review_builds_compact_agent_checklist(self):
        review = build_review(
            terminal="win",
            source="live",
            opening_state_hash="open",
            policy_id="wild_capture_fixture",
            certified_actions=2,
            findings=[{
                "kind": "resource_tempo",
                "severity": "low",
                "summary": "one ball was spent before the catch",
                "action_changing": False,
            }],
            correct_choices=["used the ROM encounter table"],
            next_test="keep the bounded hunt cap",
        )
        self.assertEqual(review["status"], "clean")
        self.assertEqual(review["agent_review"]["schema_version"], 3)
        self.assertEqual(review["agent_review"]["findings"][0]["kind"], "resource_loss")
        self.assertEqual(review["agent_review"]["findings"][0]["knowledge_target"], "scorer")
        self.assertTrue(agent_review_complete(review))
        self.assertTrue(review["review_id"])
        with tempfile.TemporaryDirectory() as directory:
            artifact = write_review_artifact(review, Path(directory) / "reviews")
            stored = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(stored["agent_review"]["reviewed_actions"], 2)
            self.assertEqual(len(stored["agent_review"]["findings"]), 1)

    def test_new_review_taxonomy_rejects_unknown_and_unlinked_strategy_findings(self):
        common = dict(
            terminal="loss", source="live", opening_state_hash="open",
            policy_id="fixture", certified_actions=1, correct_choices=[], next_test="replay",
        )
        with self.assertRaisesRegex(ValueError, "unsupported finding kind"):
            build_review(findings=[{"kind": "freeform_mistake", "severity": "low", "summary": "bad"}], **common)
        with self.assertRaisesRegex(ValueError, "strategy_id"):
            build_review(findings=[{
                "kind": "policy_gap", "severity": "medium", "summary": "missing rule",
                "action_changing": True,
            }], **common)
        linked = build_review(findings=[{
            "kind": "policy_gap", "severity": "medium", "summary": "missing rule",
            "action_changing": True, "candidate_strategy_id": "safe-entry-tempo",
        }], **common)
        self.assertTrue(agent_review_complete(linked))

    def test_clone_review_gate_requires_latest_review_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertTrue(clone_review_gate(root / "reviews", root / "qualification.json")["allowed"])
            review = review_episode([self.transition()], terminal="loss", source="cartridge_clone", opening_state_hash="open", behavior_hash="b" * 64, policy_id="fixture")
            write_review_artifact(review, root / "reviews")
            self.assertFalse(clone_review_gate(root / "reviews", root / "qualification.json")["allowed"])
            self.complete_agent_review(review)
            write_review_artifact(review, root / "reviews")
            gate = clone_review_gate(root / "reviews", root / "qualification.json")
            self.assertTrue(gate["allowed"])
            self.assertEqual(gate["review_id"], review["review_id"])

    def test_clone_review_gate_ignores_unrelated_trainer_reviews(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            review = self.complete_agent_review(review_episode(
                [self.transition()], terminal="loss", source="cartridge_clone",
                opening_state_hash="open", behavior_hash="b" * 64,
                policy_id="fixture", trainer_key="gavi",
            ))
            write_review_artifact(review, root / "reviews")
            unrelated = self.complete_agent_review(review_episode(
                [self.transition()], terminal="win", source="live",
                opening_state_hash="other", behavior_hash=None,
                policy_id="wild_capture", trainer_key=None,
            ))
            write_review_artifact(unrelated, root / "reviews")
            gavi_gate = clone_review_gate(root / "reviews", root / "qualification.json", trainer_key="gavi")
            other_gate = clone_review_gate(root / "reviews", root / "qualification.json", trainer_key="other")
            self.assertEqual(gavi_gate["review_id"], review["review_id"])
            self.assertNotEqual(gavi_gate.get("review_id"), unrelated["review_id"])
            self.assertEqual(other_gate["reason"], "no_prior_review")

    def test_loss_starts_a_new_clone_sequence(self):
        loss = review_episode([self.transition()], terminal="loss", source="cartridge_clone", opening_state_hash="open", behavior_hash="b" * 64, policy_id="fixture")
        win = review_episode([self.transition()], terminal="win", source="cartridge_clone", opening_state_hash="open", behavior_hash="b" * 64, policy_id="fixture")
        self.complete_agent_review(loss)
        self.complete_agent_review(win)
        with tempfile.TemporaryDirectory() as directory:
            ledger = QualificationLedger(Path(directory) / "qualification.json")
            self.assertEqual(ledger.record(loss)["streak"], 0)
            self.assertEqual(ledger.record(win)["streak"], 1)

    def test_live_gate_requires_three_clean_wins_for_exact_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qualification.json"
            ledger = QualificationLedger(path)
            for _ in range(3):
                review = self.complete_agent_review(review_episode([self.transition()], terminal="win", source="cartridge_clone", opening_state_hash="open", behavior_hash="b" * 64, policy_id="fixture"))
                ledger.record(review)
            self.assertTrue(live_qualification_gate("b" * 64, "open", path)["allowed"])
            self.assertFalse(live_qualification_gate("c" * 64, "open", path)["allowed"])

    def test_qualification_is_scoped_to_one_trainer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qualification.json"
            for _ in range(3):
                review = self.complete_agent_review(review_episode(
                    [self.transition()], terminal="win", source="cartridge_clone",
                    opening_state_hash="open", behavior_hash="b" * 64,
                    policy_id="fixture", trainer_key="trainer-a",
                ))
                QualificationLedger(path, fight_key="trainer-a").record(review)
            self.assertTrue(live_qualification_gate("b" * 64, "open", path, "trainer-a")["allowed"])
            self.assertFalse(live_qualification_gate("b" * 64, "open", path, "trainer-b")["allowed"])

    def test_every_finished_fight_blocks_continuation_until_reviewed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "reviews"
            reviewed = self.complete_agent_review(review_episode(
                [self.transition()], terminal="win", source="live",
                opening_state_hash="one", behavior_hash=None, policy_id="easy-one",
            ))
            reviewed["created_at"] = "2026-01-01T00:00:00+00:00"
            write_review_artifact(reviewed, root)
            pending = review_episode(
                [self.transition()], terminal="win", source="live",
                opening_state_hash="two", behavior_hash=None, policy_id="easy-two",
            )
            pending["created_at"] = "2026-01-02T00:00:00+00:00"
            write_review_artifact(pending, root)
            self.assertEqual(battle_continuation_gate(root)["reason"], "agent_battle_review_required")
            self.complete_agent_review(pending)
            write_review_artifact(pending, root)
            self.assertTrue(battle_continuation_gate(root)["allowed"])

    def test_new_agent_review_schema_requires_strategic_evidence(self):
        review = self.complete_agent_review(review_episode(
            [self.transition()], terminal="win", source="live",
            opening_state_hash="open", behavior_hash=None, policy_id="easy",
        ))
        review["agent_review"]["schema_version"] = 2
        review["agent_review"]["findings"] = [{"kind": "tempo", "severity": "low", "summary": "slow"}]
        self.assertFalse(agent_review_complete(review))
        review["agent_review"]["findings"][0].update({
            "turns": [1],
            "observed_fact": "one unnecessary switch occurred",
            "strategic_judgment": "the switch lost tempo without improving survival",
            "confidence": "verified",
            "fix_layer": "generic_tactical_scorer",
            "next_test": "replay the turn without switching",
        })
        self.assertTrue(agent_review_complete(review))

    def test_attached_high_finding_blocks_until_resolved(self):
        review = self.complete_agent_review(review_episode([self.transition()], terminal="win", source="cartridge_clone", opening_state_hash="open", behavior_hash="b" * 64, policy_id="fixture"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "turn-001.state"
            state.write_bytes(b"fixture-state")
            review["agent_review"]["findings"] = [{
                "severity": "high",
                "state": str(state),
                "observed_fact": "fixture issue",
                "next_test": "verify the fix",
            }]
            artifact = write_review_artifact(review, root / "reviews")
            stored = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertFalse(stored["agent_review"]["findings"][0].get("resolved", False))
            self.assertFalse(clone_review_gate(root / "reviews", root / "qualification.json")["allowed"])
            stored["agent_review"]["findings"][0]["resolved"] = True
            write_review_artifact(stored, root / "reviews")
            self.assertFalse(clone_review_gate(root / "reviews", root / "qualification.json")["allowed"])
            stored["agent_review"]["findings"][0]["resolution"] = {"verified": True, "observed": "the corrected action was observed from the saved state"}
            write_review_artifact(stored, root / "reviews")
            self.assertTrue(clone_review_gate(root / "reviews", root / "qualification.json")["allowed"])

    def test_review_resolution_cli_helper_requires_unique_state_backed_finding(self):
        review = {"agent_review": {"findings": [{"kind": "policy_gap", "state": "turn.state", "resolved": False}]}}
        resolve_findings(review, [["policy_gap", "post", "correct action executed"]])
        finding = review["agent_review"]["findings"][0]
        self.assertTrue(finding["resolved"])
        self.assertEqual(finding["resolution"]["post_state_hash"], "post")

    def test_postmortem_does_not_promote_weaker_fresh_fake_out(self):
        transition = self.transition()
        decision = transition["policy_decision"]
        decision["history"] = {"fresh_entry": True}
        decision["applied_strategy_ids"] = ["strategy:fixture"]
        decision["action"] = {"kind": "move", "slot": 0, "move_id": 10}
        decision["selected"]["action"] = decision["action"]
        decision["candidates"].append({"action": {"kind": "move", "slot": 0, "move_id": 252}, "score": [0, 0, 0, 0]})
        review = review_episode([transition], terminal="win", source="cartridge_clone", opening_state_hash="open", behavior_hash="b" * 64, policy_id="fixture")
        self.assertFalse(any(item["kind"] == "tactical_error" for item in review["findings"]))

    def test_loss_resets_qualification_and_records_cause(self):
        review = review_episode([self.transition()], terminal="loss", source="cartridge_clone", opening_state_hash="open", behavior_hash="b" * 64, policy_id="fixture")
        self.assertEqual(review["classification"], "tactical_error")
        self.assertEqual(review["postmortem"]["what_went_wrong"][0]["kind"], "tactical_error")
        tactical = next(item for item in review["postmortem"]["what_could_improve"] if item["kind"] == "tactical_error")
        self.assertEqual(tactical["layer"], "reusable_executable_strategy")
        self.assertEqual(next(item for item in review["findings"] if item["kind"] == "terminal_loss")["severity"], "critical")
        with tempfile.TemporaryDirectory() as directory:
            ledger = QualificationLedger(Path(directory) / "qualification.json")
            ledger.record(review)
            self.assertEqual(ledger.state["streak"], 0)

    def test_postmortem_blocks_qualification_when_loss_is_action_changing(self):
        review = review_episode([self.transition()], terminal="loss", source="cartridge_clone", opening_state_hash="open", behavior_hash="b" * 64, policy_id="fixture")
        self.assertEqual(review["postmortem"]["next_step"], "apply every action-changing improvement before retry")
        self.assertTrue(all("severity" in item for item in review["postmortem"]["what_could_improve"]))

    def test_faint_observation_excludes_foe_faint(self):
        transition = self.transition()
        transition["actual"]["resolution"] = {
            "feedback": "Foe Bibarel fainted!<PROMPT_CLEAR> Foe Ponyta used Flame Wheel! Ohmnomnom fainted!",
        }
        review = review_episode([transition], terminal="loss", source="cartridge_clone", opening_state_hash="open", behavior_hash="b" * 64, policy_id="fixture")
        faints = [item for item in review["observations"] if item["kind"] == "faint_observed"]
        self.assertEqual(len(faints), 1)
        self.assertIn("Ohmnomnom", faints[0]["summary"])


if __name__ == "__main__":
    unittest.main()
