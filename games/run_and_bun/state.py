from __future__ import annotations

from dataclasses import dataclass, asdict
import struct
from typing import Any
from pathlib import Path
import tempfile

try:
    from .visual import inspect_png, image_difference
except ImportError:  # RAM/task/text play does not require Pillow or visual.py.
    inspect_png = None
    image_difference = None

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
    15: "Ice", 16: "Dragon", 17: "Dark", 18: "Fairy",
}

# Move IDs preserve the standard Gen III numbering for the moves encountered so
# far. Keep this deliberately incremental: IDs are verified against the live ROM
# before being added rather than assuming an arbitrary expansion revision.
VERIFIED_MOVE_NAMES = {
    0: None,
    10: "Scratch",
    1: "Pound",
    28: "Sand-Attack",
    16: "Gust",
    33: "Tackle",
    43: "Leer",
    44: "Bite",
    45: "Growl",
    52: "Ember",
    71: "Absorb",
    150: "Splash",
    183: "Mach Punch",
    31: "Double Team",
    61: "Bubble Beam",
    64: "Peck",
    98: "Quick Attack",
    117: "Bide",
    267: "Nature Power",
}

VERIFIED_SPECIES_NAMES = {
    54: "Psyduck",
    16: "Pidgey",
    273: "Seedot",
    270: "Lotad",
    390: "Chimchar",
    396: "Starly",
    672: "Skiddo",
    761: "Bounsweet",
    821: "Rookidee",
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
    (0, 0): "PetalburgCity",
    (0, 10): "OldaleTown",
    (0, 17): "Route102",
    (0, 18): "Route103",
    (0, 19): "Route104",
    (8, 4): "PetalburgCity_PokemonCenter_1F",
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
    stat_stages: tuple[int, int, int, int, int, int, int, int]
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

    def _menu_pause_boundary(self) -> None:
        """Let a Lua-driven menu task settle without stealing focus."""
        reconnect = getattr(self.gba, "reconnect_boundary", None)
        if callable(reconnect):
            reconnect()
            return
        pause = getattr(self.gba, "pause", None)
        resume = getattr(self.gba, "resume", None)
        if callable(pause) and callable(resume):
            pause()
            resume()

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
            stat_stages=tuple(raw[0x18:0x20]),
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
            self.gba.wait_frames(120)
        cur = self.gba.read8(G_BATTLE_ACTION_CURSOR)
        row, col = divmod(cur, 2)
        if col != target_col:
            self.gba.press("RIGHT" if target_col > col else "LEFT", frames=press_frames)
            self.gba.wait_frames(120)
        final = self.gba.read8(G_BATTLE_ACTION_CURSOR)
        if final != slot:
            raise RuntimeError(f"failed to move battle action cursor to {slot}; got {final}")
        return final

    def open_fight_menu(self, *, press_frames: int = 3, settle_frames: int = 30):
        # Synchronize printer/menu state first.  Battle text can remain visible
        # after the RAM command cursor has already reset to Fight.
        ready = self.advance_battle_until_menu(max_frames=600)
        if ready.get("state") == "move_menu":
            return {"already_open": True, "state": "move_menu"}
        self.set_action_cursor(0, press_frames=press_frames)
        action = self.gba.press("A", frames=press_frames)
        if settle_frames:
            self.gba.wait_frames(settle_frames)
        self._menu_pause_boundary()
        return action

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
            self.gba.wait_frames(120)
        cur = self.gba.read8(G_BATTLE_MOVE_CURSOR)
        row, col = divmod(cur, 2)
        if col != target_col:
            self.gba.press("RIGHT" if target_col > col else "LEFT", frames=press_frames)
            self.gba.wait_frames(120)
        final = self.gba.read8(G_BATTLE_MOVE_CURSOR)
        if final != slot:
            raise RuntimeError(f"failed to move battle cursor to {slot}; got {final}")
        return final

    def choose_move(self, slot: int, *, press_frames: int = 3):
        battle = self.battle()
        if battle.player.move_ids[slot] == 0:
            raise ValueError(f"move slot {slot} is empty")
        # The move selector is created by the preceding Fight confirmation;
        # its RAM cursor is not updated until the next few frames.
        self.set_move_cursor(slot, press_frames=press_frames)
        before_pp = self.battle().player.pp[slot]
        self._menu_pause_boundary()
        action = self.gba.press("A", frames=press_frames)
        return {"slot": slot, "move": self.battle().player.moves[slot], "before_pp": before_pp, "action": action}

    def choose_move_id(self, move_id: int, *, press_frames: int = 3):
        """Choose a move by its decoded ID, avoiding fragile slot guesses."""
        slots = [slot for slot, value in enumerate(self.battle().player.move_ids) if value == move_id]
        if not slots:
            raise ValueError(f"move ID {move_id} is not present on the active Pokémon")
        if len(slots) > 1:
            raise ValueError(f"move ID {move_id} appears in multiple move slots: {slots}")
        return self.choose_move(slots[0], press_frames=press_frames)

    def switch_pokemon(
        self,
        slot: int | None = None,
        *,
        species_id: int | None = None,
        press_frames: int = 3,
        settle_frames: int = 12,
    ):
        """Switch to a party slot from the battle Pokémon menu.

        The party list opens with the active slot selected.  Run & Bun then
        opens a second menu (Send Out/Shift, Summary, Cancel); confirm its
        default Send Out/Shift entry before verifying the battle RAM.
        """
        count = self.party_count()
        if species_id is not None:
            matches = [mon.slot for mon in self.party() if mon.species_id == species_id and mon.hp > 0]
            if not matches:
                raise ValueError(f"no healthy party Pokémon with species {species_id}")
            slot = matches[0]
        if slot is None:
            raise ValueError("slot or species_id required")
        if slot < 0 or slot >= count:
            raise IndexError(f"party slot {slot} outside count {count}")
        mon = self.party_mon(slot)
        if mon.hp <= 0:
            raise ValueError(f"cannot switch to fainted party slot {slot}")
        active_species = self.battle_mon(0).species_id
        if mon.species_id == active_species:
            raise ValueError("switch_failed: target is already active")
        self.set_action_cursor(2, press_frames=press_frames)
        self.gba.press("A", frames=press_frames)
        # The party screen slides in before it accepts directional input.
        self.gba.wait_frames(max(settle_frames, 120))
        self._menu_pause_boundary()
        # The battle party screen displays the active identity first, even
        # when the persistent party array has not been reordered.  Map the
        # requested identity to that UI order before moving its two-column
        # cursor; using the persistent slot directly selects the active mon
        # after a voluntary switch.
        party = self.party()
        active_mons = [m for m in party if m.species_id == active_species and m.personality != mon.personality]
        if not active_mons:
            raise RuntimeError("switch_failed: active party identity not found")
        active_identity = active_mons[0].personality
        ui_order = [m for m in party if m.personality != active_identity]
        # Run & Bun lays out active mon on left and every bench mon in one
        # vertical column on right.  Cursor starts on active mon.
        target_index = next((i for i, m in enumerate(ui_order) if m.personality == mon.personality), None)
        if target_index is None:
            raise RuntimeError("switch_failed: target missing from battle party order")
        self.gba.press("RIGHT", frames=press_frames)
        self.gba.wait_frames(max(settle_frames, 120))
        for _ in range(target_index):
            self.gba.press("DOWN", frames=press_frames)
            self.gba.wait_frames(max(settle_frames, 120))
        self._menu_pause_boundary()
        action = self.gba.press("A", frames=press_frames)
        self.gba.wait_frames(settle_frames)
        # Target selection opens the party action submenu in this ROM.  Its
        # first entry is the valid switch action; without this second A the
        # caller can mistake an open submenu for switch_failed.
        self.gba.wait_frames(max(settle_frames, 120))
        # This ROM can miss an A sent in the same uninterrupted Lua run that
        # opened the submenu. A focus-free pause/resume makes it deterministic.
        self._menu_pause_boundary()
        action = self.gba.press("A", frames=press_frames)
        self.gba.wait_frames(settle_frames)
        # The switch animation and battle text can keep battler 0 stale for
        # roughly 300 frames. The submenu also occasionally ignores its first
        # confirmation; retry once, then surface a real rejection.
        for attempt in range(2):
            for _ in range(60):
                if self.battle_mon(0).species_id != active_species:
                    # Battler RAM updates before the Shift submenu closes.
                    # Do not hand control back while cursor still says
                    # Pokemon; caller would press into Summary/Cancel.
                    self.gba.wait_frames(max(settle_frames, 120))
                    self._menu_pause_boundary()
                    if self.gba.read8(G_BATTLE_ACTION_CURSOR) == 2:
                        action = self.gba.press("A", frames=press_frames)
                        self.gba.wait_frames(settle_frames)
                    return {"slot": slot, "species": mon.species, "action": action}
                self.gba.wait_frames(10)
            if attempt == 0:
                self._menu_pause_boundary()
                action = self.gba.press("A", frames=press_frames)
                self.gba.wait_frames(settle_frames)
        raise RuntimeError("switch_failed: battle rejected voluntary switch")

    def use_battle_item(self, item_index: int, *, target_slot: int = 0, press_frames: int = 3, settle_frames: int = 12):
        """Select a medicine entry in the battle Bag and apply it.

        ``item_index`` is the visible medicine-pocket index, not a raw item
        ID.  Inventory decoding belongs to the ROM adapter; keeping this
        primitive index-based makes it safe for hacks that reorder item IDs.
        """
        if item_index < 0:
            raise ValueError("item_index must be non-negative")
        self.set_action_cursor(1, press_frames=press_frames)
        self.gba.press("A", frames=press_frames)
        self.gba.wait_frames(settle_frames)
        for _ in range(item_index):
            self.gba.press("DOWN", frames=press_frames)
            self.gba.wait_frames(2)
        self.gba.press("A", frames=press_frames)
        self.gba.wait_frames(settle_frames)
        # Medicine normally opens a target-Pokémon list.  The active Pokémon
        # is the first entry, so only move when a caller explicitly requests a
        # later target.
        for _ in range(target_slot):
            self.gba.press("DOWN", frames=press_frames)
            self.gba.wait_frames(2)
        action = self.gba.press("A", frames=press_frames)
        self.gba.wait_frames(settle_frames)
        return {"item_index": item_index, "target_slot": target_slot, "action": action}

    def advance_battle_until_menu(
        self,
        *,
        sample_frames: int = 24,
        max_frames: int = 900,
        prefix: str = "/tmp/.runbun-battle",
        visual_fallback: bool = False,
    ):
        """Advance only battle message boxes until the move menu returns.

        A is sent only after a framebuffer observation identifies the teal battle
        message window, so the helper cannot accidentally choose a move when the
        white move selector has already returned.
        """
        # The local adapter owns the verified RAM/text-printer controller. Keep
        # this broader adapter as a compatibility facade without reintroducing
        # screenshot polling into the normal path.
        from games.runbun import RunBunAdapter

        result = RunBunAdapter(self.gba).advance_battle_until_menu(
            sample_frames=sample_frames,
            max_frames=max_frames,
            visual_fallback=visual_fallback,
        )
        return result

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
