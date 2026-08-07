from __future__ import annotations

from dataclasses import dataclass
from .visual import inspect_png


@dataclass
class TurnResult:
    state: str
    move_slot: int
    move_name: str | None
    selection_accepted: bool
    move_executed: bool
    pp_before: int
    pp_after: int
    player_hp_before: int
    player_hp_after: int
    opponent_species_before: int
    opponent_species_after: int
    opponent_hp_before: int
    opponent_hp_after: int
    opponent_move_slot: int | None
    opponent_move_name: str | None
    frames: int
    text_presses: int
    opponent_changed: bool = False
    opponent_fainted: bool | None = None
    party_exp_before: tuple[int, ...] = ()
    party_exp_after: tuple[int, ...] = ()


class BattleDriver:
    """Battle state machine validated against Run & Bun v1.07.

    Selection is acknowledged from RAM changes, enemy move usage is inferred from
    opponent PP deltas, and opponent changes are cross-checked against party EXP.
    """

    def __init__(self, runbun, scratch="/mnt/data/.battle-v2.png"):
        self.rb = runbun
        self.gba = runbun.gba
        self.scratch = scratch

    def visual(self):
        self.gba.screenshot(self.scratch)
        return inspect_png(self.scratch)

    def wait_for_decision(self, max_frames=1200, sample_frames=6, advance_text=True):
        elapsed = no_battle = 0
        while elapsed <= max_frames:
            v = self.visual()
            b = self.rb.battle()
            p = self.rb.party()[0]
            if p.hp == 0:
                return {"state": "player_fainted", "battle": b, "visual": v, "frames": elapsed}
            if v.bag_menu:
                return {"state": "bag_menu", "battle": b, "visual": v, "frames": elapsed}
            if v.full_screen_menu:
                return {"state": "full_screen_menu", "battle": b, "visual": v, "frames": elapsed}
            if v.battle_command_menu:
                return {"state": "command_menu", "battle": b, "visual": v, "frames": elapsed}
            if v.battle_menu_like:
                return {"state": "move_menu", "battle": b, "visual": v, "frames": elapsed}
            if advance_text and v.battle_textbox:
                self.gba.wait_frames(2)
                elapsed += 2
                v2 = self.visual()
                if v2.battle_textbox and not v2.battle_command_menu and not v2.battle_menu_like:
                    self.gba.press("A", frames=2)
            no_battle = 0 if (v.battle_hud or v.battle_textbox) else no_battle + 1
            if no_battle * sample_frames >= 180:
                return {"state": "battle_exit", "battle": b, "visual": v, "frames": elapsed}
            self.gba.wait_frames(sample_frames)
            elapsed += sample_frames
        return {"state": "timeout", "battle": self.rb.battle(), "visual": self.visual(), "frames": elapsed}

    def ensure_move_menu(self):
        v = self.visual()
        if v.battle_menu_like:
            return
        if not v.battle_command_menu:
            decision = self.wait_for_decision()
            if decision["state"] == "move_menu":
                return
            if decision["state"] != "command_menu":
                raise RuntimeError("not at decision: " + decision["state"])
        self.rb.open_fight_menu()
        for _ in range(60):
            self.gba.wait_frames(2)
            if self.visual().battle_menu_like:
                return
        raise RuntimeError("Fight menu did not open")

    @staticmethod
    def changed_move(before, after, names):
        for i, (a, b) in enumerate(zip(before, after)):
            if b < a:
                return i, names[i]
        return None, None

    def start_trainer_battle(self, *, face: str | None = None, interact: bool = True, max_frames: int = 1800):
        """Advance trainer interaction -> dialogue -> battle decision."""
        if face:
            self.gba.press(face.upper(), frames=3)
            self.gba.wait_frames(3)
        if interact:
            self.gba.press("A", frames=3)
        elapsed = 0
        while elapsed <= max_frames:
            v = self.visual()
            if v.battle_hud or v.battle_textbox:
                return self.wait_for_decision(max_frames=max_frames - elapsed)
            if v.bottom_textbox:
                ready = self.rb.wait_dialogue_ready(max_frames=120)
                if ready.get("ready"):
                    self.gba.press("A", frames=3)
            self.gba.wait_frames(8)
            elapsed += 8
        return {"state": "timeout", "frames": elapsed, "visual": self.visual()}

    def finish_to_overworld(self, *, max_frames: int = 1800):
        """Advance post-battle messages until sustained free overworld control."""
        elapsed = 0
        while elapsed <= max_frames:
            v = self.visual()
            if v.battle_textbox:
                self.gba.press("A", frames=2)
            elif v.bottom_textbox:
                ready = self.rb.wait_dialogue_ready(max_frames=120)
                if ready.get("ready"):
                    self.gba.press("A", frames=3)
            elif not v.battle_hud:
                free = self.rb.wait_free_overworld(max_frames=180, stable_samples=5)
                if free.get("free"):
                    return {"state": "overworld", "frames": elapsed, "player": free.get("player")}
            self.gba.wait_frames(8)
            elapsed += 8
        return {"state": "timeout", "frames": elapsed, "visual": self.visual()}

    def switch_to_party_slot(self, slot: int, *, max_frames: int = 1200):
        """Switch active battler and acknowledge success from live battle RAM."""
        party = self.rb.party()
        if slot < 0 or slot >= len(party):
            raise IndexError(slot)
        before = self.rb.battle().player.species_id
        target = party[slot].species_id
        if target == before:
            return {"state": "already_active", "slot": slot, "species_id": target}
        decision = self.wait_for_decision(max_frames=300, advance_text=True)
        if decision["state"] == "move_menu":
            self.gba.press("B", frames=3)
            self.gba.wait_frames(12)
            decision = self.wait_for_decision(max_frames=300, advance_text=True)
        if decision["state"] != "command_menu":
            raise RuntimeError("not at command menu for switch: " + decision["state"])
        self.rb.set_action_cursor(2)
        self.gba.press("A", frames=3)
        self.gba.wait_frames(70)
        if slot > 0:
            self.gba.press("RIGHT", frames=3)
            self.gba.wait_frames(4)
            for _ in range(slot - 1):
                self.gba.press("DOWN", frames=3)
                self.gba.wait_frames(4)
        self.gba.press("A", frames=3)
        self.gba.wait_frames(20)
        self.gba.press("A", frames=3)  # Shift is default first action.
        elapsed = presses = 0
        while elapsed <= max_frames:
            if self.rb.battle().player.species_id == target:
                nxt = self.wait_for_decision(max_frames=max(60, max_frames - elapsed), advance_text=True)
                return {"state": nxt["state"], "slot": slot, "species_id": target,
                        "frames": elapsed + nxt.get("frames", 0), "text_presses": presses}
            v = self.visual()
            if v.battle_textbox:
                self.gba.press("A", frames=2)
                presses += 1
            self.gba.wait_frames(6)
            elapsed += 6
        raise TimeoutError(f"switch to party slot {slot} did not activate species {target}")

    def submit_move(self, slot: int):
        """Submit one move and return a structured turn result.

        A move is not considered executed unless its PP drops. This distinguishes
        accepted selection from cases where a faster opponent KOs first. Opponent
        move choice is decoded from enemy PP deltas.
        """
        self.ensure_move_menu()
        before = self.rb.battle()
        move = before.player.moves[slot]
        pp0 = before.player.pp[slot]
        enemy_pp0 = tuple(before.opponent.pp)
        hp0, ohp0, species0 = before.player.hp, before.opponent.hp, before.opponent.species_id
        exp0 = tuple(mon.experience for mon in self.rb.party())
        opponent_changed = False
        enemy_move_slot = enemy_move_name = None
        self.rb.set_move_cursor(slot)
        self.gba.press("A", frames=3)

        elapsed = gone = 0
        accepted = False
        while elapsed <= 180:
            self.gba.wait_frames(2)
            elapsed += 2
            cur = self.rb.battle()
            v = self.visual()
            p = self.rb.party()[0]
            gone = gone + 1 if not (v.battle_command_menu or v.battle_menu_like) else 0
            enemy_acted = False
            if cur.opponent.species_id == species0:
                es, en = self.changed_move(enemy_pp0, cur.opponent.pp, before.opponent.moves)
                if es is not None:
                    enemy_acted = True
                    enemy_move_slot, enemy_move_name = es, en
            if (cur.player.pp[slot] < pp0 or enemy_acted or cur.player.hp != hp0 or
                    cur.opponent.hp != ohp0 or cur.opponent.species_id != species0 or gone >= 2 or p.hp == 0):
                accepted = True
                break
        if not accepted:
            raise RuntimeError("selection not accepted: " + str(move))

        presses = no_battle = 0
        state = "timeout"
        while elapsed <= 1600:
            cur = self.rb.battle()
            p = self.rb.party()[0]
            if p.hp == 0:
                state = "player_fainted"
                break
            if cur.opponent.species_id != species0 and cur.opponent.species_id != 0:
                opponent_changed = True
            elif cur.opponent.species_id == species0 and enemy_move_slot is None:
                es, en = self.changed_move(enemy_pp0, cur.opponent.pp, before.opponent.moves)
                if es is not None:
                    enemy_move_slot, enemy_move_name = es, en
            v = self.visual()
            if v.battle_command_menu:
                state = "command_menu"
                break
            if v.battle_menu_like:
                state = "move_menu"
                break
            if v.battle_textbox:
                self.gba.wait_frames(2)
                elapsed += 2
                v2 = self.visual()
                if v2.battle_textbox and not v2.battle_command_menu and not v2.battle_menu_like:
                    self.gba.press("A", frames=2)
                    presses += 1
            no_battle = 0 if (v.battle_hud or v.battle_textbox) else no_battle + 1
            if no_battle >= 30:
                state = "battle_exit"
                break
            self.gba.wait_frames(6)
            elapsed += 6

        after = self.rb.battle()
        exp1 = tuple(mon.experience for mon in self.rb.party())
        if enemy_move_slot is None and after.opponent.species_id == species0:
            enemy_move_slot, enemy_move_name = self.changed_move(enemy_pp0, after.opponent.pp, before.opponent.moves)
        exp_gain = any(b > a for a, b in zip(exp0, exp1))
        opponent_fainted = ((ohp0 > 0 and after.opponent.species_id == species0 and after.opponent.hp == 0)
                            or (opponent_changed and exp_gain))
        return TurnResult(
            state=state, move_slot=slot, move_name=move, selection_accepted=True,
            move_executed=after.player.pp[slot] < pp0, pp_before=pp0, pp_after=after.player.pp[slot],
            player_hp_before=hp0, player_hp_after=after.player.hp,
            opponent_species_before=species0, opponent_species_after=after.opponent.species_id,
            opponent_hp_before=ohp0, opponent_hp_after=after.opponent.hp,
            opponent_move_slot=enemy_move_slot, opponent_move_name=enemy_move_name,
            frames=elapsed, text_presses=presses, opponent_changed=opponent_changed,
            opponent_fainted=opponent_fainted, party_exp_before=exp0, party_exp_after=exp1,
        )
