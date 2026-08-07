from __future__ import annotations

from dataclasses import dataclass, asdict
import struct
from typing import Any
from pathlib import Path
import tempfile

from .visual import inspect_png, image_difference

# Verified against Pokemon Run & Bun v1.07 in the live mGBA session.
G_SAVE_BLOCK1_PTR = 0x03005D9C
G_SAVE_BLOCK2_PTR = 0x03005DA0
G_POKEMON_STORAGE_PTR = 0x03005DA4

# Verified battle globals for Run & Bun v1.07. gBattleMons keeps the familiar
# Gen III layout shape, extended to 16-bit abilities + a third type byte while
# retaining an 0x58-byte battler stride.
G_BATTLE_MONS = 0x020233FC
BATTLE_MON_STRIDE = 0x5C
G_BATTLE_ACTION_CURSOR = 0x02023A1C
G_BATTLE_MOVE_CURSOR = 0x02023A20
G_YES_NO_CURSOR = 0x0203C3C2
G_PLAYER_PARTY_COUNT = 0x02023A95
G_PLAYER_PARTY = 0x02023A98
PARTY_MON_STRIDE = 0x64

TYPE_NAMES = {
    0: "Normal", 1: "Fighting", 2: "Flying", 3: "Poison", 4: "Ground",
    5: "Rock", 6: "Bug", 7: "Ghost", 8: "Steel", 9: "Mystery",
    10: "Fire", 11: "Water", 12: "Grass", 13: "Electric", 14: "Psychic",
    15: "Ice", 16: "Dragon", 17: "Dark",
}

# Move IDs preserve the standard Gen III numbering for the moves encountered so
# far. Keep this deliberately incremental: IDs are verified against the live ROM
# before being added rather than assuming an arbitrary expansion revision.
VERIFIED_MOVE_NAMES = {
    0: None,
    1: "Pound",
    28: "Sand-Attack",
    33: "Tackle",
    44: "Bite",
    45: "Growl",
    71: "Absorb",
    150: "Splash",
}

VERIFIED_SPECIES_NAMES = {
    387: "Turtwig",
    987: "Zigzagoon",  # Dark/Normal form encountered in the Route 101 rescue.
}

# Physical secure-substruct order indexed by personality % 24.
SUBSTRUCT_ORDERS = (
    "GAEM", "GAME", "GEAM", "GEMA", "GMAE", "GMEA",
    "AGEM", "AGME", "AEGM", "AEMG", "AMGE", "AMEG",
    "EGAM", "EGMA", "EAGM", "EAMG", "EMGA", "EMAG",
    "MGAE", "MGEA", "MAGE", "MAEG", "MEGA", "MEAG",
)

KNOWN_MAPS = {
    (25, 40): "InsideOfTruck",
    (0, 9): "LittlerootTown",
    (0, 16): "Route101",
    (1, 0): "LittlerootTown_PlayerHouse_1F",
    (1, 1): "LittlerootTown_PlayerHouse_2F",
    (1, 2): "LittlerootTown_RivalHouse_1F",
    (1, 3): "LittlerootTown_RivalHouse_2F",
    (1, 4): "LittlerootTown_ProfessorBirchsLab",
}

# Gen III English text encoding, enough for player names and common ASCII-like text.
# Uppercase A starts at 0xBB and lowercase a starts at 0xD5.
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
        elif b == 0x00:
            out.append(" ")
        else:
            out.append(f"<{b:02X}>")
    return "".join(out).rstrip()


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
    def position(self) -> tuple[int, int]:
        return (self.x, self.y)

    @property
    def map_id(self) -> tuple[int, int]:
        return (self.map_group, self.map_num)


@dataclass(frozen=True)
class PartyMon:
    slot: int
    species_id: int
    species: str
    nickname: str
    level: int
    hp: int
    max_hp: int
    attack: int
    defense: int
    speed: int
    sp_attack: int
    sp_defense: int
    move_ids: tuple[int, int, int, int]
    moves: tuple[str | None, str | None, str | None, str | None]
    pp: tuple[int, int, int, int]
    item_id: int
    experience: int
    friendship: int
    status: int
    personality: int
    ot_id: int
    checksum: int
    checksum_valid: bool


