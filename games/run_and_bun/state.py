from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import struct
from typing import Any

from .visual import image_difference, inspect_png

# Verified against Pokemon Run & Bun v1.07 in the live mGBA session.
G_SAVE_BLOCK1_PTR = 0x03005D9C
G_SAVE_BLOCK2_PTR = 0x03005DA0
G_POKEMON_STORAGE_PTR = 0x03005DA4
G_BATTLE_MONS = 0x020233FC
BATTLE_MON_STRIDE = 0x5C
G_BATTLE_ACTION_CURSOR = 0x02023A1C
G_BATTLE_MOVE_CURSOR = 0x02023A20
G_MENU_CURSOR = 0x0203C3C2
G_YES_NO_CURSOR = G_MENU_CURSOR
G_PLAYER_PARTY_COUNT = 0x02023A95
G_PLAYER_PARTY = 0x02023A98
PARTY_MON_STRIDE = 0x64

TYPE_NAMES = {
    0: "Normal", 1: "Fighting", 2: "Flying", 3: "Poison", 4: "Ground",
    5: "Rock", 6: "Bug", 7: "Ghost", 8: "Steel", 9: "Mystery",
    10: "Fire", 11: "Water", 12: "Grass", 13: "Electric", 14: "Psychic",
    15: "Ice", 16: "Dragon", 17: "Dark", 18: "Fairy",
}

# Only IDs verified in this exact ROM/session are named here.
VERIFIED_MOVE_NAMES = {
    0: None, 1: "Pound", 10: "Scratch", 11: "Vise Grip", 17: "Wing Attack",
    28: "Sand-Attack", 33: "Tackle", 35: "Wrap", 40: "Poison Sting", 44: "Bite", 45: "Growl",
    52: "Ember", 71: "Absorb", 98: "Quick Attack", 150: "Splash",
    205: "Rollout", 207: "Swagger", 209: "Spark", 332: "Aerial Ace", 450: "Bug Bite",
    590: "Confide",
}
VERIFIED_SPECIES_NAMES = {
    54: "Psyduck", 204: "Pineco", 255: "Torchic", 261: "Poochyena", 327: "Spinda", 387: "Turtwig",
    396: "Starly", 543: "Venipede", 406: "Budew", 427: "Buneary", 506: "Lillipup",
    659: "Bunnelby", 661: "Fletchling", 672: "Skiddo", 684: "Swirlix", 736: "Grubbin", 761: "Bounsweet",
    821: "Rookidee", 829: "Gossifleur", 850: "Sizzlipede", 987: "Zigzagoon",
}
KNOWN_MAPS = {
    (25, 40): "InsideOfTruck", (0, 9): "LittlerootTown", (0, 10): "OldaleTown",
    (0, 0): "PetalburgCity", (0, 16): "Route101", (0, 17): "Route102", (0, 18): "Route103", (0, 19): "Route104",
    (1, 0): "LittlerootTown_PlayerHouse_1F", (1, 1): "LittlerootTown_PlayerHouse_2F",
    (1, 2): "LittlerootTown_RivalHouse_1F", (1, 3): "LittlerootTown_RivalHouse_2F",
    (1, 4): "LittlerootTown_ProfessorBirchsLab",
}
SUBSTRUCT_ORDERS = (
    "GAEM", "GAME", "GEAM", "GEMA", "GMAE", "GMEA", "AGEM", "AGME",
    "AEGM", "AEMG", "AMGE", "AMEG", "EGAM", "EGMA", "EAGM", "EAMG",
    "EMGA", "EMAG", "MGAE", "MGEA", "MAGE", "MAEG", "MEGA", "MEAG",
)


def decode_gen3(data: bytes) -> str:
    out: list[str] = []
    for b in data:
        if b == 0xFF:
            break
        if 0xBB <= b <= 0xD4:
            out.append(chr(ord("A") + b - 0xBB))
        elif 0xD5 <= b <= 0xEE:
            out.append(chr(ord("a") + b - 0xD5))
        elif 0xA1 <= b <= 0xAA:
            out.append(chr(ord("0") + b - 0xA1))
        elif b == 0:
            out.append(" ")
        else:
            out.append(f"<{b:02X}>")
    return "".join(out).rstrip()


