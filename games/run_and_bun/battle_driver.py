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
    """Acknowledged battle control for Run & Bun v1.07.

    Important rule: battle RAM, not party slot 0, identifies the active battler.
    That distinction matters after switches and forced replacements.
    """

    def __init__(self, runbun, scratch="/mnt/data/.battle-v2.png"):
        self.rb = runbun
        self.gba = runbun.gba
        self.scratch = scratch

    def visual(self):
        self.gba.screenshot(self.scratch)
        return inspect_png(self.scratch)

    @staticmethod
    def changed_move(before, after, names):
        for i, (a, b) in enumerate(zip(before, after)):
            if b < a:
                return i, names[i]
        return None, None

    def wait_for_decision(self, max_frames=1200, sample_frames=6, advance_text=True):
        elapsed = no_battle = 0
        while elapsed <= max_frames:
            v = self.visual()
            b = self.rb.battle()
            if b.player.hp == 0:
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
            d = self.wait_for_decision()
            if d["state"] == "move_menu":
                return
            if d["state"] != "command_menu":
                raise RuntimeError("not at decision: " + d["state"])
        self.rb.open_fight_menu()
        for _ in range(60):
            self.gba.wait_frames(2)
            if self.visual().battle_menu_like:
                return
        raise RuntimeError("Fight menu did not open")

    def start_trainer_battle(self, *, face: str | None = None, interact=True, max_frames=1800):
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
                r = self.rb.wait_dialogue_ready(max_frames=120)
                if r.get("ready"):
                    self.gba.press("A", frames=3)
            self.gba.wait_frames(8)
            elapsed += 8
        return {"state": "timeout", "frames": elapsed, "visual": self.visual()}

    def finish_to_overworld(self, *, max_frames=1800):
        elapsed = 0
        while elapsed <= max_frames:
            v = self.visual()
            if v.battle_textbox:
                self.gba.press("A", frames=2)
            elif v.bottom_textbox:
                r = self.rb.wait_dialogue_ready(max_frames=120)
                if r.get("ready"):
                    self.gba.press("A", frames=3)
            elif not v.battle_hud:
                free = self.rb.wait_free_overworld(max_frames=180, stable_samples=5)
                if free.get("free"):
                    return {"state": "overworld", "frames": elapsed, "player": free.get("player")}
            self.gba.wait_frames(8)
            elapsed += 8
        return {"state": "timeout", "frames": elapsed, "visual": self.visual()}

    def _party_cursor_to(self, slot: int):
        """Navigate the Gen III party layout from the default slot-0 cursor."""
        if slot == 0:
            return
        self.gba.press("RIGHT", frames=3)
        self.gba.wait_frames(4)
        for _ in range(slot - 1):
            self.gba.press("DOWN", frames=3)
            self.gba.wait_frames(4)

    def switch_to_party_slot(self, slot: int, *, max_frames=1200):
        party = self.rb.party()
        if not 0 <= slot < len(party):
            raise IndexError(slot)
        before = self.rb.battle().player.species_id
        target = party[slot].species_id
        if target == before:
            return {"state": "already_active", "slot": slot, "species_id": target}
        d = self.wait_for_decision(max_frames=300, advance_text=True)
        if d["state"] == "move_menu":
            self.gba.press("B", frames=3)
            self.gba.wait_frames(12)
            d = self.wait_for_decision(max_frames=300, advance_text=True)
        if d["state"] != "command_menu":
            raise RuntimeError("not at command menu for switch: " + d["state"])
        self.rb.set_action_cursor(2)
        self.gba.press("A", frames=3)
        self.gba.wait_frames(70)
        self._party_cursor_to(slot)
        self.gba.press("A", frames=3)
        self.gba.wait_frames(20)
        self.gba.press("A", frames=3)  # Shift
        return self._wait_for_active(target, slot, max_frames=max_frames)

    def replace_fainted(self, slot: int, *, max_frames=1200):
        """Handle the forced Party -> Send Out flow after the active mon faints."""
        party = self.rb.party()
        if not 0 <= slot < len(party):
            raise IndexError(slot)
        target = party[slot].species_id
        elapsed = 0
        # Advance faint text until the Party screen appears.
        while elapsed <= max_frames:
            v = self.visual()
            if v.party_menu:
                break
            if v.battle_textbox:
                self.gba.press("A", frames=2)
            self.gba.wait_frames(8)
            elapsed += 8
        else:
            raise TimeoutError("forced replacement Party screen did not appear")
        self._party_cursor_to(slot)
        self.gba.press("A", frames=3)
        self.gba.wait_frames(20)
        self.gba.press("A", frames=3)  # Send Out is the default action.
        result = self._wait_for_active(target, slot, max_frames=max_frames - elapsed)
        result["forced"] = True
        return result

    def _wait_for_active(self, target: int, slot: int, *, max_frames: int):
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
        raise TimeoutError(f"party slot {slot} did not become active")

    def submit_move(self, slot: int):
        self.ensure_move_menu()
        before = self.rb.battle()
        move = before.player.moves[slot]
        pp0 = before.player.pp[slot]
        epp0 = tuple(before.opponent.pp)
        hp0, ohp0, os0 = before.player.hp, before.opponent.hp, before.opponent.species_id
        exp0 = tuple(m.experience for m in self.rb.party())
        enemy_slot = enemy_name = None
        opponent_changed = False
        self.rb.set_move_cursor(slot)
        self.gba.press("A", frames=3)

        elapsed = gone = 0
        while elapsed <= 180:
            self.gba.wait_frames(2)
            elapsed += 2
            cur = self.rb.battle()
            v = self.visual()
            gone = gone + 1 if not (v.battle_command_menu or v.battle_menu_like) else 0
            if cur.opponent.species_id == os0:
                es, en = self.changed_move(epp0, cur.opponent.pp, before.opponent.moves)
                if es is not None:
                    enemy_slot, enemy_name = es, en
            accepted = (cur.player.pp[slot] < pp0 or enemy_slot is not None or cur.player.hp != hp0 or
                        cur.opponent.hp != ohp0 or cur.opponent.species_id != os0 or gone >= 2 or cur.player.hp == 0)
            if accepted:
                break
        else:
            raise RuntimeError("selection not accepted: " + str(move))

        presses = no_battle = 0
        state = "timeout"
        while elapsed <= 1600:
            cur = self.rb.battle()
            if cur.player.hp == 0:
                state = "player_fainted"
                break
            if cur.opponent.species_id != os0 and cur.opponent.species_id != 0:
                opponent_changed = True
            elif cur.opponent.species_id == os0 and enemy_slot is None:
                enemy_slot, enemy_name = self.changed_move(epp0, cur.opponent.pp, before.opponent.moves)
            v = self.visual()
            if v.battle_command_menu:
                state = "command_menu"; break
            if v.battle_menu_like:
                state = "move_menu"; break
            if v.battle_textbox:
                self.gba.wait_frames(2); elapsed += 2
                v2 = self.visual()
                if v2.battle_textbox and not v2.battle_command_menu and not v2.battle_menu_like:
                    self.gba.press("A", frames=2); presses += 1
            no_battle = 0 if (v.battle_hud or v.battle_textbox) else no_battle + 1
            if no_battle >= 30:
                state = "battle_exit"; break
            self.gba.wait_frames(6); elapsed += 6

        after = self.rb.battle()
        exp1 = tuple(m.experience for m in self.rb.party())
        if enemy_slot is None and after.opponent.species_id == os0:
            enemy_slot, enemy_name = self.changed_move(epp0, after.opponent.pp, before.opponent.moves)
        exp_gain = any(b > a for a, b in zip(exp0, exp1))
        opponent_fainted = ((ohp0 > 0 and after.opponent.species_id == os0 and after.opponent.hp == 0)
                            or (opponent_changed and exp_gain))
        return TurnResult(
            state=state, move_slot=slot, move_name=move, selection_accepted=True,
            move_executed=after.player.pp[slot] < pp0, pp_before=pp0, pp_after=after.player.pp[slot],
            player_hp_before=hp0, player_hp_after=after.player.hp,
            opponent_species_before=os0, opponent_species_after=after.opponent.species_id,
            opponent_hp_before=ohp0, opponent_hp_after=after.opponent.hp,
            opponent_move_slot=enemy_slot, opponent_move_name=enemy_name,
            frames=elapsed, text_presses=presses, opponent_changed=opponent_changed,
            opponent_fainted=opponent_fainted, party_exp_before=exp0, party_exp_after=exp1,
        )