@dataclass(frozen=True)
class BattleMon:
    battler: int
    species_id: int
    nickname: str
    level: int
    hp: int
    max_hp: int
    attack: int
    defense: int
    speed: int
    sp_attack: int
    sp_defense: int
    ability_id: int
    type_ids: tuple[int, int, int]
    types: tuple[str, str, str]
    move_ids: tuple[int, int, int, int]
    moves: tuple[str | None, str | None, str | None, str | None]
    pp: tuple[int, int, int, int]
    item_id: int
    status: int

    @property
    def hp_fraction(self) -> float:
        return self.hp / self.max_hp if self.max_hp else 0.0


@dataclass(frozen=True)
class BattleState:
    player: BattleMon
    opponent: BattleMon
    action_cursor: int
    move_cursor: int


class RunBun:
    """ROM-specific observation/action adapter layered over the generic mGBA RPC client."""

    def __init__(self, gba):
        self.gba = gba

    def pointers(self) -> Pointers:
        return Pointers(
            save_block1=self.gba.read32(G_SAVE_BLOCK1_PTR),
            save_block2=self.gba.read32(G_SAVE_BLOCK2_PTR),
            pokemon_storage=self.gba.read32(G_POKEMON_STORAGE_PTR),
        )

    def player(self) -> PlayerState:
        p = self.pointers()
        sb1 = self.gba.read_range(p.save_block1, 12)
        sb2 = self.gba.read_range(p.save_block2, 16)
        x, y = struct.unpack_from("<hh", sb1, 0)
        map_group, map_num, warp_id = sb1[4], sb1[5], sb1[6]
        return PlayerState(
            name=decode_gen3(sb2[:8]),
            gender="male" if sb2[8] == 0 else "female" if sb2[8] == 1 else f"unknown:{sb2[8]}",
            x=x,
            y=y,
            map_group=map_group,
            map_num=map_num,
            warp_id=warp_id,
            map_name=KNOWN_MAPS.get((map_group, map_num)),
        )

    def party_count(self) -> int:
        return self.gba.read8(G_PLAYER_PARTY_COUNT)

    def party_mon(self, slot: int) -> PartyMon:
        count = self.party_count()
        if slot < 0 or slot >= count:
            raise IndexError(f"party slot {slot} outside count {count}")
        raw = self.gba.read_range(G_PLAYER_PARTY + slot * PARTY_MON_STRIDE, PARTY_MON_STRIDE)
        personality, ot_id = struct.unpack_from("<II", raw, 0)
        nickname = decode_gen3(raw[8:18])
        checksum = struct.unpack_from("<H", raw, 0x1C)[0]
        key = personality ^ ot_id
        secure = bytearray()
        for off in range(0x20, 0x50, 4):
            word = struct.unpack_from("<I", raw, off)[0] ^ key
            secure.extend(struct.pack("<I", word))
        calc_checksum = sum(struct.unpack_from("<24H", secure, 0)) & 0xFFFF
        physical = [bytes(secure[i:i+12]) for i in range(0, 48, 12)]
        logical = {label: physical[i] for i, label in enumerate(SUBSTRUCT_ORDERS[personality % 24])}
        growth = logical["G"]
        attacks = logical["A"]
        species_id, item_id, experience = struct.unpack_from("<HHI", growth, 0)
        friendship = growth[9]
        move_ids = struct.unpack_from("<4H", attacks, 0)
        pp = tuple(attacks[8:12])
        status = struct.unpack_from("<I", raw, 0x50)[0]
        level = raw[0x54]
        hp, max_hp, attack, defense, speed, sp_attack, sp_defense = struct.unpack_from("<7H", raw, 0x56)
        return PartyMon(
            slot=slot,
            species_id=species_id,
            species=VERIFIED_SPECIES_NAMES.get(species_id, f"Species#{species_id}"),
            nickname=nickname,
            level=level,
            hp=hp,
            max_hp=max_hp,
            attack=attack,
            defense=defense,
            speed=speed,
            sp_attack=sp_attack,
            sp_defense=sp_defense,
            move_ids=move_ids,
            moves=tuple(VERIFIED_MOVE_NAMES.get(m, f"Move#{m}") if m else None for m in move_ids),
            pp=pp,
            item_id=item_id,
            experience=experience,
            friendship=friendship,
            status=status,
            personality=personality,
            ot_id=ot_id,
            checksum=checksum,
            checksum_valid=checksum == calc_checksum,
        )

    def party(self) -> tuple[PartyMon, ...]:
        return tuple(self.party_mon(i) for i in range(self.party_count()))

    def battle_mon(self, battler: int) -> BattleMon:
        if battler < 0 or battler > 3:
            raise ValueError("battler must be 0..3")
        raw = self.gba.read_range(G_BATTLE_MONS + battler * BATTLE_MON_STRIDE, BATTLE_MON_STRIDE)
        species, attack, defense, speed, sp_attack, sp_defense = struct.unpack_from("<6H", raw, 0)
        move_ids = struct.unpack_from("<4H", raw, 0x0C)
        ability_id = struct.unpack_from("<H", raw, 0x20)[0]
        type_ids = tuple(raw[0x22:0x25])
        pp = tuple(raw[0x25:0x29])
        hp = struct.unpack_from("<H", raw, 0x2A)[0]
        level = raw[0x2C]
        max_hp = struct.unpack_from("<H", raw, 0x2E)[0]
        item_id = struct.unpack_from("<H", raw, 0x30)[0]
        nickname = decode_gen3(raw[0x32:0x3D])
        status = struct.unpack_from("<I", raw, 0x50)[0]
        return BattleMon(
            battler=battler,
            species_id=species,
            nickname=nickname,
            level=level,
            hp=hp,
            max_hp=max_hp,
            attack=attack,
            defense=defense,
            speed=speed,
            sp_attack=sp_attack,
            sp_defense=sp_defense,
            ability_id=ability_id,
            type_ids=type_ids,
            types=tuple(TYPE_NAMES.get(t, f"Type#{t}") for t in type_ids),
            move_ids=move_ids,
            moves=tuple(VERIFIED_MOVE_NAMES.get(m, f"Move#{m}") if m else None for m in move_ids),
            pp=pp,
            item_id=item_id,
            status=status,
        )

    def battle(self) -> BattleState:
        return BattleState(
            player=self.battle_mon(0),
            opponent=self.battle_mon(1),
            action_cursor=self.gba.read8(G_BATTLE_ACTION_CURSOR),
            move_cursor=self.gba.read8(G_BATTLE_MOVE_CURSOR),
        )

    def choose_yes_no(self, yes: bool, *, press_frames: int = 3):
        target = 0 if yes else 1
        cur = self.gba.read8(G_YES_NO_CURSOR)
        if cur not in (0, 1):
            raise RuntimeError(f"unexpected Yes/No cursor {cur}")
        if cur != target:
            self.gba.press("UP" if target == 0 else "DOWN", frames=press_frames)
            self.gba.wait_frames(2)
        final = self.gba.read8(G_YES_NO_CURSOR)
        if final != target:
            raise RuntimeError(f"failed to set Yes/No cursor to {target}; got {final}")
        action = self.gba.press("A", frames=press_frames)
        return {"choice": "yes" if yes else "no", "cursor": final, "action": action}

    def set_action_cursor(self, slot: int, *, press_frames: int = 3) -> int:
        # 0 Fight, 1 Bag, 2 Pokemon, 3 Run.
        if slot not in (0, 1, 2, 3):
            raise ValueError("battle action slot must be 0..3")
        cur = self.gba.read8(G_BATTLE_ACTION_CURSOR)
        if cur not in (0, 1, 2, 3):
            raise RuntimeError(f"unexpected battle action cursor {cur}")
        row, col = divmod(cur, 2)
        target_row, target_col = divmod(slot, 2)
        if row != target_row:
            self.gba.press("DOWN" if target_row > row else "UP", frames=press_frames)
            self.gba.wait_frames(2)
        cur = self.gba.read8(G_BATTLE_ACTION_CURSOR)
        row, col = divmod(cur, 2)
        if col != target_col:
            self.gba.press("RIGHT" if target_col > col else "LEFT", frames=press_frames)
            self.gba.wait_frames(2)
        final = self.gba.read8(G_BATTLE_ACTION_CURSOR)
        if final != slot:
            raise RuntimeError(f"failed to move battle action cursor to {slot}; got {final}")
        return final

    def open_fight_menu(self, *, press_frames: int = 3):
        self.set_action_cursor(0, press_frames=press_frames)
        return self.gba.press("A", frames=press_frames)

    def set_move_cursor(self, slot: int, *, press_frames: int = 3) -> int:
        if slot not in (0, 1, 2, 3):
            raise ValueError("move slot must be 0..3")
        cur = self.gba.read8(G_BATTLE_MOVE_CURSOR)
        if cur not in (0, 1, 2, 3):
            raise RuntimeError(f"unexpected battle move cursor {cur}")
        row, col = divmod(cur, 2)
        target_row, target_col = divmod(slot, 2)
        if row != target_row:
            self.gba.press("DOWN" if target_row > row else "UP", frames=press_frames)
            self.gba.wait_frames(2)
        cur = self.gba.read8(G_BATTLE_MOVE_CURSOR)
        row, col = divmod(cur, 2)
        if col != target_col:
            self.gba.press("RIGHT" if target_col > col else "LEFT", frames=press_frames)
            self.gba.wait_frames(2)
        final = self.gba.read8(G_BATTLE_MOVE_CURSOR)
        if final != slot:
            raise RuntimeError(f"failed to move battle cursor to {slot}; got {final}")
        return final

    def choose_move(self, slot: int, *, press_frames: int = 3):
        battle = self.battle()
        if battle.player.move_ids[slot] == 0:
            raise ValueError(f"move slot {slot} is empty")
        self.set_move_cursor(slot, press_frames=press_frames)
        before_pp = self.battle().player.pp[slot]
        action = self.gba.press("A", frames=press_frames)
        return {"slot": slot, "move": self.battle().player.moves[slot], "before_pp": before_pp, "action": action}

    def advance_battle_until_menu(self, *, sample_frames: int = 24, max_frames: int = 900, prefix: str = "/mnt/data/.runbun-battle"):
        """Advance only battle message boxes until the move menu returns.

        A is sent only after a framebuffer observation identifies the teal battle
        message window, so the helper cannot accidentally choose a move when the
        white move selector has already returned.
        """
        elapsed = 0
        presses = 0
        path = f"{prefix}.png"
        while elapsed <= max_frames:
            self.gba.screenshot(path)
            visual = inspect_png(path)
            if visual.battle_menu_like:
                return {"state": "move_menu", "frames": elapsed, "presses": presses, "visual": visual}
            if visual.battle_command_menu:
                return {"state": "command_menu", "frames": elapsed, "presses": presses, "visual": visual}
            if not visual.battle_hud:
                try:
                    if self.battle().opponent.hp == 0:
                        return {"state": "battle_end", "frames": elapsed, "presses": presses, "visual": visual}
                except Exception:
                    pass
            if visual.battle_textbox:
                self.gba.press("A", frames=3)
                presses += 1
            self.gba.wait_frames(sample_frames)
            elapsed += sample_frames
        return {"state": "timeout", "frames": elapsed, "presses": presses, "visual": inspect_png(path)}

    def observe(self, *, screenshot: str | bool = False) -> dict[str, Any]:
        info = self.gba.info()
        result: dict[str, Any] = {
            "frame": info["frame"],
            "title": info["title"],
            "code": info["code"],
            "player": asdict(self.player()),
            "pointers": asdict(self.pointers()),
            "party": [asdict(mon) for mon in self.party()],
        }
        if screenshot:
            path = screenshot if isinstance(screenshot, str) else f"/mnt/data/runbun-{info['frame']}.png"
            result["screenshot"] = str(self.gba.screenshot(path))
            visual = inspect_png(path)
            result["visual"] = asdict(visual)
            if visual.battle_hud:
                battle = self.battle()
                result["battle"] = {
                    "player": asdict(battle.player),
                    "opponent": asdict(battle.opponent),
                    "action_cursor": battle.action_cursor,
                    "move_cursor": battle.move_cursor,
                }
        return result

    def press(self, key: str, frames: int = 3):
        return self.gba.press(key, frames=frames)

    def walk(self, direction: str, tiles: int = 1, *, hold_frames: int = 12, settle_frames: int = 8, strict: bool = True):
        direction = direction.upper()
        if direction not in {"UP", "DOWN", "LEFT", "RIGHT"}:
            raise ValueError(direction)
        history = []
        for _ in range(tiles):
            before = self.player()
            action = self.gba.press(direction, frames=hold_frames)
            if settle_frames:
                self.gba.wait_frames(settle_frames)
            after = self.player()
            moved = before.position != after.position or before.map_id != after.map_id
            item = {"before": asdict(before), "after": asdict(after), "moved": moved, "action": action}
            history.append(item)
            if strict and not moved:
                raise RuntimeError(f"{direction} did not change player position/map from {before}")
        return history

    def visual_state(self, path: str = "/mnt/data/runbun-observe.png"):
        self.gba.screenshot(path)
        return inspect_png(path)

    def wait_dialogue_ready(self, *, sample_frames: int = 6, max_frames: int = 300, prefix: str = "/mnt/data/.runbun-dialogue"):
        """Wait until a standard bottom dialogue box is ready for one A press.

        Preferred signal is Emerald's red completion prompt. Some scripted messages
        intentionally omit that prompt; for those, a stable textbox crop is accepted
        after several samples. Overworld sprite animation is ignored.
        """
        elapsed = 0
        a = f"{prefix}-a.png"
        b = f"{prefix}-b.png"
        self.gba.screenshot(a)
        va = inspect_png(a)
        if not va.bottom_textbox or va.dialogue_ready:
            return {"ready": va.dialogue_ready, "textbox": va.bottom_textbox, "frames": 0, "reason": "prompt" if va.dialogue_ready else "no_textbox", "visual": va}
        stable = 0
        while elapsed < max_frames:
            self.gba.wait_frames(sample_frames)
            elapsed += sample_frames
            self.gba.screenshot(b)
            vb = inspect_png(b)
            if not vb.bottom_textbox:
                return {"ready": False, "textbox": False, "frames": elapsed, "reason": "no_textbox", "visual": vb}
            if vb.dialogue_ready:
                return {"ready": True, "textbox": True, "frames": elapsed, "reason": "prompt", "visual": vb}
            diff = image_difference(a, b, textbox_only=True, ignore_right=0)
            if diff <= 0.03:
                stable += 1
                if stable >= 3:
                    return {"ready": True, "textbox": True, "frames": elapsed, "reason": "stable_no_prompt", "difference": diff, "visual": vb}
            else:
                stable = 0
            a, b = b, a
        v = inspect_png(a)
        return {"ready": False, "textbox": v.bottom_textbox, "frames": elapsed, "reason": "timeout", "visual": v}

    def wait_screen_stable(self, *, sample_frames: int = 12, stable_samples: int = 2, max_frames: int = 240, threshold: float = 0.20, prefix: str = "/mnt/data/.runbun-stable"):
        previous = f"{prefix}-a.png"
        current = f"{prefix}-b.png"
        self.gba.screenshot(previous)
        elapsed = 0
        stable = 0
        last_diff = None
        while elapsed < max_frames:
            self.gba.wait_frames(sample_frames)
            elapsed += sample_frames
            self.gba.screenshot(current)
            last_diff = image_difference(previous, current)
            if last_diff <= threshold:
                stable += 1
                if stable >= stable_samples:
                    return {"stable": True, "frames": elapsed, "difference": last_diff, "visual": inspect_png(current)}
            else:
                stable = 0
            previous, current = current, previous
        return {"stable": False, "frames": elapsed, "difference": last_diff, "visual": inspect_png(previous)}

    def advance_dialogue(self, presses: int = 1, *, frames: int = 3):
        """Advance standard Emerald dialogue one completed page per A press."""
        results = []
        for _ in range(presses):
            before = self.wait_dialogue_ready()
            action = self.gba.press("A", frames=frames)
            # Let the engine react before testing the next page/box.
            self.gba.wait_frames(3)
            after = self.wait_dialogue_ready()
            results.append({"before": before, "action": action, "after": after})
        return results