def decode_status(status: int) -> str | None:
    if status & 0x7: return "sleep"
    if status & 0x8: return "poison"
    if status & 0x10: return "burn"
    if status & 0x20: return "freeze"
    if status & 0x40: return "paralysis"
    if status & 0x80: return "toxic"
    return None


@dataclass(frozen=True)
class Pointers:
    save_block1: int
    save_block2: int
    pokemon_storage: int


@dataclass(frozen=True)
class PlayerState:
    name: str
    gender: str
    x: int
    y: int
    map_group: int
    map_num: int
    warp_id: int
    map_name: str | None

    @property
    def position(self): return (self.x, self.y)
    @property
    def map_id(self): return (self.map_group, self.map_num)


@dataclass(frozen=True)
class PartyMon:
    slot: int; species_id: int; species: str; nickname: str; level: int
    hp: int; max_hp: int; attack: int; defense: int; speed: int
    sp_attack: int; sp_defense: int
    move_ids: tuple[int, int, int, int]
    moves: tuple[str | None, str | None, str | None, str | None]
    pp: tuple[int, int, int, int]
    item_id: int; experience: int; friendship: int; status: int
    personality: int; ot_id: int; checksum: int; checksum_valid: bool

    @property
    def status_name(self): return decode_status(self.status)


@dataclass(frozen=True)
class BattleMon:
    battler: int; species_id: int; nickname: str; level: int
    hp: int; max_hp: int; attack: int; defense: int; speed: int
    sp_attack: int; sp_defense: int; ability_id: int
    type_ids: tuple[int, int, int]; types: tuple[str, str, str]
    move_ids: tuple[int, int, int, int]
    moves: tuple[str | None, str | None, str | None, str | None]
    pp: tuple[int, int, int, int]; item_id: int; status: int
    stat_stages: tuple[int, int, int, int, int, int, int, int]

    @property
    def hp_fraction(self): return self.hp / self.max_hp if self.max_hp else 0.0
    @property
    def status_name(self): return decode_status(self.status)
    @property
    def stage_deltas(self):
        names = ("hp", "attack", "defense", "speed", "sp_attack", "sp_defense", "accuracy", "evasion")
        return {name: self.stat_stages[i] - 6 for i, name in enumerate(names)}


@dataclass(frozen=True)
class BattleState:
    player: BattleMon
    opponent: BattleMon
    action_cursor: int
    move_cursor: int


class RunBun:
    """ROM-specific observation/action adapter over the generic mGBA RPC client."""

    def __init__(self, gba): self.gba = gba

    def pointers(self) -> Pointers:
        return Pointers(self.gba.read32(G_SAVE_BLOCK1_PTR), self.gba.read32(G_SAVE_BLOCK2_PTR), self.gba.read32(G_POKEMON_STORAGE_PTR))

    def player(self) -> PlayerState:
        p = self.pointers()
        sb1 = self.gba.read_range(p.save_block1, 12)
        sb2 = self.gba.read_range(p.save_block2, 16)
        x, y = struct.unpack_from("<hh", sb1, 0)
        group, num, warp = sb1[4], sb1[5], sb1[6]
        gender = "male" if sb2[8] == 0 else "female" if sb2[8] == 1 else f"unknown:{sb2[8]}"
        return PlayerState(decode_gen3(sb2[:8]), gender, x, y, group, num, warp, KNOWN_MAPS.get((group, num)))

    def party_count(self) -> int: return self.gba.read8(G_PLAYER_PARTY_COUNT)

    def party_mon(self, slot: int) -> PartyMon:
        count = self.party_count()
        if not 0 <= slot < count: raise IndexError(f"party slot {slot} outside count {count}")
        raw = self.gba.read_range(G_PLAYER_PARTY + slot * PARTY_MON_STRIDE, PARTY_MON_STRIDE)
        personality, ot_id = struct.unpack_from("<II", raw, 0)
        nickname = decode_gen3(raw[8:18]); checksum = struct.unpack_from("<H", raw, 0x1C)[0]
        key = personality ^ ot_id; secure = bytearray()
        for off in range(0x20, 0x50, 4):
            secure.extend(struct.pack("<I", struct.unpack_from("<I", raw, off)[0] ^ key))
        calc = sum(struct.unpack_from("<24H", secure, 0)) & 0xFFFF
        physical = [bytes(secure[i:i+12]) for i in range(0, 48, 12)]
        logical = {label: physical[i] for i, label in enumerate(SUBSTRUCT_ORDERS[personality % 24])}
        growth, attacks = logical["G"], logical["A"]
        species_id, item_id, exp = struct.unpack_from("<HHI", growth, 0)
        move_ids = struct.unpack_from("<4H", attacks, 0); pp = tuple(attacks[8:12])
        status = struct.unpack_from("<I", raw, 0x50)[0]; level = raw[0x54]
        hp, max_hp, atk, defense, speed, spa, spd = struct.unpack_from("<7H", raw, 0x56)
        return PartyMon(slot, species_id, VERIFIED_SPECIES_NAMES.get(species_id, f"Species#{species_id}"), nickname,
            level, hp, max_hp, atk, defense, speed, spa, spd, move_ids,
            tuple(VERIFIED_MOVE_NAMES.get(m, f"Move#{m}") if m else None for m in move_ids), pp,
            item_id, exp, growth[9], status, personality, ot_id, checksum, checksum == calc)

    def party(self): return tuple(self.party_mon(i) for i in range(self.party_count()))

    def battle_mon(self, battler: int) -> BattleMon:
        if not 0 <= battler <= 3: raise ValueError("battler must be 0..3")
        raw = self.gba.read_range(G_BATTLE_MONS + battler * BATTLE_MON_STRIDE, BATTLE_MON_STRIDE)
        species, atk, defense, speed, spa, spd = struct.unpack_from("<6H", raw, 0)
        moves = struct.unpack_from("<4H", raw, 0x0C); stages = tuple(raw[0x18:0x20])
        ability = struct.unpack_from("<H", raw, 0x20)[0]; type_ids = tuple(raw[0x22:0x25]); pp = tuple(raw[0x25:0x29])
        hp = struct.unpack_from("<H", raw, 0x2A)[0]; level = raw[0x2C]; max_hp = struct.unpack_from("<H", raw, 0x2E)[0]
        item = struct.unpack_from("<H", raw, 0x30)[0]; nickname = decode_gen3(raw[0x32:0x3D]); status = struct.unpack_from("<I", raw, 0x50)[0]
        return BattleMon(battler, species, nickname, level, hp, max_hp, atk, defense, speed, spa, spd, ability,
            type_ids, tuple(TYPE_NAMES.get(t, f"Type#{t}") for t in type_ids), moves,
            tuple(VERIFIED_MOVE_NAMES.get(m, f"Move#{m}") if m else None for m in moves), pp, item, status, stages)

    def battle(self):
        return BattleState(self.battle_mon(0), self.battle_mon(1), self.gba.read8(G_BATTLE_ACTION_CURSOR), self.gba.read8(G_BATTLE_MOVE_CURSOR))

    def open_start_menu(self, *, press_frames: int = 3) -> int:
        v = self.visual_state()
        if not v.start_menu:
            self.gba.press("START", frames=press_frames); self.gba.wait_frames(4)
        cur = self.gba.read8(G_MENU_CURSOR)
        if not 0 <= cur <= 7: raise RuntimeError(f"unexpected Start-menu cursor {cur}")
        return cur

    def set_start_menu_cursor(self, slot: int, *, press_frames: int = 3) -> int:
        if not 0 <= slot <= 7: raise ValueError("Start-menu slot must be 0..7")
        cur = self.open_start_menu(press_frames=press_frames)
        while cur != slot:
            down = (slot - cur) % 8; up = (cur - slot) % 8
            self.gba.press("DOWN" if down <= up else "UP", frames=press_frames); self.gba.wait_frames(2)
            cur = self.gba.read8(G_MENU_CURSOR)
        return cur

    def choose_start_menu(self, slot: int, *, press_frames: int = 3):
        self.set_start_menu_cursor(slot, press_frames=press_frames)
        return self.gba.press("A", frames=press_frames)

    def choose_yes_no(self, yes: bool, *, press_frames: int = 3):
        target = 0 if yes else 1; cur = self.gba.read8(G_YES_NO_CURSOR)
        if cur not in (0, 1): raise RuntimeError(f"unexpected Yes/No cursor {cur}")
        if cur != target:
            self.gba.press("UP" if target == 0 else "DOWN", frames=press_frames); self.gba.wait_frames(2)
        final = self.gba.read8(G_YES_NO_CURSOR)
        if final != target: raise RuntimeError(f"failed Yes/No cursor: {final}")
        return {"choice": "yes" if yes else "no", "cursor": final, "action": self.gba.press("A", frames=press_frames)}

    def _set_grid_cursor(self, address: int, slot: int, *, press_frames: int = 3) -> int:
        if slot not in range(4): raise ValueError("slot must be 0..3")
        cur = self.gba.read8(address)
        if cur not in range(4): raise RuntimeError(f"unexpected grid cursor {cur}")
        row, col = divmod(cur, 2); tr, tc = divmod(slot, 2)
        if row != tr:
            self.gba.press("DOWN" if tr > row else "UP", frames=press_frames); self.gba.wait_frames(2)
        cur = self.gba.read8(address); row, col = divmod(cur, 2)
        if col != tc:
            self.gba.press("RIGHT" if tc > col else "LEFT", frames=press_frames); self.gba.wait_frames(2)
        final = self.gba.read8(address)
        if final != slot: raise RuntimeError(f"failed cursor {address:#x}: {final}")
        return final

    def set_action_cursor(self, slot: int, *, press_frames: int = 3): return self._set_grid_cursor(G_BATTLE_ACTION_CURSOR, slot, press_frames=press_frames)
    def set_move_cursor(self, slot: int, *, press_frames: int = 3): return self._set_grid_cursor(G_BATTLE_MOVE_CURSOR, slot, press_frames=press_frames)
    def open_fight_menu(self, *, press_frames: int = 3):
        self.set_action_cursor(0, press_frames=press_frames); return self.gba.press("A", frames=press_frames)

    def choose_move(self, slot: int, *, press_frames: int = 3):
        battle = self.battle()
        if battle.player.move_ids[slot] == 0: raise ValueError(f"move slot {slot} is empty")
        self.set_move_cursor(slot, press_frames=press_frames); before_pp = self.battle().player.pp[slot]
        return {"slot": slot, "move": battle.player.moves[slot], "before_pp": before_pp, "action": self.gba.press("A", frames=press_frames)}

    def observe(self, *, screenshot: str | bool = False) -> dict[str, Any]:
        info = self.gba.info(); out: dict[str, Any] = {
            "frame": info["frame"], "title": info["title"], "code": info["code"],
            "player": asdict(self.player()), "pointers": asdict(self.pointers()),
            "party": [asdict(mon) for mon in self.party()],
        }
        if screenshot:
            path = screenshot if isinstance(screenshot, str) else f"/mnt/data/runbun-{info['frame']}.png"
            self.gba.screenshot(path); v = inspect_png(path); out["screenshot"] = path; out["visual"] = asdict(v)
            if v.battle_hud:
                b = self.battle(); out["battle"] = {"player": asdict(b.player), "opponent": asdict(b.opponent), "action_cursor": b.action_cursor, "move_cursor": b.move_cursor}
        return out

    def press(self, key: str, frames: int = 3): return self.gba.press(key, frames=frames)

    def step_tile(self, direction: str, *, pulse_frames: int = 4, max_pulses: int = 5, settle_frames: int = 2, strict: bool = True):
        """Move at most one overworld tile, independent of the avatar's current facing."""
        direction = direction.upper()
        if direction not in {"UP", "DOWN", "LEFT", "RIGHT"}: raise ValueError(direction)
        before = self.player()
        for pulses in range(1, max_pulses + 1):
            self.gba.press(direction, frames=pulse_frames)
            if settle_frames: self.gba.wait_frames(settle_frames)
            after = self.player()
            if after.position != before.position or after.map_id != before.map_id:
                return {"before": asdict(before), "after": asdict(after), "moved": True, "pulses": pulses}
        after = self.player()
        if strict: raise RuntimeError(f"{direction} did not move from {before.map_id}@{before.position}")
        return {"before": asdict(before), "after": asdict(after), "moved": False, "pulses": max_pulses}

    def visual_state(self, path: str = "/mnt/data/runbun-observe.png"):
        self.gba.screenshot(path); return inspect_png(path)

    def wait_free_overworld(self, *, sample_frames: int = 15, stable_samples: int = 8, max_frames: int = 900, prefix: str = "/mnt/data/.runbun-free"):
        elapsed = clean = 0; path = f"{prefix}.png"
        while elapsed <= max_frames:
            self.gba.screenshot(path); v = inspect_png(path)
            if not v.bottom_textbox and not v.battle_hud and not v.battle_textbox and not v.full_screen_menu:
                clean += 1
                if clean >= stable_samples: return {"free": True, "frames": elapsed, "visual": v, "player": asdict(self.player())}
            else: clean = 0
            self.gba.wait_frames(sample_frames); elapsed += sample_frames
        return {"free": False, "frames": elapsed, "visual": inspect_png(path), "player": asdict(self.player())}

    def wait_dialogue_ready(self, *, sample_frames: int = 6, max_frames: int = 300, prefix: str = "/mnt/data/.runbun-dialogue"):
        elapsed = 0; a, b = f"{prefix}-a.png", f"{prefix}-b.png"; self.gba.screenshot(a); va = inspect_png(a)
        if not va.bottom_textbox or va.dialogue_ready:
            return {"ready": va.dialogue_ready, "textbox": va.bottom_textbox, "frames": 0, "reason": "prompt" if va.dialogue_ready else "no_textbox", "visual": va}
        stable = 0
        while elapsed < max_frames:
            self.gba.wait_frames(sample_frames); elapsed += sample_frames; self.gba.screenshot(b); vb = inspect_png(b)
            if not vb.bottom_textbox: return {"ready": False, "textbox": False, "frames": elapsed, "reason": "no_textbox", "visual": vb}
            if vb.dialogue_ready: return {"ready": True, "textbox": True, "frames": elapsed, "reason": "prompt", "visual": vb}
            diff = image_difference(a, b, textbox_only=True, ignore_right=0)
            stable = stable + 1 if diff <= 0.03 else 0
            if stable >= 3: return {"ready": True, "textbox": True, "frames": elapsed, "reason": "stable_no_prompt", "difference": diff, "visual": vb}
            a, b = b, a
        return {"ready": False, "textbox": inspect_png(a).bottom_textbox, "frames": elapsed, "reason": "timeout", "visual": inspect_png(a)}

    def wait_screen_stable(self, *, sample_frames: int = 12, stable_samples: int = 2, max_frames: int = 240, threshold: float = 0.20, prefix: str = "/mnt/data/.runbun-stable"):
        previous, current = f"{prefix}-a.png", f"{prefix}-b.png"; self.gba.screenshot(previous)
        elapsed = stable = 0; last_diff = None
        while elapsed < max_frames:
            self.gba.wait_frames(sample_frames); elapsed += sample_frames; self.gba.screenshot(current)
            last_diff = image_difference(previous, current); stable = stable + 1 if last_diff <= threshold else 0
            if stable >= stable_samples: return {"stable": True, "frames": elapsed, "difference": last_diff, "visual": inspect_png(current)}
            previous, current = current, previous
        return {"stable": False, "frames": elapsed, "difference": last_diff, "visual": inspect_png(previous)}

    def advance_dialogue(self, presses: int = 1, *, frames: int = 3):
        results = []
        for _ in range(presses):
            before = self.wait_dialogue_ready(); action = self.gba.press("A", frames=frames); self.gba.wait_frames(3); after = self.wait_dialogue_ready()
            results.append({"before": before, "action": action, "after": after})
        return results
