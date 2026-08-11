"""Verified Pokémon Run & Bun v1.07 state decoding.

The addresses in this module are ROM-specific and come from
docs/RUNBUN_V107.md. They must not be reused for another ROM revision.
"""

from __future__ import annotations

import math
import hashlib
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    from games.run_and_bun.visual import inspect_png
except ImportError:  # Pillow remains optional for RAM-only clients.
    inspect_png = None

from games.run_and_bun.rom_data import BattleRomData, RomMove


ROM_TITLE = "POKEMON EMER"
ROM_CODE = "BPEE"
# Bump when the tactical ranking or its evidence semantics change.  The
# reusable policy qualification hash includes this version, while excluding
# reporting-only changes from the clone streak.
BATTLE_SCORER_VERSION = "runbun-tactical-v9"

SAVE_BLOCK1_PTR = 0x03005D9C
SAVE_BLOCK2_PTR = 0x03005DA0
PC_STORAGE_PTR = 0x03005DA4

NEW_GAME_CURSOR = 0x02023006
YES_NO_CURSOR = 0x0203C3C2
# Run & Bun reuses this byte for the field start-menu cursor and the party
# target cursor.  The Bag task keeps its pocket and item cursors in task data;
# these are read back rather than inferred from a framebuffer.
FIELD_MENU_CURSOR = 0x0203C3C2
FIELD_PARTY_CURSOR = 0x0203C51D
BATTLE_COMMAND_CURSOR = 0x02023A1C
BATTLE_MOVE_CURSOR = 0x02023A20
# Verified by equal-frame savestate diffs in the Route 109 double battle.
# 0xFF means no target selector; live battler slots are 0..3.
BATTLE_TARGET = 0x03005D84

PLAYER_PARTY_COUNT = 0x02023A95
PLAYER_PARTY = 0x02023A98
PARTY_STRIDE = 0x64
PARTY_SECURITY_OFFSET = 0x20
PARTY_SECURITY_LENGTH = 0x30
BOX_STRIDE = 0x50
BOX_COUNT = 14
BOX_CAPACITY = 30

BATTLE_MONS = 0x020233FC
BATTLE_MON_STRIDE = 0x5C

# Verified in the live Run & Bun v1.07 process. These are the Gen III
# gStringVar4/text-printer locations used by the dialogue state decoder.
TEXT_BUFFER = 0x02021FC4
TEXT_BUFFER_LENGTH = 0x3E8
TEXT_PRINTERS = 0x0202018C
TEXT_PRINTER_STRIDE = 0x24
TEXT_PRINTER_SLOTS = 16
FIELD_MESSAGE_BOX_MODE = 0x0202183D
FIELD_MESSAGE_MODE_NAMES = {
    0: "none",
    2: "ready",
    3: "auto_scroll",
    11: "party_menu",
    13: "party_prompt",
    15: "field_item_target",
    10: "nickname_screen",
    16: "battle_intro",
    19: "party_target_transition",
    34: "battle_text",
    36: "double_battle_party_transition",
    38: "battle_intro",
    42: "battle_text",
    44: "double_battle_text",
    45: "battle_move",
    46: "battle_text",
    48: "double_battle_command",
    50: "battle_text_prompt",
    52: "double_battle_command",
    54: "battle_text_prompt",
    55: "battle_post",
    59: "battle_text",
    60: "double_battle_move",
    68: "double_battle_text",
}


def nickname_target_prompt_verified(prompt: str) -> bool:
    """Accept the ROM printer's occasionally truncated final character."""
    normalized = " ".join(str(prompt).split()).casefold()
    return "nickname should i chan" in normalized
# Battler structs are not cleared when a prior double battle ends. These are
# the field modes observed only while the current battle engine owns the
# double-battle UI; stale gBattleMons slots 2/3 must not promote a single
# battle into a double battle.
DOUBLE_BATTLE_FIELD_MESSAGE_MODES = {36, 44, 48, 52, 60, 68}
BATTLE_FIELD_MESSAGE_MODES = {16, 34, 36, 38, 42, 44, 45, 46, 48, 50, 52, 54, 55, 59, 60, 68}
BATTLE_KO_FIELD_MESSAGE_MODES = BATTLE_FIELD_MESSAGE_MODES

# The move IDs below are the ones observed in this Run & Bun save.  The hack
# keeps the familiar Gen III move numbering for these entries, but battle
# feedback remains authoritative because abilities and custom encounter data
# can change the result of an otherwise reasonable type-chart choice.
MOVE_TYPE_IDS = {
    10: 0,   # Scratch, Normal
    43: 0,   # Leer, Normal (status)
    16: 2,   # Gust, Flying
    23: 0,   # Stomp, Normal
    20: 0,   # Bind, Normal residual-damage move
    28: 0,   # Sand-Attack, status
    33: 0,   # Tackle, Normal
    44: 17,  # Bite, Dark
    49: 0,   # Sonic Boom, fixed-damage Normal
    52: 10,  # Ember, Fire
    71: 12,  # Absorb, Grass
    75: 12,  # Razor Leaf, Grass
    88: 5,   # Rock Throw, Rock
    479: 5,  # Smack Down, Rock
    7: 10,   # Fire Punch, Fire
    24: 1,   # Double Kick, Fighting
    92: 3,   # Toxic, Poison
    98: 0,   # Quick Attack, Normal
    117: 0,  # Bide, delayed Normal
    182: 0,  # Protect, status
    183: 1,  # Mach Punch, Fighting
    205: 5,  # Rollout, Rock; unsafe for capture because it locks in
    209: 13,  # Spark, Electric
    252: 0,   # Fake Out, Normal; priority
    270: 0,   # Helping Hand, status
    317: 5,   # Rock Tomb, Rock
    225: 16,  # DragonBreath, Dragon
    249: 1,   # Rock Smash, Fighting
    229: 0,  # Rapid Spin, Normal (verified from the live move window)
    267: 0,  # Nature Power (terrain-dependent; conservative Normal)
    283: 0,  # Endeavor, fixed/conditional; never use for bounded weakening
    332: 2,  # Aerial Ace, Flying
    340: 2,  # Bounce, Flying
    342: 3,  # Poison Tail, Poison; residual-status risk for capture
    352: 11,  # Water Pulse, Water
    351: 13,  # Shock Wave, Electric
    395: 1,   # Force Palm, Fighting
    410: 1,   # Vacuum Wave, Fighting; special priority
    458: 0,   # Double Hit, Normal
    474: 3,   # Venoshock, Poison
    162: 0,   # Super Fang, fixed half-current-HP Normal damage
    172: 10,  # Flame Wheel, Fire
    365: 2,   # Pluck, Flying
    583: 18,  # Play Rough, Fairy
    72: 12,   # Mega Drain, Grass
    188: 3,   # Sludge Bomb, Poison
    73: 12,   # Leech Seed, Grass status
    320: 12,  # GrassWhistle, Grass status
    611: 6,   # Infestation, Bug
    355: 2,   # Roost, Flying status
    336: 0,   # Howl, status
    109: 7,  # Confuse Ray, Ghost
    86: 13,  # Thunder Wave, Electric
    341: 4,  # Mud Shot, Ground
    420: 15, # Ice Shard, Ice; priority
    450: 6,  # Bug Bite, Bug
    523: 4,  # Bulldoze, Ground; spread speed control
    453: 11, # Aqua Jet, Water
    512: 2,  # Acrobatics, Flying
    589: 0,  # Play Nice, Normal status move in this build
}
MOVE_POWER = {
    10: 35, 16: 40, 20: 15, 23: 65, 33: 40, 44: 60, 49: 20, 52: 40,
    71: 20, 75: 55, 88: 50, 98: 40, 183: 40, 205: 30, 229: 20,
    7: 75, 24: 30, 86: 0, 92: 0, 103: 0, 109: 0,
    209: 65, 252: 40, 317: 60, 332: 60, 342: 50, 351: 60, 352: 60,
    395: 60, 410: 40, 420: 40, 458: 35, 474: 65, 267: 80, 270: 0, 341: 55,
    479: 50,
    340: 85, 225: 60, 249: 40, 450: 60, 453: 40, 512: 60, 523: 60,
    172: 60, 365: 60, 583: 90, 72: 40, 188: 90, 611: 20,
}
MOVE_SPECIAL_IDS = frozenset({16, 49, 52, 71, 72, 188, 267, 341, 351, 352, 410, 450, 474, 611})
MOVE_PRIORITY_IDS = frozenset({98, 183, 252, 410, 420, 453})
STATUS_MOVE_IDS = frozenset({28, 43, 73, 86, 92, 103, 109, 117, 150, 182, 270, 283, 320, 336, 355, 589})
# Fixed-damage moves bypass the ordinary type/power formula. Values are the
# denominator of the defender's current-HP fraction; Run & Bun's Super Fang
# follows the verified cartridge behavior of floor(current HP / 2).
MOVE_FIXED_DAMAGE_FRACTIONS = {162: 2}
PHYSICAL_THREAT_DEBUFF_IDS = frozenset({589})  # Play Nice lowers Attack.
ABILITY_FLASH_FIRE = 18
ABILITY_LEVITATE = 26
ABILITY_GUTS = 62
MECHANICS_MODELED_ABILITIES = frozenset({0, ABILITY_FLASH_FIRE, ABILITY_LEVITATE, ABILITY_GUTS, 19})  # Shield Dust is enforced by policy.
RESIDUAL_OR_MULTI_TURN_MOVE_IDS = frozenset({20, 73, 205, 267, 340, 611})
BURN_STATUS = 0x10
PARALYSIS_STATUS = 0x40

# These moves can be nonlethal on the immediate roll but create an avoidable
# later KO (residual poison/bind or Rollout lock-in).  A capture planner must
# not call them a safe weakening line.
CAPTURE_UNSAFE_MOVE_IDS = frozenset({20, 205, 342})
CAPTURE_STATUS_MOVES = {
    "Dark Void": ("sleep", 2.0), "GrassWhistle": ("sleep", 2.0),
    "Hypnosis": ("sleep", 2.0), "Lovely Kiss": ("sleep", 2.0),
    "Sing": ("sleep", 2.0), "Sleep Powder": ("sleep", 2.0),
    "Spore": ("sleep", 2.0),
    "Glare": ("paralysis", 1.5), "Nuzzle": ("paralysis", 1.5),
    "Stun Spore": ("paralysis", 1.5), "Thunder Wave": ("paralysis", 1.5),
}

# Species types are only a fallback for party entries: battle entries already
# carry live type bytes.  Keep this small and verified against this save.
SPECIES_TYPE_IDS = {
    16: (0, 2), 98: (11,), 193: (6, 2), 273: (12,),
    390: (10,), 761: (12,), 987: (17, 0),
    95: (5, 4), 111: (5, 4), 231: (4,), 388: (12,), 397: (0, 2),
    453: (3, 1), 543: (6, 3), 777: (13, 8), 878: (8,), 404: (13,),
}

# Only the interactions needed by the currently observed party/moves are
# listed here.  Unknown types are neutral rather than guessed, and the
# post-move battle message can refine the score for a species/ability pair.
TYPE_EFFECTIVENESS = {
    0: {7: 0.0},                         # Normal -> Ghost
    1: {0: 2.0, 2: 0.5, 3: 0.5, 5: 2.0, 6: 0.5, 8: 2.0, 11: 1.0, 14: 0.5, 15: 2.0, 17: 2.0},
    2: {1: 2.0, 6: 2.0, 12: 2.0, 10: 0.5, 11: 1.0},
    3: {3: 0.5, 4: 0.5, 5: 0.5, 7: 0.5, 8: 0.0, 12: 2.0, 18: 2.0},
    # Ground is neutral into Water; the live Bibarel turn verified this
    # correction when Bulldoze dealt 12 rather than the old super-effective
    # estimate. Keep the explicit Water entry to prevent accidental fallback
    # to an overconfident multiplier.
    4: {10: 2.0, 11: 1.0, 12: 0.5, 2: 0.0, 6: 0.5, 13: 2.0},
    13: {4: 0.0, 12: 0.5, 13: 0.5},       # Electric -> Ground/Grass/Electric
    6: {12: 2.0, 10: 0.5, 1: 0.5, 2: 0.5, 17: 2.0, 18: 0.5},
    10: {12: 2.0, 6: 2.0, 10: 0.5, 11: 0.5, 5: 0.5, 15: 2.0, 8: 2.0},
    11: {10: 2.0, 4: 2.0, 12: 0.5, 11: 0.5},
    12: {11: 2.0, 4: 2.0, 5: 2.0, 10: 0.5, 12: 0.5, 2: 0.5, 3: 0.5, 6: 0.5, 8: 0.5},
}

CHAR_PROMPT_SCROLL = 0xFA
CHAR_PROMPT_CLEAR = 0xFB
CHAR_NEWLINE = 0xFE
CHAR_EOS = 0xFF


def _gen3_charset() -> dict[int, str]:
    values: dict[int, str] = {0x00: " "}
    values.update({0xBB + i: chr(ord("A") + i) for i in range(26)})
    values.update({0xD5 + i: chr(ord("a") + i) for i in range(26)})
    values.update({0xA1 + i: str(i) for i in range(10)})
    values.update(
        {
            0x01: "À",
            0x02: "Á",
            0x03: "Â",
            0x04: "Ç",
            0x06: "É",
            0x1B: "é",
            0x2D: "&",
            0x2E: "+",
            0x34: "Lv",
            0x35: "=",
            0x36: ";",
            0x5A: "Í",
            0x68: "â",
            0x6F: "í",
            0xAB: "!",
            0xAC: "?",
            0xAD: ".",
            0xAE: "-",
            0xB0: "…",
            0xB1: "“",
            0xB2: "”",
            0xB4: "'",
            0xB5: "♂",
            0xB6: "♀",
            0xB7: "¥",
            0xB8: ",",
            0xB9: "×",
            0xBA: "/",
            0xEF: "▶",
            0xF0: ":",
            0xF1: "Ä",
            0xF2: "Ö",
            0xF3: "Ü",
            0xF4: "ä",
            0xF5: "ö",
            0xF6: "ü",
        }
    )
    return values


GEN3_CHARSET = _gen3_charset()
_PLACEHOLDERS = {
    0x00: "<BATTLE_BUFFER>",
    0x01: "<PLAYER>",
    0x02: "<STR_VAR_1>",
    0x03: "<STR_VAR_2>",
    0x04: "<STR_VAR_3>",
    0x05: "<KUN>",
    0x06: "<RIVAL>",
    0x07: "<VERSION>",
    0x08: "<AQUA>",
    0x09: "<MAGMA>",
    0x0A: "<ARCHIE>",
    0x0B: "<MAXIE>",
    0x0C: "<KYOGRE>",
    0x0D: "<GROUDON>",
}


def decode_gen3_text(data: bytes, *, stop_at_eos: bool = True) -> str:
    """Decode the English Gen III text encoding used by this ROM."""
    out: list[str] = []
    i = 0
    control_lengths = {
        0x01: 1,
        0x02: 1,
        0x03: 1,
        0x04: 3,
        0x05: 1,
        0x06: 1,
        0x07: 0,
        0x08: 1,
        0x09: 0,
        0x0A: 0,
        0x0B: 2,
        0x0C: 1,
        0x0D: 1,
        0x0E: 1,
        0x0F: 1,
        0x10: 2,
        0x11: 1,
        0x12: 1,
        0x13: 1,
        0x14: 1,
        0x15: 0,
        0x16: 0,
        0x17: 0,
        0x18: 0,
    }
    while i < len(data):
        value = data[i]
        if value == CHAR_EOS and stop_at_eos:
            break
        if value == CHAR_NEWLINE:
            out.append("\n")
            i += 1
            continue
        if value == CHAR_PROMPT_SCROLL:
            out.append("<PROMPT_SCROLL>")
            i += 1
            continue
        if value == CHAR_PROMPT_CLEAR:
            out.append("<PROMPT_CLEAR>")
            i += 1
            continue
        if value == 0xFC and i + 1 < len(data):
            code = data[i + 1]
            out.append(f"<CTRL_{code:02X}>")
            i += 2 + control_lengths.get(code, 0)
            continue
        if value == 0xFD and i + 1 < len(data):
            out.append(_PLACEHOLDERS.get(data[i + 1], f"<PLACEHOLDER_{data[i + 1]:02X}>"))
            i += 2
            continue
        if value == 0xF7:
            out.append("<DYNAMIC>")
            i += 1
            continue
        out.append(GEN3_CHARSET.get(value, f"<0x{value:02X}>"))
        i += 1
    return "".join(out)


def _text_page(data: bytes, buffer_address: int, printer: dict[str, Any]) -> dict[str, Any] | None:
    current_char = int(printer.get("current_char", 0))
    cursor = current_char - buffer_address
    if cursor < 0 or cursor > len(data):
        return None
    marker_positions = [
        index
        for index, value in enumerate(data[:cursor])
        if value in (CHAR_PROMPT_SCROLL, CHAR_PROMPT_CLEAR)
    ]
    if cursor and data[cursor - 1] in (CHAR_PROMPT_SCROLL, CHAR_PROMPT_CLEAR):
        end = cursor - 1
        prior = [index for index in marker_positions if index < end]
        start = (prior[-1] + 1) if prior else 0
    else:
        start = (marker_positions[-1] + 1) if marker_positions else 0
        end = cursor
    raw = data[start:end]
    return {
        "start": buffer_address + start,
        "end": buffer_address + end,
        "cursor": current_char,
        "state": printer.get("state"),
        "raw": raw,
        "text": decode_gen3_text(raw),
    }


def decode_text_observation(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Turn the Lua text-printer inspection payload into semantic dialogue state."""
    if not raw:
        return None
    buffer = raw["buffer"]
    data = buffer["data"]
    pages = []
    for printer in raw.get("printers", []):
        page = _text_page(data, buffer["address"], printer)
        if page and page["raw"]:
            pages.append({"printer": printer, "page": page})
    selected = None
    if pages:
        selected = max(
            pages,
            key=lambda item: (
                item["printer"].get("state") == 2,
                item["printer"].get("current_char", 0),
            ),
        )
    return {
        "buffer_address": buffer["address"],
        "buffer_length": buffer["length"],
        "printers": raw.get("printers", []),
        "pages": pages,
        "active": bool(selected and selected["printer"].get("active")),
        "current": selected["page"] if selected else None,
    }


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "little")


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "little")


def _s16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "little", signed=True)


@dataclass(frozen=True)
class BattleMon:
    species: int
    attack: int
    defense: int
    speed: int
    special_attack: int
    special_defense: int
    stat_stages: tuple[int, int, int, int, int, int, int, int]
    moves: tuple[int, int, int, int]
    ability: int
    types: tuple[int, int, int]
    pp: tuple[int, int, int, int]
    current_hp: int
    level: int
    max_hp: int
    held_item: int
    experience: int
    personality: int
    status: int


def decode_battle_mon(data: bytes, offset: int = 0) -> BattleMon:
    """Decode one 0x5c-byte battler using the verified runtime offsets."""
    return BattleMon(
        species=_u16(data, offset + 0x00),
        attack=_u16(data, offset + 0x02),
        defense=_u16(data, offset + 0x04),
        speed=_u16(data, offset + 0x06),
        special_attack=_u16(data, offset + 0x08),
        special_defense=_u16(data, offset + 0x0A),
        stat_stages=tuple(data[offset + 0x18 : offset + 0x20]),
        moves=tuple(_u16(data, offset + 0x0C + 2 * i) for i in range(4)),
        ability=_u16(data, offset + 0x20),
        types=tuple(data[offset + 0x22 + i] for i in range(3)),
        pp=tuple(data[offset + 0x25 + i] for i in range(4)),
        current_hp=_u16(data, offset + 0x2A),
        level=data[offset + 0x2C],
        max_hp=_u16(data, offset + 0x2E),
        held_item=_u16(data, offset + 0x30),
        experience=_u32(data, offset + 0x48),
        personality=_u32(data, offset + 0x4C),
        status=_u32(data, offset + 0x50),
    )


def decode_battle_mons(data: bytes, slots: int = 4) -> list[dict[str, Any]]:
    expected = BATTLE_MON_STRIDE * slots
    if len(data) < expected:
        raise ValueError(f"battle buffer too short: {len(data)} < {expected}")
    result = []
    for index in range(slots):
        mon = decode_battle_mon(data, index * BATTLE_MON_STRIDE)
        result.append({"slot": index, "present": mon.species != 0, "state": asdict(mon)})
    return result


# Gen III stores four 12-byte substructures in a personality-dependent order.
# Each tuple maps logical substructure (Growth, Attacks, EVs, Misc) to its
# physical slot in the encrypted 48-byte region.
GEN3_SUBSTRUCT_ORDER = (
    (0, 1, 2, 3), (0, 1, 3, 2), (0, 2, 1, 3), (0, 3, 1, 2),
    (0, 2, 3, 1), (0, 3, 2, 1), (1, 0, 2, 3), (1, 0, 3, 2),
    (2, 0, 1, 3), (3, 0, 1, 2), (2, 0, 3, 1), (3, 0, 2, 1),
    (1, 2, 0, 3), (1, 3, 0, 2), (2, 1, 0, 3), (3, 1, 0, 2),
    (2, 3, 0, 1), (3, 2, 0, 1), (1, 2, 3, 0), (1, 3, 2, 0),
    (2, 1, 3, 0), (3, 1, 2, 0), (2, 3, 1, 0), (3, 2, 1, 0),
)


def _u32_words(data: bytes) -> list[int]:
    return [_u32(data, offset) for offset in range(0, len(data), 4)]


def _decode_party_secure(raw: bytes, personality: int, ot_id: int) -> bytes:
    key = personality ^ ot_id
    return b"".join((word ^ key).to_bytes(4, "little") for word in _u32_words(raw))


def decode_party_mon(data: bytes, offset: int = 0) -> dict[str, Any]:
    """Decode one encrypted 0x64-byte Gen III party Pokémon."""
    if len(data) < offset + PARTY_STRIDE:
        raise ValueError("party buffer too short")
    personality = _u32(data, offset)
    ot_id = _u32(data, offset + 0x04)
    nickname = decode_gen3_text(data[offset + 0x08 : offset + 0x12]).rstrip(" ")
    stored_checksum = _u16(data, offset + 0x1C)
    secure = _decode_party_secure(
        data[offset + PARTY_SECURITY_OFFSET : offset + PARTY_SECURITY_OFFSET + PARTY_SECURITY_LENGTH],
        personality,
        ot_id,
    )
    physical = [secure[index : index + 0x0C] for index in range(0, PARTY_SECURITY_LENGTH, 0x0C)]
    logical = [physical[index] for index in GEN3_SUBSTRUCT_ORDER[personality % 24]]
    checksum = sum(_u16(logical_type, index) for logical_type in logical for index in range(0, 0x0C, 2)) & 0xFFFF
    growth, attacks, evs, misc = logical
    ivs = _u32(misc, 0x04)
    return {
        "personality": personality,
        "ot_id": ot_id,
        "nickname": nickname,
        "species": _u16(growth, 0x00),
        "held_item": _u16(growth, 0x02),
        "experience": _u32(growth, 0x04),
        "pp_bonuses": growth[0x08],
        "friendship": growth[0x09],
        "moves": tuple(_u16(attacks, 0x02 * index) for index in range(4)),
        "pp": tuple(attacks[0x08 + index] for index in range(4)),
        "evs": tuple(evs[index] for index in range(6)),
        "ivs": tuple((ivs >> (5 * index)) & 0x1F for index in range(6)),
        "is_egg": bool((ivs >> 30) & 1),
        "ability_num": (ivs >> 31) & 1,
        "status": _u32(data, offset + 0x50),
        "level": data[offset + 0x54],
        "current_hp": _u16(data, offset + 0x56),
        "max_hp": _u16(data, offset + 0x58),
        "attack": _u16(data, offset + 0x5A),
        "defense": _u16(data, offset + 0x5C),
        "speed": _u16(data, offset + 0x5E),
        "special_attack": _u16(data, offset + 0x60),
        "special_defense": _u16(data, offset + 0x62),
        "checksum": {"stored": stored_checksum, "calculated": checksum, "valid": stored_checksum == checksum},
    }


def decode_party_mons(data: bytes, slots: int = 6) -> list[dict[str, Any]]:
    expected = PARTY_STRIDE * slots
    if len(data) < expected:
        raise ValueError(f"party buffer too short: {len(data)} < {expected}")
    result = []
    for index in range(slots):
        mon = decode_party_mon(data, index * PARTY_STRIDE)
        result.append({"slot": index, "present": mon["species"] != 0, "state": mon})
    return result


def decode_box_mon(data: bytes, offset: int = 0) -> dict[str, Any]:
    """Decode the persistent 80-byte prefix shared by box and party Pokémon."""
    if len(data) < offset + BOX_STRIDE:
        raise ValueError("box buffer too short")
    mon = decode_party_mon(data[offset:offset + BOX_STRIDE] + bytes(PARTY_STRIDE - BOX_STRIDE))
    for field in (
        "status", "level", "current_hp", "max_hp", "attack", "defense",
        "speed", "special_attack", "special_defense",
    ):
        mon.pop(field, None)
    return mon


def _named_values(reads: list[dict[str, Any]]) -> dict[str, int]:
    return {item["name"]: item["value"] for item in reads}


def _valid_ewram_pointer(value: int | None) -> bool:
    return value is not None and 0x02000000 <= value < 0x02040000


class RunBunAdapter:
    """Read a structured Run & Bun observation from an MGBA client."""

    def __init__(self, gba, *, enforce_live_trainer_gate: bool = True):
        self.gba = gba
        self.enforce_live_trainer_gate = enforce_live_trainer_gate
        self._rom_data: BattleRomData | None = None
        # Learned from live HP deltas. Keyed by attacker species, move ID,
        # defender species; values are observed damage samples.
        try:
            from games.run_and_bun.experience import load_damage_memory

            self._damage_memory = load_damage_memory()
        except Exception:
            # Gameplay must remain available if the optional local ledger is
            # damaged; the current adapter can still learn in memory.
            self._damage_memory: dict[tuple[int, int, int], list[int]] = {}

    def _remember_damage(self, key: tuple[int, int, int], damage: int, *, feedback: str = "") -> None:
        self._damage_memory.setdefault(key, []).append(damage)
        try:
            from games.run_and_bun.experience import append_damage_sample

            append_damage_sample(key, damage, feedback=feedback)
        except Exception:
            # Persistent learning is helpful but never allowed to interrupt
            # the authoritative emulator action loop.
            pass

    def rom_data(self) -> BattleRomData:
        """Return the cached, header-validated ROM metadata reader."""
        if self._rom_data is None:
            self._rom_data = BattleRomData(self.gba)
        return self._rom_data

    def battle_move_data(self, observation: dict[str, Any]) -> dict[int, RomMove]:
        """Return ROM records for every move present in the canonical state."""
        move_ids = {
            int(move_id)
            for section in (observation.get("battle", {}), observation.get("party", {}))
            for mon in section.get("mons", [])
            if mon.get("present")
            for move_id in mon.get("state", {}).get("moves", ())
            if move_id
        }
        rom = self.rom_data()
        return {move_id: rom.move(move_id) for move_id in sorted(move_ids)}

    def pokemon_storage(self, *, include_empty: bool = False) -> dict[str, Any]:
        """Return every valid box record from the pointed save-storage image."""
        pointer = self.gba.read32(PC_STORAGE_PTR)
        if not _valid_ewram_pointer(pointer):
            raise RuntimeError(f"invalid_pokemon_storage_pointer: {pointer:#x}")
        current_box = self.gba.read32(pointer)
        if current_box >= BOX_COUNT:
            raise RuntimeError(f"invalid_current_box: {current_box}")
        raw = self.gba.read_range(pointer + 4, BOX_COUNT * BOX_CAPACITY * BOX_STRIDE, name="pokemon_storage")
        records = []
        for index in range(BOX_COUNT * BOX_CAPACITY):
            mon = decode_box_mon(raw, index * BOX_STRIDE)
            if not include_empty and not mon["species"]:
                continue
            records.append({
                "box": index // BOX_CAPACITY,
                "slot": index % BOX_CAPACITY,
                "present": bool(mon["species"]),
                "reference": {
                    "personality": mon["personality"],
                    "ot_id": mon["ot_id"],
                    "box": index // BOX_CAPACITY,
                    "slot": index % BOX_CAPACITY,
                },
                "state": mon,
            })
        return {
            "pointer": pointer,
            "current_box": current_box,
            "occupied": sum(record["present"] for record in records),
            "records": records,
        }

    def _pc_tap(self, key: str, *, wait_frames: int = 120, hold_frames: int = 3) -> None:
        self.gba.press(key, frames=hold_frames)
        self.gba.wait_frames(wait_frames)

    def _pc_terminal_approach(self) -> tuple[tuple[int, int], str]:
        """Find the live PC terminal and a reachable tile facing it."""
        from games.run_and_bun.live_map import read_live_map

        state = self.observe()
        map_state = state.get("map") or {}
        map_id = (map_state.get("group"), map_state.get("number"))
        current = (map_state.get("x"), map_state.get("y"))
        if None in map_id or None in current:
            raise RuntimeError("pc_terminal_map_state_incomplete")
        nurse_candidates = [
            obj for obj in self.live_objects()
            if obj.get("active")
            and not obj.get("is_player")
            and tuple(obj.get("map_id", ())) == map_id
            and obj.get("local_id") == 1
            and obj.get("graphics_id") == 58
        ]
        if len(nurse_candidates) != 1:
            raise RuntimeError(f"pc_terminal_requires_pokecenter: {nurse_candidates}")
        live = read_live_map(self.gba)
        # The terminal is a static map tile, not an object event. In this ROM
        # the verified center layout places it three tiles east of the nurse;
        # require the resulting approach/terminal geometry instead of probing
        # arbitrary coordinates or talking to a nearby NPC.
        nurse = nurse_candidates[0]["position"]
        approach = (int(nurse[0]) + 3, int(nurse[1]))
        terminal = (approach[0], approach[1] - 1)
        if not (
            0 <= terminal[0] < live.active_width
            and 0 <= terminal[1] < live.active_height
            and live.walkable(*approach)
            and not live.walkable(*terminal)
        ):
            raise RuntimeError(f"pc_terminal_geometry_unverified: nurse={nurse} approach={approach}")
        live.path_to(
            (int(current[0]), int(current[1])),
            approach,
            allow_nonwalkable_start=True,
            grass_penalty=100,
        )
        return approach, "UP"

    def _close_pc_storage(self) -> dict[str, Any]:
        """Exit the storage script and prove field movement owns input again."""
        for _ in range(2):
            self._pc_tap("B", hold_frames=12)
        state = self.observe()
        map_state = state.get("map") or {}
        map_id = (map_state.get("group"), map_state.get("number"))
        approach, _ = self._pc_terminal_approach()
        result = self.follow_live_path_adaptive(
            approach, expected_map=map_id, avoid_trainer_sight_lines=False
        )
        if result.get("position") != approach or result["state"].get("mode") != "overworld":
            raise RuntimeError("pc_storage_cleanup_failed")
        return result["state"]

    @staticmethod
    def _pc_box_keys(current: int, target: int) -> list[str]:
        right = (target - current) % BOX_COUNT
        left = (current - target) % BOX_COUNT
        return (["RIGHT"] * right) if right <= left else (["LEFT"] * left)

    def _open_pc_storage_menu(self) -> None:
        state = self.observe()
        if state.get("mode") != "overworld" or state.get("battle", {}).get("active"):
            raise RuntimeError("pc_transfer_requires_clean_overworld")
        map_state = state.get("map") or {}
        map_id = (map_state.get("group"), map_state.get("number"))
        approach, direction = self._pc_terminal_approach()
        self.follow_live_path_adaptive(
            approach,
            expected_map=map_id,
            avoid_trainer_sight_lines=False,
        )
        self._pc_tap(direction, wait_frames=12)
        self._pc_tap("A")
        text = ((self.observe().get("text") or {}).get("current") or {}).get("text", "")
        if "booted up the PC" not in text:
            raise RuntimeError(f"pc_terminal_identity_not_verified: {text!r}")
        # Which PC -> Someone's PC -> accessed -> storage opened -> operation menu.
        for _ in range(4):
            self._pc_tap("A")

    def _pc_deposit_from_main(self, party_slot: int, box: int) -> None:
        party = self.observe().get("party", {})
        count = int(party.get("count", 0))
        if count <= 1:
            raise RuntimeError("cannot_deposit_last_party_pokemon")
        if party_slot not in range(count):
            raise IndexError(f"party slot {party_slot} outside count {count}")
        current_box = self.pokemon_storage()["current_box"]
        self._pc_tap("DOWN")  # Deposit Pokémon.
        self._pc_tap("A")
        if party_slot:
            self._pc_tap("RIGHT")
            for _ in range(party_slot - 1):
                self._pc_tap("DOWN")
        self._pc_tap("A")  # Pokémon action menu.
        self._pc_tap("A")  # Store.
        for key in self._pc_box_keys(current_box, box):
            self._pc_tap(key)
        self._pc_tap("A")  # Chosen destination box.
        self._pc_tap("B")  # Continue box operations? prompt.
        self._pc_tap("B")  # Return to operation menu and reconcile party count.

    def _pc_withdraw_from_main(self, box: int, box_slot: int, *, menu_cursor: int = 0) -> None:
        if self.observe().get("party", {}).get("count", 0) >= 6:
            raise RuntimeError("cannot_withdraw_with_full_party")
        storage = self.pokemon_storage()
        target = next(
            (record for record in storage["records"] if record["box"] == box and record["slot"] == box_slot),
            None,
        )
        if target is None:
            raise RuntimeError(f"box slot is empty: box={box} slot={box_slot}")
        if menu_cursor == 1:
            self._pc_tap("UP")
        self._pc_tap("A")  # Withdraw Pokémon.
        current_box = storage["current_box"]
        if box != current_box:
            self._pc_tap("UP")
            for key in self._pc_box_keys(current_box, box):
                self._pc_tap(key)
            self._pc_tap("DOWN")
        row, column = divmod(box_slot, 6)
        for _ in range(column):
            self._pc_tap("RIGHT")
        for _ in range(row):
            self._pc_tap("DOWN")
        self._pc_tap("A")  # Pokémon action menu.
        self._pc_tap("A")  # Withdraw.
        self._pc_tap("B")  # Continue box operations? prompt.
        self._pc_tap("B")  # Return to operation menu and reconcile party count.

    def pc_transfer(
        self,
        operation: str,
        *,
        party_slot: int | None = None,
        box: int = 0,
        box_slot: int | None = None,
    ) -> dict[str, Any]:
        """Deposit, withdraw, or swap through the verified storage UI."""
        if operation not in {"deposit", "withdraw", "swap"}:
            raise ValueError("pc operation must be deposit, withdraw, or swap")
        if box not in range(BOX_COUNT):
            raise ValueError("box must be 0..13")
        before_party = self.observe()["party"]
        before_storage = self.pokemon_storage()
        self._open_pc_storage_menu()
        if operation in {"deposit", "swap"}:
            if party_slot is None:
                raise ValueError("deposit/swap requires party_slot")
            self._pc_deposit_from_main(int(party_slot), box)
        if operation in {"withdraw", "swap"}:
            if box_slot is None:
                raise ValueError("withdraw/swap requires box_slot")
            self._pc_withdraw_from_main(box, int(box_slot), menu_cursor=1 if operation == "swap" else 0)

        after = self._close_pc_storage()
        after_storage = self.pokemon_storage()
        before_ids = {
            mon["state"]["personality"] for mon in before_party.get("mons", []) if mon.get("present")
        }
        after_ids = {
            mon["state"]["personality"] for mon in after["party"].get("mons", []) if mon.get("present")
        }
        storage_before_ids = {record["state"]["personality"] for record in before_storage["records"]}
        storage_after_ids = {record["state"]["personality"] for record in after_storage["records"]}
        if operation == "deposit" and not (before_ids - after_ids <= storage_after_ids):
            raise RuntimeError("pc_deposit_identity_verification_failed")
        if operation == "withdraw" and not (storage_before_ids - storage_after_ids <= after_ids):
            raise RuntimeError("pc_withdraw_identity_verification_failed")
        if operation == "swap" and (len(before_ids) != len(after_ids) or before_ids == after_ids):
            raise RuntimeError("pc_swap_identity_verification_failed")
        return {
            "operation": operation,
            "verified": True,
            "party_before": [(mon["slot"], mon["state"]["species"]) for mon in before_party.get("mons", []) if mon.get("present")],
            "party_after": [(mon["slot"], mon["state"]["species"]) for mon in after["party"].get("mons", []) if mon.get("present")],
            "storage_occupied_before": before_storage["occupied"],
            "storage_occupied_after": after_storage["occupied"],
            "state": after,
        }

    @staticmethod
    def _party_grid_keys(current: int, target: int) -> list[str]:
        """Shortest path on the field party screen's vertical cursor ring."""
        if current not in range(6) or target not in range(6):
            raise ValueError("party grid slots must be 0..5")
        ring = (0, 1, 2, 3, 4, 5, 7)
        start, end = ring.index(current), ring.index(target)
        down, up = (end - start) % len(ring), (start - end) % len(ring)
        return ["DOWN"] * down if down <= up else ["UP"] * up

    def _move_party_grid_cursor(self, target: int) -> int:
        current = self.gba.read8(FIELD_PARTY_CURSOR)
        for key in self._party_grid_keys(current, target):
            self._pc_tap(key, wait_frames=30)
        final = self.gba.read8(FIELD_PARTY_CURSOR)
        if final != target:
            raise RuntimeError(f"party_grid_cursor_failed: expected={target} got={final}")
        return final

    @staticmethod
    def _party_switch_row(row_count: int) -> int:
        """Locate Switch after Summary and any dynamic field-move rows."""
        if row_count < 4:
            raise ValueError("party command menu must contain at least four rows")
        return row_count - 3

    @staticmethod
    def _nickname_keyboard_plan(nickname: str) -> tuple[str, list[str]]:
        """Normalize and route the auto-title-casing nickname grid."""
        if not nickname.isascii() or re.fullmatch(r"[A-Za-z]{1,10}", nickname) is None:
            raise ValueError("nickname must contain 1..10 ASCII letters")
        normalized = nickname[:1].upper() + nickname[1:].lower()
        rows = ("ABCDEF", "GHIJKL", "MNOPQRS", "TUVWXYZ")
        positions = {character: (x, y) for y, row in enumerate(rows) for x, character in enumerate(row)}

        def axis(current: int, target: int, size: int, positive: str, negative: str) -> list[str]:
            forward = (target - current) % size
            backward = (current - target) % size
            return [positive] * forward if forward <= backward else [negative] * backward

        x = y = 0
        keys: list[str] = []
        for character in normalized.upper():
            target_x, target_y = positions[character]
            keys.extend(axis(y, target_y, 4, "DOWN", "UP"))
            # Horizontal wrap includes the right-side button column (x=8).
            keys.extend(axis(x, target_x, 9, "RIGHT", "LEFT"))
            keys.append("A")
            x, y = target_x, target_y
        return normalized, keys + ["START", "A"]

    def _type_nickname_keyboard(self, nickname: str) -> str:
        """Type one validated nickname into an already-open naming screen."""
        normalized, keyboard = self._nickname_keyboard_plan(nickname)
        if self.gba.read8(FIELD_MESSAGE_BOX_MODE) != 10:
            raise RuntimeError("nickname_screen_not_ready")
        for key in keyboard[:-2]:
            self._pc_tap(key, wait_frames=90 if key == "A" else 24, hold_frames=12)
        self._pc_tap("START", wait_frames=60, hold_frames=12)
        self._pc_tap("A", wait_frames=300, hold_frames=12)
        return normalized

    def party_reorder(self, personalities: list[int]) -> dict[str, Any]:
        """Reorder the full party through the field UI and verify stable identities."""
        before = self.observe()
        if before.get("mode") != "overworld" or before.get("battle", {}).get("active"):
            raise RuntimeError("party_reorder_requires_clean_overworld")
        current = [
            int(mon["state"]["personality"])
            for mon in before.get("party", {}).get("mons", [])
            if mon.get("present")
        ]
        desired = [int(value) for value in personalities]
        if len(current) != len(desired) or len(set(desired)) != len(desired) or set(current) != set(desired):
            raise ValueError("party order must contain every current personality exactly once")
        if current == desired:
            return {"verified": True, "changed": False, "order_before": current, "order_after": current, "state": before}

        self._pc_tap("START", wait_frames=60)
        if not self._field_start_menu_open() or self.gba.read8(FIELD_MESSAGE_BOX_MODE) != 2:
            raise RuntimeError("party_reorder_start_menu_not_ready")
        self._move_field_cursor(FIELD_MENU_CURSOR, 1, max_steps=8)
        self._pc_tap("A")
        if self.gba.read8(FIELD_MESSAGE_BOX_MODE) != 11:
            raise RuntimeError("party_reorder_selector_not_ready")

        swaps: list[dict[str, int]] = []
        for target_slot, personality in enumerate(desired):
            source_slot = current.index(personality)
            if source_slot == target_slot:
                continue
            self._move_party_grid_cursor(source_slot)
            self._pc_tap("A")
            rows = 1
            for _ in range(7):
                self._pc_tap("DOWN", wait_frames=30)
                if self.gba.read8(FIELD_MENU_CURSOR) == 0:
                    break
                rows += 1
            else:
                raise RuntimeError("party_command_menu_did_not_wrap")
            self._move_field_cursor(
                FIELD_MENU_CURSOR, self._party_switch_row(rows), max_steps=8
            )
            self._pc_tap("A")
            for key in self._party_grid_keys(source_slot, target_slot):
                self._pc_tap(key, wait_frames=30)
            self._pc_tap("A")
            current[source_slot], current[target_slot] = current[target_slot], current[source_slot]
            actual = [
                int(mon["state"]["personality"])
                for mon in self.observe().get("party", {}).get("mons", [])
                if mon.get("present")
            ]
            if actual != current:
                raise RuntimeError(f"party_reorder_identity_mismatch: expected={current} actual={actual}")
            swaps.append({"from": source_slot, "to": target_slot, "personality": personality})

        self._pc_tap("B")
        self._pc_tap("B")
        after = self.observe()
        final = [
            int(mon["state"]["personality"])
            for mon in after.get("party", {}).get("mons", [])
            if mon.get("present")
        ]
        if after.get("mode") != "overworld" or final != desired:
            raise RuntimeError(f"party_reorder_cleanup_failed: mode={after.get('mode')} order={final}")
        return {
            "verified": True,
            "changed": True,
            "order_before": [int(mon["state"]["personality"]) for mon in before["party"]["mons"] if mon.get("present")],
            "order_after": final,
            "swaps": swaps,
            "state": after,
        }

    def field_move_options(self, party_slot: int = 0, *, open_first_field_move: bool = False) -> dict[str, Any]:
        """Open one party command menu, report it, and cancel without selection."""
        before = self.observe()
        party = [mon for mon in before.get("party", {}).get("mons", []) if mon.get("present")]
        if before.get("mode") != "overworld" or before.get("battle", {}).get("active"):
            raise RuntimeError("field_move_options_requires_clean_overworld")
        if party_slot not in range(len(party)):
            raise ValueError("party_slot_out_of_range")

        self._pc_tap("START", wait_frames=60)
        if not self._field_start_menu_open() or self.gba.read8(FIELD_MESSAGE_BOX_MODE) != 2:
            raise RuntimeError("field_move_options_start_menu_not_ready")
        self._move_field_cursor(FIELD_MENU_CURSOR, 1, max_steps=8)
        self._pc_tap("A")
        if self.gba.read8(FIELD_MESSAGE_BOX_MODE) != 11:
            raise RuntimeError("field_move_options_party_menu_not_ready")
        self._move_party_grid_cursor(party_slot)
        self._pc_tap("A")

        menu = self.observe(screenshot=True)
        start_cursor = self.gba.read8(FIELD_MENU_CURSOR)
        rows = {start_cursor}
        for _ in range(8):
            self._pc_tap("DOWN", wait_frames=30)
            cursor = self.gba.read8(FIELD_MENU_CURSOR)
            if cursor in rows:
                break
            rows.add(cursor)
        else:
            raise RuntimeError("field_move_options_menu_did_not_wrap")

        field_screen = None
        if open_first_field_move:
            if len(rows) < 5:
                raise RuntimeError("field_move_options_no_field_move_row")
            self._move_field_cursor(FIELD_MENU_CURSOR, 1, max_steps=8)
            self._pc_tap("A", wait_frames=180)
            field_screen = self.observe(screenshot=True)

        for _ in range(6):
            self._pc_tap("B")
            after = self.observe()
            if after.get("mode") == "overworld" and not self._field_start_menu_open():
                break
        else:
            raise RuntimeError("field_move_options_cleanup_failed")
        if after.get("map") != before.get("map"):
            raise RuntimeError("field_move_options_changed_map")
        return {
            "party_slot": party_slot,
            "personality": int(party[party_slot]["state"]["personality"]),
            "row_count": len(rows),
            "cursor_rows": sorted(rows),
            "text": self._active_field_page_texts(menu),
            "screenshot": menu.get("screenshot"),
            "field_screen": None if field_screen is None else {
                "ui": field_screen.get("ui"),
                "text": self._active_field_page_texts(field_screen),
                "screenshot": field_screen.get("screenshot"),
                "tasks": [
                    task for task in (field_screen.get("tasks") or {}).get("tasks", [])
                    if task.get("active")
                ],
            },
            "state": after,
        }

    def heal_party(self) -> dict[str, Any]:
        """Use the Center nurse and verify HP, status, and move PP from RAM/ROM."""
        before = self.observe()
        if before.get("mode") != "overworld" or before.get("battle", {}).get("active"):
            raise RuntimeError("heal_requires_clean_overworld")
        candidates = [
            obj for obj in self.live_objects()
            if obj.get("local_id") == 1 and obj.get("graphics_id") == 58
        ]
        if len(candidates) != 1:
            raise RuntimeError(f"nurse_identity_ambiguous: {candidates}")
        npc = candidates[0]
        map_state = before.get("map") or {}
        map_id = (map_state.get("group"), map_state.get("number"))
        self.follow_live_path_adaptive(
            (int(npc["current_x"]), int(npc["current_y"]) + 2),
            expected_map=map_id,
            avoid_trainer_sight_lines=False,
        )
        self._pc_tap("UP", wait_frames=12)
        self._pc_tap("A")
        first_text = (((self.observe().get("text") or {}).get("current") or {}).get("text", ""))
        if "Pokémon" not in first_text:
            raise RuntimeError(f"nurse_dialogue_not_verified: {first_text!r}")
        pages = []
        for _ in range(12):
            state = self.observe()
            if state.get("mode") == "overworld":
                break
            if state.get("battle", {}).get("active"):
                raise RuntimeError("heal_interrupted_by_battle")
            field_mode = state.get("ui", {}).get("field_message_box_mode")
            if field_mode in {10, 16, 42, 50}:
                raise RuntimeError(f"heal_reached_unsafe_mode: {field_mode}")
            text = (((state.get("text") or {}).get("current") or {}).get("text"))
            if text:
                pages.append(text)
                self._pc_tap("A", wait_frames=90)
            else:
                self.gba.wait_frames(30)
        else:
            raise RuntimeError("heal_dialogue_exceeded_page_limit")

        after = self.observe()
        rom = self.rom_data()
        failures = []
        for mon in after.get("party", {}).get("mons", []):
            if not mon.get("present"):
                continue
            state = mon["state"]
            expected_pp = []
            for slot, move_id in enumerate(state.get("moves", ())):
                if not move_id:
                    expected_pp.append(0)
                    continue
                bonus = (int(state.get("pp_bonuses", 0)) >> (2 * slot)) & 3
                base = int(rom.move(int(move_id)).pp)
                expected_pp.append(base * (5 + bonus) // 5)
            if (
                int(state.get("current_hp", 0)) != int(state.get("max_hp", 0))
                or int(state.get("status", 0)) != 0
                or list(state.get("pp", ())) != expected_pp
            ):
                failures.append({"slot": mon["slot"], "hp": [state.get("current_hp"), state.get("max_hp")], "status": state.get("status"), "pp": list(state.get("pp", ())), "expected_pp": expected_pp})
        if failures:
            raise RuntimeError(f"heal_ram_verification_failed: {failures}")
        return {"verified": True, "nurse": {"local_id": 1, "graphics_id": 58, "position": npc.get("position")}, "pages": pages, "state": after}

    def pokecenter_apply_status(self, target_slot: int, status_name: str) -> dict[str, Any]:
        """Apply one utility-NPC status and verify the party RAM bitfield."""
        status_options = {"burn": 0x10, "freeze": 0x20, "paralysis": 0x40, "poison": 0x08, "sleep": 0x07}
        key = status_name.casefold()
        if key not in status_options:
            raise ValueError(f"unsupported status: {status_name!r}")
        before = self.observe()
        party = before.get("party", {})
        if target_slot not in range(int(party.get("count", 0))):
            raise IndexError(f"party slot {target_slot} is unavailable")
        target = party["mons"][target_slot]["state"]
        if target.get("status"):
            raise RuntimeError("status_service_target_already_statused")
        candidates = [
            obj for obj in self.live_objects()
            if obj.get("local_id") in {4, 5, 6} and obj.get("graphics_id") in {28, 70}
        ]
        if len(candidates) != 1:
            raise RuntimeError(f"utility_npc_identity_ambiguous: {candidates}")
        npc = candidates[0]
        map_state = before.get("map") or {}
        map_id = (map_state.get("group"), map_state.get("number"))
        self.follow_live_path_adaptive(
            (int(npc["current_x"]), int(npc["current_y"]) + 2),
            expected_map=map_id,
            avoid_trainer_sight_lines=False,
        )
        self._pc_tap("UP", wait_frames=12)
        self._pc_tap("A")
        greeting = ((self.observe().get("text") or {}).get("current") or {}).get("text", "")
        if "How can I help you today" not in greeting:
            raise RuntimeError(f"utility_npc_greeting_not_verified: {greeting!r}")
        self._pc_tap("A")  # Open the six-entry service menu.
        for _ in range(5):
            self._pc_tap("DOWN")
        self._pc_tap("A")  # Apply status.
        self._pc_tap("A")  # Open status menu after its explanation.
        for _ in range(("burn", "freeze", "paralysis", "poison", "sleep").index(key)):
            self._pc_tap("DOWN")
        self._pc_tap("A")  # Chosen status; ask for target.
        for _ in range(3):
            self._pc_tap("A")
            if self.gba.read8(FIELD_MESSAGE_BOX_MODE) in {11, 13, 15, 19}:
                break
        else:
            raise RuntimeError("status_service_party_selector_not_ready")
        self._move_field_cursor(FIELD_PARTY_CURSOR, target_slot)
        self._pc_tap("A")
        self._pc_tap("A")  # Open confirmation choice.
        if self.gba.read8(YES_NO_CURSOR) == 1:
            self._pc_tap("UP")
        self._pc_tap("A")
        self.gba.wait_frames(180)
        after = self.observe()
        actual = after["party"]["mons"][target_slot]["state"].get("status", 0)
        expected = status_options[key]
        verified = bool(actual & expected) if key == "sleep" else (actual & expected) == expected
        if not verified:
            raise RuntimeError(f"status_service_verification_failed: expected={key} actual={actual:#x}")
        acknowledgement = (((after.get("text") or {}).get("last_page") or {}).get("text", ""))
        if "status condition" not in acknowledgement:
            raise RuntimeError(f"status_service_acknowledgement_missing: {acknowledgement!r}")
        # The custom script drops field mode to zero before dismissing its
        # success box, so mode=overworld is not yet proof that movement owns
        # input. Exactly one acknowledged A releases the script lock.
        self._pc_tap("A")
        final = self.observe()
        if final.get("mode") != "overworld" or final.get("ui", {}).get("field_message_box_mode") != 0:
            raise RuntimeError("status_service_cleanup_failed")
        return {
            "service": "apply_status",
            "status": status_name,
            "target_slot": target_slot,
            "target_species": target["species"],
            "status_before": target.get("status", 0),
            "status_after": actual,
            "verified": True,
            "state": final,
        }

    def pokecenter_change_nickname(self, target_slot: int, nickname: str) -> dict[str, Any]:
        """Change one party nickname and verify identity and text from RAM."""
        normalized, _ = self._nickname_keyboard_plan(nickname)
        before = self.observe()
        if before.get("mode") != "overworld" or before.get("battle", {}).get("active"):
            raise RuntimeError("nickname_service_requires_clean_overworld")
        party = before.get("party", {})
        if target_slot not in range(int(party.get("count", 0))):
            raise IndexError(f"party slot {target_slot} is unavailable")
        target = party["mons"][target_slot]["state"]
        personality = int(target["personality"])
        old_nickname = str(target.get("nickname", ""))
        if old_nickname == normalized:
            return {
                "service": "change_nickname", "target_slot": target_slot,
                "personality": personality, "nickname_before": old_nickname,
                "nickname_after": normalized, "changed": False, "verified": True,
                "state": before,
            }

        candidates = [
            obj for obj in self.live_objects()
            if obj.get("local_id") in {4, 5, 6} and obj.get("graphics_id") in {28, 70}
        ]
        if len(candidates) != 1:
            raise RuntimeError(f"utility_npc_identity_ambiguous: {candidates}")
        npc = candidates[0]
        map_state = before.get("map") or {}
        map_id = (map_state.get("group"), map_state.get("number"))
        self.follow_live_path_adaptive(
            (int(npc["current_x"]), int(npc["current_y"]) + 2),
            expected_map=map_id,
            avoid_trainer_sight_lines=False,
        )

        def tap(key: str, wait_frames: int = 120) -> None:
            self._pc_tap(key, wait_frames=wait_frames, hold_frames=12)

        tap("UP", 12)
        tap("A")
        greeting = ((self.observe().get("text") or {}).get("current") or {}).get("text", "")
        if "How can I help you today" not in greeting:
            raise RuntimeError(f"utility_npc_greeting_not_verified: {greeting!r}")
        tap("A")
        for _ in range(4):
            tap("DOWN")
        tap("A")
        selected = ((self.observe().get("text") or {}).get("current") or {}).get("text", "")
        if "So you want to change a Pokémon's" not in selected:
            raise RuntimeError(f"nickname_service_selection_not_verified: {selected!r}")
        tap("A")
        prompt = ((self.observe().get("text") or {}).get("current") or {}).get("text", "")
        if not nickname_target_prompt_verified(prompt):
            raise RuntimeError(f"nickname_service_target_prompt_not_verified: {prompt!r}")
        tap("A")
        if self.gba.read8(FIELD_MESSAGE_BOX_MODE) != 11:
            raise RuntimeError("nickname_service_party_selector_not_ready")
        self._move_field_cursor(FIELD_PARTY_CURSOR, target_slot)
        tap("A")

        for _ in range(4):
            if self.gba.read8(FIELD_MESSAGE_BOX_MODE) == 10:
                break
            observed = self.observe()
            text = observed.get("text") or {}
            rendered = "\n".join(
                value for value in (
                    ((text.get("current") or {}).get("text") or ""),
                    ((text.get("last_page") or {}).get("text") or ""),
                ) if value
            )
            if "Do you want" in rendered and self.gba.read8(YES_NO_CURSOR) == 1:
                tap("UP")
            tap("A")
        else:
            raise RuntimeError("nickname_screen_not_ready")

        self._type_nickname_keyboard(normalized)
        changed = self.observe()
        changed_target = changed["party"]["mons"][target_slot]["state"]
        if int(changed_target["personality"]) != personality or changed_target.get("nickname") != normalized:
            raise RuntimeError(
                f"nickname_service_verification_failed: expected={normalized!r} "
                f"actual={changed_target.get('nickname')!r}"
            )

        for _ in range(2):
            acknowledgement = self.observe()
            if acknowledgement.get("mode") == "overworld":
                break
            tap("A", 180)
        else:
            raise RuntimeError("nickname_service_acknowledgement_not_dismissed")
        # The custom script clears field mode one command before releasing
        # input ownership. B closes that retained command without re-talking
        # to the NPC (A would immediately reopen its service menu).
        tap("B")
        final = self.follow_live_path_adaptive(
            (10, 4), expected_map=map_id, avoid_trainer_sight_lines=False
        )["state"]
        final_target = final["party"]["mons"][target_slot]["state"]
        if (
            final.get("mode") != "overworld"
            or int(final_target["personality"]) != personality
            or final_target.get("nickname") != normalized
        ):
            raise RuntimeError("nickname_service_cleanup_failed")
        return {
            "service": "change_nickname", "target_slot": target_slot,
            "target_species": target["species"], "personality": personality,
            "nickname_before": old_nickname, "nickname_after": normalized,
            "changed": True, "verified": True, "state": final,
        }

    def _battle_printer_contexts(self, printers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Decode the transient battle-text buffers behind active printers.

        Field dialogue uses ``gStringVar4`` and is covered by the bridge's
        normal text observation. Battle messages use short transient buffers
        elsewhere in EWRAM; the printer's current-character pointer is the
        stable handle to those buffers. Keeping a small context window here
        exposes battle messages and the ``0x70`` command-menu marker without
        requiring a framebuffer read.
        """
        contexts: list[dict[str, Any]] = []
        for printer in printers:
            if not printer.get("active"):
                continue
            current_char = int(printer.get("current_char", 0))
            if not 0x02000000 <= current_char < 0x02040000:
                continue
            start = max(0x02000000, current_char - 128)
            length = min(256, 0x02040000 - start)
            try:
                raw = self.gba.read_range(start, length)
            except Exception:
                # Minimal fake clients and older bridge versions may expose
                # printer metadata without range reads; retain the metadata.
                contexts.append(
                    {
                        "printer_address": printer.get("address"),
                        "current_char": current_char,
                        "text": None,
                    }
                )
                continue
            contexts.append(
                {
                    "printer_address": printer.get("address"),
                    "current_char": current_char,
                    "start": start,
                    "end": start + len(raw),
                    "text": self._current_printer_text(raw, current_char - start),
                }
            )
        return contexts

    @staticmethod
    def _current_printer_text(raw: bytes, cursor: int) -> str:
        """Decode the string owned by a printer, ignoring adjacent stale strings."""
        if cursor > 0 and raw[cursor - 1] == 0xFF:
            end = cursor - 1
            start = raw.rfind(b"\xff", 0, end) + 1
        else:
            start = raw.rfind(b"\xff", 0, cursor) + 1
            end = raw.find(b"\xff", cursor)
            if end < 0:
                end = len(raw)
        return decode_gen3_text(raw[start:end] + b"\xff")

    @staticmethod
    def _battle_command_prompt(contexts: list[dict[str, Any]]) -> bool:
        """Recognize the battle command prompt from transient printer RAM."""
        for context in contexts:
            value = (context.get("text") or "").rstrip()
            # The command selector in this ROM is rendered from a transient
            # printer buffer. Its prompt survives the field-mode transition as
            # the exact localized string below, while ordinary move messages
            # do not contain this question.
            if "What will" in value and "do?" in value:
                return True
        return False

    @staticmethod
    def _battle_move_prompt(contexts: list[dict[str, Any]]) -> bool:
        """Recognize the move selector from its RAM-backed type/PP printer."""
        for context in contexts:
            value = (context.get("text") or "").rstrip()
            # This hack's custom move window sometimes omits the PP label
            # while the selector is first being drawn.  ``Type/`` plus a live
            # active battle printer is already a stronger signal than a
            # framebuffer guess; PP is still included when present.
            if "Type/" in value:
                return True
        return False

    @staticmethod
    def _battle_move_prompt_details(contexts: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Decode the selected move's type/PP from the live RAM printer."""
        for context in contexts:
            value = (context.get("text") or "").rstrip()
            if "Type/" not in value:
                continue
            type_match = re.search(
                r"Type/(?:<CTRL_[^>]+>)*([A-Za-z]+)", value
            )
            pp_match = re.search(r"PP\s*(\d+)\s*/\s*(\d+)", value)
            return {
                "type": type_match.group(1) if type_match else None,
                "pp": int(pp_match.group(1)) if pp_match else None,
                "max_pp": int(pp_match.group(2)) if pp_match else None,
                "text": value,
            }
        return None

    @staticmethod
    def _battle_command_subject(contexts: list[dict[str, Any]]) -> str | None:
        """Return the battler named by the live ``What will … do?`` prompt."""
        for context in contexts:
            value = (context.get("text") or "").strip()
            match = re.search(r"What will\s+(.+?)\s+do\?", value, re.DOTALL)
            if match:
                return " ".join(match.group(1).split())
        return None

    @staticmethod
    def _battle_format_for_field_mode(field_mode: int) -> str:
        """Use the live battle UI mode, not stale partner battler structs."""
        return (
            "double"
            if field_mode in DOUBLE_BATTLE_FIELD_MESSAGE_MODES
            else "single"
        )

    @staticmethod
    def _battle_menu_state(
        *,
        party_switch_prompt: bool,
        move_prompt: bool,
        command_prompt: bool,
        battle_active: bool,
        battle_format: str,
        battle_target: int,
    ) -> str:
        """Classify battle input state without conflating move and target UI."""
        if party_switch_prompt:
            return "party_switch"
        if move_prompt and battle_format == "double" and battle_target in range(4):
            return "target_menu"
        if move_prompt:
            return "move_menu"
        if command_prompt:
            return "command_menu"
        if battle_active:
            return "battle_text"
        return "none"

    @staticmethod
    def _battle_party_switch_prompt(contexts: list[dict[str, Any]]) -> bool:
        """Recognize the forced party choice after a battler faints."""
        for context in contexts:
            value = (context.get("text") or "").rstrip()
            if "Choose a Pokémon." in value or "Use next Pokémon?" in value:
                return True
        return False

    @staticmethod
    def _active_player_fainted(battle: dict[str, Any]) -> bool:
        active = next(
            (item for item in battle.get("mons", []) if item.get("present") and item.get("slot") == 0),
            None,
        )
        return bool(active and active.get("state", {}).get("current_hp", 0) <= 0)

    @staticmethod
    def _battle_printer_text(contexts: list[dict[str, Any]]) -> str:
        return "\n".join(
            (context.get("text") or "").strip()
            for context in contexts
            if context.get("text")
        )

    @classmethod
    def _battle_kind(cls, contexts: list[dict[str, Any]]) -> str | None:
        """Classify a live fight from RAM text when the ROM exposes it."""
        text = cls._battle_printer_text(contexts).lower()
        if "trainer battle" in text or "trainer sent" in text:
            return "trainer"
        if "wild" in text:
            return "wild"
        return None

    @staticmethod
    def _battle_effectiveness(text: str) -> float | None:
        lowered = text.lower()
        if "doesn't affect" in lowered or "no effect" in lowered:
            return 0.0
        if "super effective" in lowered:
            return 2.0
        if "not very effective" in lowered:
            return 0.5
        return None

    @classmethod
    def _mon_types(cls, mon: dict[str, Any]) -> tuple[int, ...]:
        state = mon.get("state", mon)
        # This build repeats a single type in both type bytes (for example
        # Grass/Grass/Unknown). Keep the first occurrence only: multiplying
        # the chart by duplicate type bytes falsely squares effectiveness.
        types = tuple(dict.fromkeys(type_id for type_id in state.get("types", ()) if type_id != 9))
        return types or SPECIES_TYPE_IDS.get(state.get("species"), ())

    @classmethod
    def _stage_multiplier(cls, state: dict[str, Any], stat_key: str) -> float:
        """Apply the live Gen III stat-stage ratio to a decoded battler stat."""
        stage_index = {
            # The verified Run & Bun battler layout keeps byte 0 as the
            # reserved HP stage; the live Attack decrement appears at byte 1.
            "attack": 1,
            "defense": 2,
            "speed": 3,
            "special_attack": 4,
            "special_defense": 5,
        }.get(stat_key)
        stages = state.get("stat_stages") or ()
        if stage_index is None or stage_index >= len(stages):
            return 1.0
        stage = max(0, min(12, int(stages[stage_index])))
        if stage >= 6:
            return (2 + stage - 6) / 2
        return 2 / (2 + 6 - stage)

    @classmethod
    def _accuracy_stage_multiplier(cls, state: dict[str, Any]) -> float:
        """Return the Gen III accuracy-stage ratio for a live battler."""
        stages = state.get("stat_stages") or ()
        if len(stages) <= 6:
            return 1.0
        stage = max(0, min(12, int(stages[6])))
        if stage >= 6:
            return (3 + stage - 6) / 3
        return 3 / (3 + 6 - stage)

    @classmethod
    def _evasion_stage_multiplier(cls, state: dict[str, Any]) -> float:
        stages = state.get("stat_stages") or ()
        if len(stages) <= 7:
            return 1.0
        stage = max(0, min(12, int(stages[7])))
        if stage >= 6:
            return (3 + stage - 6) / 3
        return 3 / (3 + 6 - stage)

    @classmethod
    def _effective_speed(cls, state: dict[str, Any]) -> float:
        speed = float(state.get("speed", 0)) * cls._stage_multiplier(state, "speed")
        if int(state.get("status", 0)) & PARALYSIS_STATUS:
            speed *= 0.25
        return speed

    @classmethod
    def _damage_bounds(
        cls,
        move_id: int,
        attacker: dict[str, Any],
        defender: dict[str, Any],
        *,
        effectiveness_memory: dict[tuple[int, int], float] | None = None,
        type_chart: dict[int, dict[int, float]] | None = None,
        damage_memory: dict[tuple[int, int, int], list[int]] | None = None,
        move_data: dict[int, RomMove] | None = None,
    ) -> tuple[float, float]:
        """Return a conservative normal-roll damage interval.

        Learned samples are evidence, not a point estimate: the minimum is
        safe for guaranteed outgoing KOs and the maximum is safe for the
        normal-hit incoming threat. Static estimates use the Gen III random
        roll interval; unknown move metadata remains zero and therefore
        cannot create a false forced KO.
        """
        metadata = (move_data or {}).get(move_id)
        if metadata is not None and metadata.category == "status":
            return (0.0, 0.0)
        if metadata is None and move_id in STATUS_MOVE_IDS:
            return (0.0, 0.0)
        attacker_state = attacker.get("state", attacker)
        defender_state = defender.get("state", defender)
        fixed_denominator = MOVE_FIXED_DAMAGE_FRACTIONS.get(move_id)
        if fixed_denominator is not None:
            current_hp = int(defender_state.get("current_hp", 0) or 0)
            if current_hp <= 0:
                return (0.0, 0.0)
            damage = float(current_hp // fixed_denominator)
            return (damage, damage)
        learned = (damage_memory or {}).get(
            (attacker_state.get("species"), move_id, defender_state.get("species")),
            (),
        )
        if learned:
            if isinstance(learned, dict):
                samples = learned.get("samples") or []
            else:
                samples = learned
            if samples:
                move_type = metadata.type_id if metadata is not None else MOVE_TYPE_IDS.get(move_id)
                # Legacy samples do not carry their stage context, so they
                # are never a standalone bound for a known move. Keep them
                # useful for custom/unknown move IDs, but let the verified
                # ROM formula below bound known moves across stat stages and
                # random rolls.
                if move_type is None:
                    return (
                        max(1.0, float(min(samples))),
                        max(1.0, float(max(samples))),
                    )
        move_type = metadata.type_id if metadata is not None else MOVE_TYPE_IDS.get(move_id)
        if move_type is None:
            return (0.0, 0.0)
        power = metadata.power if metadata is not None else MOVE_POWER.get(move_id, 40)
        if move_id == 49:  # Sonic Boom is fixed 20 damage in this battle.
            return (20.0, 20.0)
        special = metadata.category == "special" if metadata is not None else move_id in MOVE_SPECIAL_IDS
        if metadata is not None and metadata.category == "unknown":
            return (0.0, 0.0)
        attack_key = "special_attack" if special else "attack"
        defense_key = "special_defense" if special else "defense"
        attack = attacker_state.get(attack_key, 0)
        defense = defender_state.get(defense_key, 0)
        level = attacker_state.get("level", 0)
        if not attack or not defense or not level:
            return (0.0, 0.0)
        if defender_state.get("ability") == ABILITY_LEVITATE and move_type == 4:
            return (0.0, 0.0)
        attack = max(1, math.floor(attack * cls._stage_multiplier(attacker_state, attack_key)))
        if not special and int(attacker_state.get("status", 0) or 0) & BURN_STATUS and attacker_state.get("ability") != ABILITY_GUTS:
            attack = max(1, attack // 2)
        defense = max(1, math.floor(defense * cls._stage_multiplier(defender_state, defense_key)))
        defender_types = cls._mon_types(defender)
        effectiveness = 1.0
        for defender_type in defender_types:
            chart = type_chart or TYPE_EFFECTIVENESS
            effectiveness *= chart.get(move_type, {}).get(defender_type, 1.0)
        remembered = (effectiveness_memory or {}).get((defender_state.get("species"), move_id))
        if remembered is not None:
            effectiveness = remembered
        if effectiveness <= 0:
            return (0.0, 0.0)
        stab = 1.5 if move_type in cls._mon_types(attacker) else 1.0
        if attacker_state.get("ability") == ABILITY_FLASH_FIRE and move_type == 10:
            return (0.0, 0.0)
        base = (((2 * int(level) // 5 + 2) * int(power) * attack) // defense) // 50 + 2

        def cartridge_roll(roll: int) -> int:
            damage = base * roll // 100
            if stab > 1:
                damage = damage * 15 // 10
            multipliers = (
                (remembered,)
                if remembered is not None
                else tuple(
                    (type_chart or TYPE_EFFECTIVENESS).get(move_type, {}).get(defender_type, 1.0)
                    for defender_type in defender_types
                )
            )
            for multiplier in multipliers:
                damage = damage * int(round(multiplier * 0x1000)) // 0x1000
            return max(1, damage)

        values = [cartridge_roll(roll) for roll in range(85, 101)]
        return (float(min(values)), float(max(values)))

    @classmethod
    def _estimated_damage(
        cls,
        move_id: int,
        attacker: dict[str, Any],
        defender: dict[str, Any],
        *,
        effectiveness_memory: dict[tuple[int, int], float] | None = None,
        type_chart: dict[int, dict[int, float]] | None = None,
        damage_memory: dict[tuple[int, int, int], list[int]] | None = None,
        move_data: dict[int, RomMove] | None = None,
    ) -> float:
        """Return the midpoint of the bounded interval for ranking only."""
        low, high = cls._damage_bounds(
            move_id,
            attacker,
            defender,
            effectiveness_memory=effectiveness_memory,
            type_chart=type_chart,
            damage_memory=damage_memory,
            move_data=move_data,
        )
        return (low + high) / 2

    @classmethod
    def _tactical_battle_action(
        cls,
        player: dict[str, Any],
        opponent: dict[str, Any],
        party: list[dict[str, Any]],
        *,
        effectiveness_memory: dict[tuple[int, int], float] | None,
        type_chart: dict[int, dict[int, float]] | None,
        damage_memory: dict[tuple[int, int, int], list[int]] | None,
        move_data: dict[int, RomMove] | None,
        low_hp_fraction: float,
        allow_switch: bool,
    ) -> dict[str, Any] | None:
        """Plan one turn using KO timing and the next incoming hit.

        Return ``None`` when a test/minimal observation lacks live stats; the
        older conservative scorer below remains the compatibility fallback.
        """
        player_state = player.get("state", player)
        opponent_state = opponent.get("state", opponent)
        required = ("attack", "defense", "speed", "special_attack", "special_defense", "level")
        if not all(player_state.get(key) is not None for key in required):
            return None
        if not all(opponent_state.get(key) is not None for key in required):
            return None

        def move_options(attacker: dict[str, Any], defender: dict[str, Any]) -> list[dict[str, Any]]:
            state = attacker.get("state", attacker)
            result = []
            for slot, move_id in enumerate(state.get("moves", ())):
                pp = state.get("pp", (0, 0, 0, 0))[slot]
                if not move_id or not pp:
                    continue
                damage_min, damage_max = cls._damage_bounds(
                    move_id,
                    attacker,
                    defender,
                    effectiveness_memory=effectiveness_memory,
                    type_chart=type_chart,
                    damage_memory=damage_memory,
                    move_data=move_data,
                )
                result.append({
                    "slot": slot,
                    "move_id": move_id,
                    "damage": (damage_min + damage_max) / 2,
                    "damage_min": damage_min,
                    "damage_max": damage_max,
                })
            return result

        active_moves = move_options(player, opponent)
        if not active_moves:
            return None
        best_move = max(
            active_moves,
            key=lambda item: (
                item["damage"],
                (move_data or {}).get(item["move_id"], None) is not None
                and (move_data or {})[item["move_id"]].category != "status"
                or item["move_id"] not in STATUS_MOVE_IDS,
                -item["slot"],
            ),
        )
        opponent_hp = opponent_state.get("current_hp", 0)
        player_hp = player_state.get("current_hp", 0)
        active_fraction = player_hp / max(player_state.get("max_hp", 1), 1)
        incoming = max(
            (cls._damage_bounds(move_id, opponent, player, type_chart=type_chart, damage_memory=damage_memory, move_data=move_data)[1] for move_id in opponent_state.get("moves", ()) if move_id),
            default=0.0,
        )
        player_speed = cls._effective_speed(player_state)
        opponent_speed = cls._effective_speed(opponent_state)
        speed_order = (
            "first" if player_speed > opponent_speed
            else "second" if player_speed < opponent_speed
            else "tie"
        )
        acts_first = speed_order == "first"
        # If every healthy bench option is also KO'd by the known incoming
        # hit, switching is not a survival action.  In this build Play Nice
        # is the one live move that lowers Attack; use it while the opponent
        # is preparing a physical Bounce instead of throwing away the only
        # viable battler.
        defensive_debuff = next(
            (item for item in active_moves if item["move_id"] in PHYSICAL_THREAT_DEBUFF_IDS),
            None,
        )
        if defensive_debuff and incoming >= max(player_hp, 1) and speed_order != "first":
            # One Attack stage is a 2/3 multiplier. If that still cannot
            # keep the active mon alive, status is no longer a survival line;
            # spend the turn on the highest observed/estimated damage instead.
            if incoming * (2 / 3) < max(player_hp, 1):
                return {
                    "action": "move",
                    "slot": defensive_debuff["slot"],
                    "move_id": defensive_debuff["move_id"],
                    "reason": "defensive_status_vs_physical_threat",
                }
            if best_move["damage_max"] > 0:
                return {
                    "action": "move",
                    "slot": best_move["slot"],
                    "move_id": best_move["move_id"],
                    "reason": "last_damage_line",
                }
        can_finish = best_move["damage_min"] >= opponent_hp > 0
        can_finish_before_hit = can_finish and (acts_first or best_move["move_id"] in MOVE_PRIORITY_IDS)
        turns_to_ko = int((opponent_hp + max(best_move["damage_min"], 1) - 1) // max(best_move["damage_min"], 1))
        # A live, repeatable two-hit line is often better than a speculative
        # switch: keep the active mon if it can absorb the one intervening hit.
        # Priority means the final hit lands before the opponent's next move.
        safe_two_turn_finish = (
            1 < turns_to_ko <= 2
            and player_hp > incoming * (turns_to_ko - 1)
            and (acts_first or best_move["move_id"] in MOVE_PRIORITY_IDS)
        )

        switch_options: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
        if allow_switch:
            for mon in party:
                state = mon.get("state", {})
                if not mon.get("present") or state.get("current_hp", 0) <= 0:
                    continue
                if state.get("species") == player_state.get("species"):
                    continue
                options = move_options(mon, opponent)
                if not options:
                    continue
                best = max(options, key=lambda item: item["damage"])
                threat = max(
                    (cls._damage_bounds(move_id, opponent, mon, type_chart=type_chart, damage_memory=damage_memory, move_data=move_data)[1] for move_id in opponent_state.get("moves", ()) if move_id),
                    default=0.0,
                )
                if threat >= max(state.get("current_hp", 0), 1):
                    continue
                hp_fraction = state.get("current_hp", 0) / max(state.get("max_hp", 1), 1)
                score = best["damage"] + (100.0 if best["damage_min"] >= opponent_hp else 0.0)
                score += hp_fraction * 12.0 + (4.0 if cls._effective_speed(state) > opponent_speed else 0.0)
                score -= threat * 0.75
                switch_options.append((score, mon, best))

        best_switch = max(switch_options, key=lambda item: item[0], default=None)
        # A guaranteed KO wins over a switch.  If the active mon is slower and
        # cannot survive the incoming hit, a safer teammate gets the turn.
        if can_finish_before_hit or (
            can_finish and best_switch is None
        ):
            return {
                "action": "move",
                "slot": best_move["slot"],
                "move_id": best_move["move_id"],
                "reason": "finish_before_switch",
            }
        if safe_two_turn_finish:
            return {
                "action": "move",
                "slot": best_move["slot"],
                "move_id": best_move["move_id"],
                "reason": "safe_two_turn_finish",
            }
        if best_switch is not None and (
            active_fraction <= low_hp_fraction
            or incoming >= max(player_hp, 1) * 0.9
            or (not acts_first and best_switch[2]["damage"] > best_move["damage"])
        ):
            mon = best_switch[1]
            species = mon["state"].get("species")
            return {
                "action": "switch",
                "slot": mon["slot"],
                "species": species,
                "reason": "matchup_survival_and_turn_order",
            }
        return {
            "action": "move",
            "slot": best_move["slot"],
            "move_id": best_move["move_id"],
            "reason": "best_damage_while_surviving",
        }

    @classmethod
    def choose_battle_action(
        cls,
        observation: dict[str, Any],
        *,
        effectiveness_memory: dict[tuple[int, int], float] | None = None,
        type_chart: dict[int, dict[int, float]] | None = None,
        damage_memory: dict[tuple[int, int, int], list[int]] | None = None,
        move_data: dict[int, RomMove] | None = None,
        low_hp_fraction: float = 0.25,
        allow_switch: bool = True,
        actor_slot: int | None = None,
        target_slot: int | None = None,
    ) -> dict[str, Any]:
        """Choose a safe move or a switch using the current RAM observation.

        This is deliberately conservative for an unfamiliar hack: known move
        types get ordinary Gen III effectiveness scores, Flash Fire blocks an
        Ember-like choice, and observed battle feedback overrides the static
        chart for a particular opponent.  A teammate switch is preferred over
        gambling with a critically low active Pokémon.
        """
        if not observation.get("battle", {}).get("active"):
            return {"action": "none", "reason": "not_in_battle"}
        battle = observation["battle"]
        mons = battle.get("mons", [])
        if actor_slot is None:
            actor_slot = battle.get("menu", {}).get("command_battler") if battle.get("format") == "double" else 0
        if actor_slot not in (0, 2):
            actor_slot = 0
        opponents = [slot for slot in (1, 3) if any(m.get("slot") == slot and m.get("present") and m["state"].get("current_hp", 0) > 0 for m in mons)]
        if target_slot not in opponents:
            target_slot = opponents[0] if opponents else 1
        player = next((m["state"] for m in mons if m.get("slot") == actor_slot and m.get("present")), None)
        opponent = next((m["state"] for m in mons if m.get("slot") == target_slot and m.get("present")), None)
        if player is None or opponent is None:
            return {"action": "none", "reason": "battle_mons_incomplete"}

        party = observation.get("party", {}).get("mons", [])
        tactical = cls._tactical_battle_action(
            player,
            opponent,
            party,
            effectiveness_memory=effectiveness_memory,
            type_chart=type_chart,
            damage_memory=damage_memory,
            move_data=move_data,
            low_hp_fraction=low_hp_fraction,
            allow_switch=allow_switch,
        )
        if tactical is not None:
            return tactical
        if allow_switch and player["max_hp"] and player["current_hp"] / player["max_hp"] <= low_hp_fraction:
            healthy = [
                mon for mon in party
                if mon.get("present") and mon["state"].get("current_hp", 0) > 0
                and mon["slot"] != 0
            ]
            if healthy:
                best = max(healthy, key=lambda mon: (mon["state"].get("level", 0), mon["state"].get("current_hp", 0)))
                plan = {
                    "action": "switch",
                    "slot": best["slot"],
                    "reason": "active_hp_low",
                }
                species = best["state"].get("species", best["state"].get("species_id"))
                if species is not None:
                    plan["species"] = species
                return plan

        remembered = effectiveness_memory or {}
        defender_types = {type_id for type_id in opponent.get("types", ()) if type_id != 9}
        candidates: list[tuple[float, int]] = []
        for slot, move_id in enumerate(player.get("moves", ())):
            pp = player.get("pp", (0, 0, 0, 0))[slot]
            if not move_id or not pp:
                continue
            move_type = MOVE_TYPE_IDS.get(move_id)
            if move_type is None:
                score = 1.0
            else:
                score = max(
                    ((type_chart or TYPE_EFFECTIVENESS).get(move_type, {}).get(defender, 1.0) for defender in defender_types),
                    default=1.0,
                )
            score *= MOVE_POWER.get(move_id, 1)
            if move_type is not None and move_type in {
                type_id for type_id in player.get("types", ()) if type_id != 9
            }:
                score *= 1.5
            if opponent.get("ability") == ABILITY_FLASH_FIRE and move_type == 10:
                score = -1000.0
            observed = remembered.get((opponent["species"], move_id))
            if observed is not None:
                score = observed
            if move_id in STATUS_MOVE_IDS:
                score -= 0.25
            # Prefer the earlier move on exact ties so the controller stays
            # deterministic and does not burn time changing cursors.
            candidates.append((score, -slot))
        if not candidates:
            return {"action": "none", "reason": "no_usable_move"}
        _, neg_slot = max(candidates)
        selected = -neg_slot
        return {
            "action": "move",
            "slot": selected,
            "move_id": player["moves"][selected],
            "reason": "effectiveness_and_pp",
        }

    @classmethod
    def explain_battle_action(
        cls,
        observation: dict[str, Any],
        *,
        effectiveness_memory: dict[tuple[int, int], float] | None = None,
        type_chart: dict[int, dict[int, float]] | None = None,
        damage_memory: dict[tuple[int, int, int], list[int]] | None = None,
        move_data: dict[int, RomMove] | None = None,
        low_hp_fraction: float = 0.25,
        allow_switch: bool = True,
        actor_slot: int | None = None,
        target_slot: int | None = None,
    ) -> dict[str, Any]:
        """Return a compact, auditable explanation for one battle turn.

        This is deliberately a proof report, not a claim that hidden RNG or
        trainer AI has been solved.  ``forced_estimate`` means the visible
        model has a faster KO line; ``best_estimate`` means the choice is a
        ranking under incomplete damage/AI information.
        """
        battle = observation.get("battle", {})
        if not battle.get("active"):
            return {"decision": {"action": "none", "reason": "not_in_battle"}, "proof": {"level": "none"}}
        mons = battle.get("mons", [])
        if actor_slot is None:
            actor_slot = battle.get("menu", {}).get("command_battler") if battle.get("format") == "double" else 0
        if actor_slot not in (0, 2):
            actor_slot = 0
        opponents = [slot for slot in (1, 3) if any(m.get("slot") == slot and m.get("present") and m["state"].get("current_hp", 0) > 0 for m in mons)]
        if target_slot not in opponents:
            target_slot = opponents[0] if opponents else 1
        player = next((m["state"] for m in mons if m.get("slot") == actor_slot and m.get("present")), None)
        opponent = next((m["state"] for m in mons if m.get("slot") == target_slot and m.get("present")), None)
        if player is None or opponent is None:
            return {"decision": {"action": "none", "reason": "battle_mons_incomplete"}, "proof": {"level": "none"}}

        memory = damage_memory or {}
        plan = cls.choose_battle_action(
            observation,
            effectiveness_memory=effectiveness_memory,
            type_chart=type_chart,
            damage_memory=memory,
            move_data=move_data,
            low_hp_fraction=low_hp_fraction,
            allow_switch=allow_switch,
            actor_slot=actor_slot,
            target_slot=target_slot,
        )
        opponent_hp = opponent.get("current_hp", 0)
        opponent_speed = cls._effective_speed(opponent)
        player_speed = cls._effective_speed(player)
        opponent_key = opponent.get("species")
        player_key = player.get("species")

        def move_name(state: dict[str, Any], slot: int, move_id: int) -> str:
            names = state.get("move_names") or ()
            return names[slot] if slot < len(names) else str(move_id)

        def move_report(state: dict[str, Any], slot: int, move_id: int, defender: dict[str, Any]) -> dict[str, Any]:
            raw_samples = memory.get((state.get("species"), move_id, defender.get("species")), ())
            samples = list(raw_samples.get("samples", ())) if isinstance(raw_samples, dict) else list(raw_samples)
            metadata = (move_data or {}).get(move_id)
            known_move = metadata is not None or move_id in MOVE_TYPE_IDS
            damage_min, damage_max = cls._damage_bounds(
                move_id, state, defender,
                effectiveness_memory=effectiveness_memory,
                type_chart=type_chart,
                damage_memory=memory,
                move_data=move_data,
            )
            damage = (damage_min + damage_max) / 2
            critical_damage_max = damage_max if move_id in MOVE_FIXED_DAMAGE_FRACTIONS else damage_max * 2
            accuracy_known = metadata is not None
            base_accuracy = int(metadata.accuracy) if metadata is not None else None
            hit_probability = (
                1.0
                if base_accuracy == 0
                else min(1.0, max(0.0, (base_accuracy or 0) / 100 * cls._accuracy_stage_multiplier(state) / cls._evasion_stage_multiplier(defender)))
            )
            expected_damage = damage * hit_probability
            ko_in = int((defender.get("current_hp", 0) + max(damage_max, 1) - 1) // max(damage_max, 1))
            guaranteed_ko_in = int((defender.get("current_hp", 0) + max(damage_min, 1) - 1) // max(damage_min, 1))
            priority_value = int(metadata.priority) if metadata is not None else int(move_id in MOVE_PRIORITY_IDS)
            attacker_speed = cls._effective_speed(state)
            defender_speed = cls._effective_speed(defender)
            speed_order = (
                "first" if attacker_speed > defender_speed
                else "second" if attacker_speed < defender_speed
                else "tie"
            )
            order = "first" if priority_value > 0 else speed_order
            first = order == "first"
            uncertainties = []
            if not accuracy_known:
                uncertainties.append("move_accuracy_unavailable")
            if metadata is not None and metadata.secondary_chance:
                uncertainties.append("secondary_effect_identity_unmodeled")
            if metadata is not None and metadata.category == "status":
                uncertainties.append("status_move_effect_unmodeled")
            if move_id in RESIDUAL_OR_MULTI_TURN_MOVE_IDS:
                uncertainties.append("residual_or_multi_turn_transition_unmodeled")
            attacker_ability = int(state.get("ability", 0) or 0)
            defender_ability = int(defender.get("ability", 0) or 0)
            if attacker_ability not in MECHANICS_MODELED_ABILITIES:
                uncertainties.append(f"attacker_ability_effect_unmodeled:{attacker_ability}")
            if defender_ability not in MECHANICS_MODELED_ABILITIES:
                uncertainties.append(f"defender_ability_effect_unmodeled:{defender_ability}")
            if int(state.get("held_item", 0) or 0):
                uncertainties.append(f"attacker_held_item_effect_unmodeled:{int(state['held_item'])}")
            if int(defender.get("held_item", 0) or 0):
                uncertainties.append(f"defender_held_item_effect_unmodeled:{int(defender['held_item'])}")
            uncertainties = list(dict.fromkeys(uncertainties))
            return {
                "slot": slot,
                "actor": actor_slot,
                "target": target_slot,
                "move_id": move_id,
                "move": move_name(state, slot, move_id),
                "damage_est": round(expected_damage, 2),
                "damage_on_hit": round(damage, 2),
                "damage_range": [round(damage_min, 2), round(damage_max, 2)],
                "critical_damage_max": round(critical_damage_max, 2),
                "accuracy": base_accuracy,
                "hit_probability": round(hit_probability, 6),
                "guaranteed_hit": hit_probability >= 1.0,
                "priority": priority_value,
                "ko_in": ko_in,
                "guaranteed_ko_in": guaranteed_ko_in,
                "order": order,
                "acts_first": first,
                "ko_before_hit": hit_probability >= 1.0 and damage_min >= defender.get("current_hp", 0) > 0 and first,
                "outcome_set": {
                    "miss_probability": round(1 - hit_probability, 6),
                    "normal_hit": [round(damage_min, 2), round(damage_max, 2)],
                    "critical_hit": [round(damage_min * 2, 2), round(critical_damage_max, 2)],
                    "critical_probability_given_hit": 0.0625,
                },
                "mechanics_coverage": {
                    "damage": "rom_formula" if metadata is not None else "static_or_unknown",
                    "accuracy": "rom_and_stages" if accuracy_known else "unknown",
                    "priority": "rom" if metadata is not None else "static_fallback",
                    "secondary_effect": "none" if metadata is None or not metadata.secondary_chance else "chance_known_effect_unmodeled",
                    "complete_for_transition": not uncertainties,
                    "gaps": uncertainties,
                },
                "uncertainties": uncertainties,
                "evidence": (
                    {"kind": "observed_samples", "samples": samples}
                    if samples and not known_move
                    else {
                        "kind": "rom_formula" if metadata is not None else "static_model",
                        **({"legacy_samples_quarantined": len(samples)} if samples else {}),
                    }
                ),
            }

        moves = []
        for slot, move_id in enumerate(player.get("moves", ())):
            pp = (player.get("pp") or (0, 0, 0, 0))[slot]
            if move_id and pp:
                moves.append(move_report(player, slot, move_id, opponent))
        incoming = []
        for slot, move_id in enumerate(opponent.get("moves", ())):
            if move_id:
                incoming.append(move_report(opponent, slot, move_id, player))
        max_opponent_priority = max((item["priority"] for item in incoming), default=0)
        for item in moves:
            if item["priority"] > max_opponent_priority:
                item["order"] = "first"
            elif item["priority"] < max_opponent_priority:
                item["order"] = "second"
            item["acts_first"] = item["order"] == "first"
            item["ko_before_hit"] = bool(
                item["guaranteed_hit"]
                and item["damage_range"][0] >= opponent.get("current_hp", 0) > 0
                and item["acts_first"]
            )
        incoming_max = max((item["damage_range"][1] for item in incoming), default=0.0)
        incoming_critical_max = max((item["critical_damage_max"] for item in incoming), default=0.0)
        alternatives = sorted(moves, key=lambda item: (-item["damage_est"], item["slot"]))
        chosen = next(
            (item for item in moves if item["move_id"] == plan.get("move_id") and item["slot"] == plan.get("slot")),
            None,
        )
        mechanics_gaps = list(dict.fromkeys([
            *(chosen.get("uncertainties", []) if chosen else ["no_chosen_move"]),
            *(gap for item in incoming for gap in item.get("uncertainties", [])),
        ]))
        if chosen and chosen["ko_before_hit"]:
            proof_level = "minimax_visible"
            claim = "visible-state one-turn KO before the modeled reply"
        elif chosen and plan.get("reason") == "safe_two_turn_finish":
            proof_level = "expected_best"
            claim = "estimated two-turn finish while surviving the modeled intervening hit"
        elif chosen and plan.get("reason") == "defensive_status_vs_physical_threat":
            proof_level = "expected_best"
            claim = "only live status line that can reduce the modeled physical KO threat; every switch candidate is also KO'd"
        elif chosen and plan.get("reason") == "last_damage_line":
            proof_level = "heuristic"
            claim = "no legal line guarantees survival; selected the highest observed damage chance"
        else:
            proof_level = "expected_best"
            claim = "highest modeled damage/survival score among legal actions"
        return {
            "state": {
                "player": {"species": player_key, "hp": player.get("current_hp"), "max_hp": player.get("max_hp"), "speed": player_speed, "status": player.get("status", 0)},
                "opponent": {"species": opponent_key, "hp": opponent_hp, "max_hp": opponent.get("max_hp"), "speed": opponent_speed, "status": opponent.get("status", 0)},
            },
            "decision": plan,
            "chosen": chosen,
            "alternatives": alternatives,
            "incoming": {
                "max_damage_est": round(incoming_max, 2),
                "critical_max_damage_est": round(incoming_critical_max, 2),
                "moves": incoming,
            },
            "proof": {
                "level": proof_level,
                "claim": claim,
                "checks": {
                    "legal_move_count": len(moves),
                    "opponent_hp": opponent_hp,
                    "chosen_damage_est": chosen["damage_est"] if chosen else None,
                    "chosen_acts_first": chosen["acts_first"] if chosen else None,
                },
                "mechanics_coverage": chosen.get("mechanics_coverage") if chosen else None,
                "material_uncertainty": mechanics_gaps,
                "mechanics_complete": not mechanics_gaps,
                "caveat": "Hidden AI choices, unmodeled move effects, items, and abilities can change non-exhaustive rankings.",
            },
        }

    def observe(self, screenshot: str | bool = False) -> dict[str, Any]:
        scalar_reads = [
            {"name": "save_block1_ptr", "address": SAVE_BLOCK1_PTR, "width": 32},
            {"name": "save_block2_ptr", "address": SAVE_BLOCK2_PTR, "width": 32},
            {"name": "pc_storage_ptr", "address": PC_STORAGE_PTR, "width": 32},
            {"name": "new_game_cursor", "address": NEW_GAME_CURSOR, "width": 8},
            {"name": "yes_no_cursor", "address": YES_NO_CURSOR, "width": 8},
            {"name": "battle_command_cursor", "address": BATTLE_COMMAND_CURSOR, "width": 8},
            {"name": "battle_command_cursor_1", "address": BATTLE_COMMAND_CURSOR + 1, "width": 8},
            {"name": "battle_command_cursor_2", "address": BATTLE_COMMAND_CURSOR + 2, "width": 8},
            {"name": "battle_command_cursor_3", "address": BATTLE_COMMAND_CURSOR + 3, "width": 8},
            {"name": "battle_move_cursor", "address": BATTLE_MOVE_CURSOR, "width": 8},
            {"name": "battle_move_cursor_1", "address": BATTLE_MOVE_CURSOR + 1, "width": 8},
            {"name": "battle_move_cursor_2", "address": BATTLE_MOVE_CURSOR + 2, "width": 8},
            {"name": "battle_move_cursor_3", "address": BATTLE_MOVE_CURSOR + 3, "width": 8},
            {"name": "battle_target", "address": BATTLE_TARGET, "width": 8},
            {"name": "party_count", "address": PLAYER_PARTY_COUNT, "width": 8},
            {"name": "field_message_box_mode", "address": FIELD_MESSAGE_BOX_MODE, "width": 8},
        ]
        try:
            base = self.gba.observe(
                reads=scalar_reads,
                screenshot=screenshot,
                text=True,
                tasks=True,
            )
        except TypeError:
            try:
                # Keep the decoder testable with minimal fake clients; the
                # live client supports both structured fields above.
                base = self.gba.observe(reads=scalar_reads, screenshot=screenshot, text=True)
            except TypeError:
                base = self.gba.observe(reads=scalar_reads, screenshot=screenshot)
        values = _named_values(base["reads"])

        save = {
            "save_block1_ptr": values["save_block1_ptr"],
            "save_block2_ptr": values["save_block2_ptr"],
            "pc_storage_ptr": values["pc_storage_ptr"],
        }
        if _valid_ewram_pointer(save["save_block1_ptr"]):
            block1 = self.gba.read_range(save["save_block1_ptr"], 8)
            save["block1"] = {
                "x": _s16(block1, 0x00),
                "y": _s16(block1, 0x02),
                "map_group": block1[0x04],
                "map_number": block1[0x05],
                "warp_id": block1[0x06],
            }
        else:
            save["block1"] = None

        player = None
        if _valid_ewram_pointer(save["save_block2_ptr"]):
            block2 = self.gba.read_range(save["save_block2_ptr"], 9)
            player = {
                "name": decode_gen3_text(block2[:8]).rstrip(" "),
                "gender": "female" if block2[8] else "male",
                "gender_id": block2[8],
            }

        party_count = min(values["party_count"], 6)
        party = {
            "count": party_count,
            "address": PLAYER_PARTY,
            "stride": PARTY_STRIDE,
            "encrypted": True,
        }
        if party_count:
            party_raw = self.gba.read_range(PLAYER_PARTY, party_count * PARTY_STRIDE)
            party["raw"] = party_raw
            party["mons"] = decode_party_mons(party_raw, slots=party_count)

        battle_raw = self.gba.read_range(BATTLE_MONS, BATTLE_MON_STRIDE * 4)
        battle_mons = decode_battle_mons(battle_raw)
        battle_mode = values["field_message_box_mode"]
        battle_format = self._battle_format_for_field_mode(battle_mode)
        opponent = next((mon for mon in battle_mons if mon["slot"] == 1), None)
        battle_active = any(mon["present"] for mon in battle_mons)
        # gBattleMons is not cleared immediately after a fight.  A zero-HP
        # opponent outside the observed battle message modes is the stable
        # post-KO signature; without this guard, lab/overworld dialogue would
        # be misclassified as an active battle.
        if (
            opponent
            and opponent["present"]
            and opponent["state"]["current_hp"] == 0
            and battle_mode not in BATTLE_FIELD_MESSAGE_MODES
        ):
            battle_active = False
        # A faint/escape can leave non-zero gBattleMons behind while the
        # engine has already restored the overworld (and may have warped the
        # player home).  All live battle UI states in this ROM use a non-zero
        # field message mode, so a clean field mode is a stronger signal than
        # stale battler structs here.
        if battle_mode == 0:
            battle_active = False
        text = decode_text_observation(base.get("text"))
        tasks = base.get("tasks")
        task_entries = (tasks or {}).get("tasks", []) if isinstance(tasks, dict) else []
        field_bag_open = any(self._is_field_bag_task(task) for task in task_entries)
        field_start_menu_open = any(
            task.get("active") and task.get("function_address") == 0x080BD7B9
            for task in task_entries
        )
        if text is not None:
            text["battle_printers"] = self._battle_printer_contexts(text.get("printers", []))
            # ``pages`` intentionally retains the last few decoded printers
            # for battle/text forensics.  A page's old window_id is not proof
            # that a message box is still on screen: after a Mart interaction
            # the renderer can be blank while the history still contains a
            # window-5 page.  Only live printers (or the authoritative field
            # message mode) may keep the game in dialogue mode.
            text["visible"] = (
                values["field_message_box_mode"] != 0
                or text["active"]
                or any(
                    # Run & Bun's field/battle message window is 5.  The
                    # other live printers (notably window 1) are renderer
                    # bookkeeping and remain active in a blank overworld.
                    printer.get("active") and printer.get("window_id", 0) == 5
                    for printer in text.get("printers", [])
                )
            )
            text["last_page"] = text.get("current")
            if not text["visible"] and not text["active"]:
                text["current"] = None
            command_prompt = self._battle_command_prompt(text.get("battle_printers", []))
            command_subject = self._battle_command_subject(
                text.get("battle_printers", [])
            )
            battle_kind = self._battle_kind(text.get("battle_printers", []))
            party_switch_prompt = self._battle_party_switch_prompt(text.get("battle_printers", []))
            move_prompt = self._battle_move_prompt(text.get("battle_printers", []))
            move_prompt_details = self._battle_move_prompt_details(
                text.get("battle_printers", [])
            )
        else:
            command_prompt = False
            command_subject = None
            battle_kind = None
            party_switch_prompt = False
            move_prompt = False
            move_prompt_details = None
        # After a send-out this hack can leave the battle message printer
        # empty while the command selector is already live.  A valid battler
        # pair plus a battle field mode and no active text is the RAM-only
        # command-menu signature; without it, the controller presses A into
        # the selector while waiting for a printer string that never arrives.
        if (
            battle_mode in BATTLE_FIELD_MESSAGE_MODES
            and battle_format == "single"
            and not party_switch_prompt
            and not command_prompt
            and not move_prompt
            and text is not None
            and not text.get("active")
            and not text.get("battle_printers")
            and any(mon.get("present") and mon["state"].get("current_hp", 0) > 0 for mon in battle_mons[:2])
        ):
            command_prompt = True
        # gBattleMons remains populated after battles and during ordinary NPC
        # dialogue. Require a battle-specific field mode or the RAM printer's
        # exact command prompt; this keeps stale May/Mudkip data from blocking
        # dialogue advancement while still recognizing the ROM's field-mode-0
        # command menu without a screenshot.
        if battle_mode not in BATTLE_FIELD_MESSAGE_MODES and not command_prompt and not party_switch_prompt:
            battle_active = False
        elif command_prompt or party_switch_prompt:
            battle_active = True
        if text and text["visible"]:
            mode = "dialogue"
        elif values["field_message_box_mode"] != 0:
            mode = "dialogue"
        elif save["block1"] is not None:
            mode = "overworld"
        else:
            mode = "unknown"
        battle_metadata_error = None
        if battle_active:
            try:
                rom = self.rom_data()
                for mon in battle_mons:
                    if mon.get("present"):
                        move_ids = mon["state"]["moves"]
                        mon["state"]["move_names"] = [
                            rom.move_name(move_id) for move_id in move_ids if move_id
                        ]
            except Exception as error:
                # The live RAM observation remains useful if a different ROM
                # revision is loaded; callers can see the failed profile check.
                battle_metadata_error = f"{type(error).__name__}: {error}"
        objects: list[dict[str, Any]] = []
        objects_error: str | None = None
        try:
            from games.run_and_bun.objects import read_live_objects

            objects = [obj.as_dict() for obj in read_live_objects(self.gba)]
        except Exception as error:
            # Minimal fake clients and non-overworld ROM states may not expose
            # the object table. Keep the rest of the semantic observation
            # useful while surfacing the missing capability to the caller.
            objects_error = f"{type(error).__name__}: {error}"
        command_cursors = [
            values["battle_command_cursor"],
            values["battle_command_cursor_1"],
            values["battle_command_cursor_2"],
            values["battle_command_cursor_3"],
        ]
        command_battler = next(
            (
                mon["slot"]
                for mon in battle_mons
                if mon.get("present")
                and command_subject
                and (mon["state"].get("nickname") or "").strip().casefold()
                == command_subject.casefold()
            ),
            None,
        )
        active_command_cursor = (
            command_cursors[command_battler]
            if command_battler in range(4)
            else values["battle_command_cursor"]
        )
        result = {
            "frame": base["frame"],
            "title": base["title"],
            "code": base["code"],
            "screenshot": base.get("screenshot"),
            "ui": {
                "new_game_option": values["new_game_cursor"],
                "yes_no": values["yes_no_cursor"],
                "battle_command": values["battle_command_cursor"],
                "battle_command_cursors": command_cursors,
                "battle_move": values["battle_move_cursor"],
                "battle_move_cursors": [
                    values["battle_move_cursor"],
                    values["battle_move_cursor_1"],
                    values["battle_move_cursor_2"],
                    values["battle_move_cursor_3"],
                ],
                "battle_target": values["battle_target"],
                "field_message_box_mode": values["field_message_box_mode"],
                "field_message_box_mode_name": FIELD_MESSAGE_MODE_NAMES.get(
                    values["field_message_box_mode"],
                    f"unknown_{values['field_message_box_mode']}",
                ),
                "field_bag_open": field_bag_open,
                "field_start_menu_open": field_start_menu_open,
            },
            "save": save,
            "map": (
                {
                    "group": save["block1"]["map_group"],
                    "number": save["block1"]["map_number"],
                    "x": save["block1"]["x"],
                    "y": save["block1"]["y"],
                    "warp_id": save["block1"]["warp_id"],
                }
                if save["block1"] is not None
                else None
            ),
            "player": player,
            "mode": mode,
            "objects": objects,
            "text": text,
            "party": party,
            "tasks": tasks,
            "battle": {
                "active": battle_active,
                "format": battle_format,
                "party_switch_required": party_switch_prompt,
                "kind": battle_kind,
                "activity_detection": "battle_mons_species_and_battle_field_mode_or_command_prompt",
                "metadata": {
                    "source": "verified_rom",
                    "type_chart": "Q4.12@0x083ADEE0",
                    "move_names": "13-byte slots@0x083A4493",
                } if battle_active and battle_metadata_error is None else None,
                "menu": {
                    "state": self._battle_menu_state(
                        party_switch_prompt=party_switch_prompt,
                        move_prompt=move_prompt,
                        command_prompt=command_prompt,
                        battle_active=battle_active,
                        battle_format=battle_format,
                        battle_target=values["battle_target"],
                    ),
                    "command": active_command_cursor if battle_active else None,
                    "command_battler": command_battler if battle_active else None,
                    "command_subject": command_subject if battle_active else None,
                    "move": values["battle_move_cursor"] if battle_active else None,
                    "target": (
                        values["battle_target"]
                        if battle_active and values["battle_target"] in range(4)
                        else None
                    ),
                    "selected_move": move_prompt_details if battle_active else None,
                    "command_name": (
                        ("fight", "bag", "pokemon", "run")[active_command_cursor]
                        if battle_active and active_command_cursor < 4
                        else None
                    ),
                },
                "mons": battle_mons,
            },
        }
        if objects_error is not None:
            result["objects_error"] = objects_error
        if battle_metadata_error is not None:
            result["battle_metadata_error"] = battle_metadata_error
        if screenshot and inspect_png is not None:
            try:
                screenshot_path = base.get("screenshot") or (
                    screenshot if isinstance(screenshot, str) else None
                )
                if screenshot_path:
                    result["visual"] = asdict(inspect_png(screenshot_path))
            except Exception:
                # Framebuffer classification is diagnostic; it must not make
                # a RAM-only observation fail.
                result["visual"] = None
        return result

    def advance_dialogue(self, max_pages: int = 32, timeout: float = 10.0) -> list[str]:
        """Advance visible dialogue pages using RAM state as the stop signal."""
        pages: list[str] = []
        pending_signature = None
        deadline = time.monotonic() + timeout
        while len(pages) < max_pages:
            state = self.observe()
            if state["battle"]["active"]:
                return pages
            if state["mode"] != "dialogue":
                return pages
            field_mode = state["ui"].get("field_message_box_mode")
            if field_mode in {10, 16, 42, 50}:
                # These modes are a nickname editor or battle-specific text
                # window.  They need their own controller and must not receive
                # blind dialogue A presses.
                return pages
            if field_mode == 3:
                if time.monotonic() >= deadline:
                    raise TimeoutError("auto-scroll dialogue did not close")
                time.sleep(0.01)
                continue
            text = state.get("text") or {}
            current = text.get("current")
            if not current:
                # A text buffer can be between printers for a few frames even
                # though the message box is still open. Keep sampling the RAM
                # printer state instead of returning an empty result and
                # forcing the caller back to screenshots.
                if (
                    text.get("visible")
                    or text.get("active")
                    or field_mode not in (0, None)
                ):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("dialogue printer did not expose a current page")
                    time.sleep(0.01)
                    continue
                return pages
            signature = (
                current.get("start"),
                current.get("end"),
                current.get("cursor"),
                current.get("text"),
            )
            # A press can leave the old page visible for several frames. Do
            # not send another A until the printer has moved to a new page or
            # the message box has closed.
            if pending_signature is not None:
                if signature == pending_signature:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("dialogue page did not advance")
                    time.sleep(0.01)
                    continue
                pending_signature = None
                deadline = time.monotonic() + timeout

            ready = (
                (
                    state["ui"].get("field_message_box_mode") == 2
                    and not text.get("active")
                )
                or current.get("state") in (1, 2, 3, 5, 6)
                or (
                    current.get("state") == 0
                    and not text.get("active")
                    and text.get("visible")
                )
            )
            if not ready:
                if time.monotonic() >= deadline:
                    raise TimeoutError("dialogue printer did not reach an input prompt")
                time.sleep(0.01)
                continue

            pages.append(current["text"])
            pending_signature = signature
            self.gba.press("A")
            deadline = time.monotonic() + timeout
        return pages

    @staticmethod
    def _grid_cursor_keys(current: int, target: int) -> list[str]:
        """Return the shortest deterministic path on a two-column 2×2 menu."""
        if current not in range(4) or target not in range(4):
            raise ValueError("grid cursor slots must be 0..3")
        row, col = divmod(current, 2)
        target_row, target_col = divmod(target, 2)
        keys: list[str] = []
        if row != target_row:
            keys.append("DOWN" if target_row > row else "UP")
        if col != target_col:
            keys.append("RIGHT" if target_col > col else "LEFT")
        return keys

    def _battle_menu_tap(
        self,
        key: str,
        *,
        hold_frames: int = 1,
        settle_frames: int = 12,
    ) -> dict[str, Any]:
        """Send one frame-synchronized menu input with an atomic settle gap."""
        if hold_frames < 1 or settle_frames < 1:
            raise ValueError("battle menu hold/settle frames must be positive")
        return self.gba.sequence(
            [
                {"keys": [key], "frames": hold_frames},
                {"keys": [], "frames": settle_frames},
            ]
        )

    @staticmethod
    def _present_battle_mon(observation: dict[str, Any], slot: int) -> dict[str, Any]:
        mon = next(
            (
                item
                for item in observation["battle"]["mons"]
                if item.get("slot") == slot and item.get("present")
            ),
            None,
        )
        if mon is None:
            raise RuntimeError(f"battle battler slot {slot} is not present")
        return mon["state"]

    def select_double_switch(
        self,
        battler_slot: int,
        party_slot: int,
    ) -> dict[str, Any]:
        """Queue one voluntary switch from a double-battle command prompt."""
        if battler_slot not in (0, 2):
            raise ValueError("allied double-battle battler slot must be 0 or 2")
        observation = self.observe()
        battle = observation["battle"]
        if not battle["active"] or battle.get("format") != "double":
            raise RuntimeError("double_battle_not_active")
        if battle["menu"]["state"] != "command_menu":
            raise RuntimeError(
                f"double switch requires command_menu, got {battle['menu']['state']}"
            )
        acting = self._present_battle_mon(observation, battler_slot)
        contexts = (observation.get("text") or {}).get("battle_printers", [])
        subject = self._battle_command_subject(contexts)
        nickname = (acting.get("nickname") or "").strip()
        if subject and nickname and subject.casefold() != nickname.casefold():
            raise RuntimeError(
                f"command prompt belongs to {subject!r}, not battler {nickname!r}"
            )

        party = [
            {**mon["state"], "slot": mon["slot"]}
            for mon in observation.get("party", {}).get("mons", [])
            if mon.get("present")
        ]
        target = next((mon for mon in party if mon.get("slot") == party_slot), None)
        if target is None:
            raise IndexError(f"party slot {party_slot} is not present")
        if target.get("current_hp", 0) <= 0:
            raise ValueError(f"party slot {party_slot} is fainted")
        def same_mon(left: dict[str, Any], right: dict[str, Any]) -> bool:
            if left.get("personality") is not None and right.get("personality") is not None:
                return left["personality"] == right["personality"]
            return left.get("species") == right.get("species")

        active = [self._present_battle_mon(observation, slot) for slot in (0, 2)]
        if any(same_mon(target, mon) for mon in active):
            raise ValueError(f"party slot {party_slot} is already active")
        acting_party_slots = [
            mon["slot"] for mon in party if same_mon(mon, acting)
        ]
        if len(acting_party_slots) != 1:
            raise RuntimeError(
                f"cannot uniquely map active species {acting['species']} to party"
            )
        ui_order = [
            mon for mon in party if mon["slot"] != acting_party_slots[0]
        ]
        target_index = next(
            (index for index, mon in enumerate(ui_order) if mon["slot"] == party_slot),
            None,
        )
        if target_index is None:
            raise RuntimeError("switch target missing from battle party UI order")

        command_cursors = observation["ui"].get("battle_command_cursors", [])
        command_cursor = (
            command_cursors[battler_slot]
            if len(command_cursors) > battler_slot
            else None
        )
        if command_cursor not in range(4):
            raise RuntimeError(f"invalid battler command cursor {command_cursor}")
        for key in self._grid_cursor_keys(command_cursor, 2):
            self._battle_menu_tap(key, hold_frames=3)
        # Party slide-in, target selection, and Shift submenu each have a
        # verified 120-frame acceptance boundary in this ROM/UI.
        self._battle_menu_tap("A", hold_frames=3, settle_frames=120)
        self._battle_menu_tap("RIGHT", hold_frames=3, settle_frames=120)
        for _ in range(target_index):
            self._battle_menu_tap("DOWN", hold_frames=3, settle_frames=120)
        self._battle_menu_tap("A", hold_frames=3, settle_frames=120)
        self._battle_menu_tap("A", hold_frames=3, settle_frames=120)

        after = self.observe()
        partner_alive = any(
            item.get("slot") == (2 if battler_slot == 0 else 0)
            and item.get("present")
            and item["state"].get("current_hp", 0) > 0
            for item in after["battle"]["mons"]
        )
        expected_state = (
            "command_menu"
            if battler_slot == 0 and partner_alive
            else "battle_text"
        )
        actual_state = after["battle"]["menu"]["state"]
        if actual_state != expected_state:
            raise RuntimeError(
                f"switch queue ended in {actual_state}, expected {expected_state}"
            )
        return {
            "battler_slot": battler_slot,
            "from_species": acting["species"],
            "party_slot": party_slot,
            "to_species": target["species"],
            "to_hp": target["current_hp"],
            "ack": actual_state,
            "next_command_battler": after["battle"]["menu"].get(
                "command_battler"
            ),
        }

    def select_double_move(
        self,
        battler_slot: int,
        move_slot: int,
        *,
        expected_type: str,
        target_slot: int | None = None,
        visible_cursor: int | None = None,
        max_attempts: int = 2,
    ) -> dict[str, Any]:
        """Queue one allied action in Run & Bun's custom double-battle UI.

        ``gMoveSelectionCursor`` is a four-byte per-battler array. This method
        uses the acting battler's own byte and independently acknowledges the
        selected move from the RAM text printer's Type field. Explicit target
        selection is verified through ``gBattlerTarget``.
        """
        if battler_slot not in (0, 2):
            raise ValueError("allied double-battle battler slot must be 0 or 2")
        if move_slot not in range(4):
            raise ValueError("move slot must be 0..3")
        if target_slot is not None and target_slot not in range(4):
            raise ValueError("target battler slot must be 0..3")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")

        observation = self.observe()
        battle = observation["battle"]
        if not battle["active"] or battle.get("format") != "double":
            raise RuntimeError("double_battle_not_active")
        battler = self._present_battle_mon(observation, battler_slot)
        move_id = battler["moves"][move_slot]
        before_pp = battler["pp"][move_slot]
        if not move_id or before_pp <= 0:
            raise ValueError(f"battler {battler_slot} move slot {move_slot} is not legal")

        menu_state = battle["menu"]["state"]
        if menu_state == "command_menu":
            contexts = (observation.get("text") or {}).get("battle_printers", [])
            subject = self._battle_command_subject(contexts)
            nickname = (battler.get("nickname") or "").strip()
            if subject and nickname and subject.casefold() != nickname.casefold():
                raise RuntimeError(
                    f"command prompt belongs to {subject!r}, not battler {nickname!r}"
                )
            command_cursors = observation["ui"].get(
                "battle_command_cursors", []
            )
            command_cursor = (
                command_cursors[battler_slot]
                if len(command_cursors) > battler_slot
                else None
            )
            if command_cursor not in range(4):
                raise RuntimeError(f"invalid battle command cursor {command_cursor}")
            for key in self._grid_cursor_keys(command_cursor, 0):
                self._battle_menu_tap(key, hold_frames=3)
            for _ in range(max_attempts):
                self._battle_menu_tap("A")
                observation = self.observe()
                menu_state = observation["battle"]["menu"]["state"]
                if menu_state == "move_menu":
                    break
                if menu_state != "command_menu":
                    raise RuntimeError(f"Fight opened unexpected state {menu_state}")
            else:
                raise RuntimeError("Fight confirmation was not acknowledged")
            battle = observation["battle"]
        elif menu_state != "move_menu":
            raise RuntimeError(f"expected command/move menu, got {menu_state}")

        if visible_cursor is None:
            selected_cursors = observation["ui"].get(
                "battle_move_cursors", []
            )
            visible_cursor = (
                selected_cursors[battler_slot]
                if len(selected_cursors) > battler_slot
                else None
            )
        if visible_cursor not in range(4):
            raise RuntimeError(f"invalid visible move cursor {visible_cursor}")
        for key in self._grid_cursor_keys(visible_cursor, move_slot):
            self._battle_menu_tap(key, hold_frames=3)

        selected = self.observe()
        selected_menu = selected["battle"]["menu"]
        if selected_menu["state"] != "move_menu":
            raise RuntimeError(
                f"move cursor input left selector in {selected_menu['state']}"
            )
        details = selected_menu.get("selected_move") or {}
        selected_type = details.get("type")
        if not selected_type or selected_type.casefold() != expected_type.casefold():
            raise RuntimeError(
                f"move cursor verification failed: expected Type/{expected_type}, "
                f"observed {details.get('text')!r}"
            )

        selection_attempts = 0
        while selection_attempts < max_attempts:
            selection_attempts += 1
            self._battle_menu_tap("A", settle_frames=2)
            after = self.observe()
            if (
                after["battle"]["menu"]["state"] == "move_menu"
                and after["battle"]["menu"].get("target") is None
            ):
                continue
            break
        else:
            raise RuntimeError("move selection was not acknowledged")

        explicit_target = after["battle"]["menu"]["state"] == "target_menu"
        if explicit_target:
            current_target = after["battle"]["menu"].get("target")
            desired_target = current_target if target_slot is None else target_slot
            if desired_target != current_target:
                if {desired_target, current_target} != {1, 3}:
                    raise RuntimeError(
                        f"cannot move explicit target {current_target} to {desired_target}"
                    )
                self._battle_menu_tap("LEFT", settle_frames=2)
                after = self.observe()
                if after["battle"]["menu"].get("target") != desired_target:
                    raise RuntimeError(
                        f"target verification failed: wanted {desired_target}, "
                        f"got {after['battle']['menu'].get('target')}"
                    )
            for _ in range(max_attempts):
                self._battle_menu_tap("A", settle_frames=2)
                after = self.observe()
                if after["battle"]["menu"]["state"] != "target_menu":
                    break
            else:
                raise RuntimeError("target confirmation was not acknowledged")
        elif target_slot is not None:
            automatic_target = after["battle"]["menu"].get("target")
            if automatic_target != target_slot:
                raise RuntimeError(
                    f"automatic target mismatch: wanted {target_slot}, got {automatic_target}"
                )

        final_menu = after["battle"]["menu"]["state"]
        partner_alive = any(
            item.get("slot") == 2
            and item.get("present")
            and item["state"].get("current_hp", 0) > 0
            for item in after["battle"]["mons"]
        )
        if battler_slot == 0 and partner_alive and final_menu != "command_menu":
            raise RuntimeError(f"first allied action ended in {final_menu}, not command_menu")
        if (
            battler_slot == 0
            and not partner_alive
            and final_menu in {"command_menu", "move_menu", "target_menu"}
        ):
            raise RuntimeError(
                f"sole allied action was not queued after partner faint: {final_menu}"
            )
        if battler_slot == 2 and final_menu in {"command_menu", "move_menu", "target_menu"}:
            raise RuntimeError(f"second allied action was not queued: {final_menu}")

        return {
            "battler_slot": battler_slot,
            "species": battler["species"],
            "move_slot": move_slot,
            "move_id": move_id,
            "move_name": self.rom_data().move_name(move_id),
            "pp_before": before_pp,
            "selected_type": selected_type,
            "target": after["battle"]["menu"].get("target"),
            "explicit_target": explicit_target,
            "selection_attempts": selection_attempts,
            "ack": final_menu,
        }

    def advance_battle_until_menu(
        self,
        *,
        sample_frames: int = 24,
        max_frames: int = 900,
        visual_fallback: bool = False,
        after_action: bool = False,
        pre_action_signature: tuple[Any, ...] | None = None,
    ) -> dict[str, Any]:
        """Advance battle text from RAM until a command menu or battle end.

        Battle text is printer-driven in this ROM.  Wait while the printer is
        still typing, send A only on a completed battle text page, and stop as
        soon as the opponent HP reaches zero.  This keeps battle control off
        the screenshot path and prevents a KO transition from receiving an
        accidental second move selection.
        """
        if sample_frames < 1 or max_frames < 1:
            raise ValueError("sample_frames and max_frames must be positive")
        elapsed = 0
        presses = 0
        visual_path = "/tmp/runbun-battle-ui.png"
        feedback_parts: list[str] = []
        # After a move or a rejected menu action the old transient printer can
        # retain the command marker for a few frames.  A caller that just made
        # an action must observe one non-identical printer state before the
        # next command prompt is accepted; otherwise the controller can queue
        # a second move into the previous turn.
        initial_prompt_marker = None
        prompt_transition_seen = not after_action
        initial_battle_signature = pre_action_signature
        action_transition_seen = not after_action
        pending_text_signature = None
        last_battle_signature = None
        while elapsed <= max_frames:
            state = self.observe()
            battle = state["battle"]
            visual = None
            if visual_fallback and inspect_png is not None:
                try:
                    self.gba.screenshot(visual_path)
                    visual = inspect_png(visual_path)
                except Exception:
                    # A headless/minimal client can still use all RAM signals.
                    visual = None
            opponent = next(
                (item for item in battle["mons"] if item["slot"] == 1),
                None,
            )
            battle_signature = tuple(
                (item["slot"], item["state"].get("species"), item["state"].get("current_hp"), item["state"].get("pp"))
                for item in battle["mons"]
                if item.get("present")
            )
            if last_battle_signature is not None and battle_signature != last_battle_signature:
                # Locked moves can render the same text at the same printer
                # address on consecutive turns. HP/PP progress proves the new
                # page is not a stale duplicate and may be acknowledged.
                pending_text_signature = None
            last_battle_signature = battle_signature
            if after_action and initial_battle_signature is None:
                initial_battle_signature = battle_signature
            if after_action and battle_signature != initial_battle_signature:
                # The prompt printer is reused at the same address after a
                # turn, so its pointer/text tuple can be identical even
                # though the turn already resolved.  HP/PP/species RAM is the
                # authoritative transition signal.
                action_transition_seen = True
            text = state.get("text") or {}
            text_ready = self._battle_text_ready(text)
            current_text = text.get("current") or {}
            text_signature = (
                current_text.get("start"), current_text.get("end"),
                current_text.get("cursor"), current_text.get("text"),
                tuple(
                    (printer.get("address"), printer.get("current_char"), printer.get("state"), printer.get("active"))
                    for printer in text.get("printers", [])
                ),
                tuple(
                    (printer.get("printer_address"), printer.get("current_char"), printer.get("text"))
                    for printer in text.get("battle_printers", [])
                ),
            )
            battle_contexts = text.get("battle_printers", [])
            for context in battle_contexts:
                value = (context.get("text") or "").strip()
                if value and value not in feedback_parts[-4:]:
                    feedback_parts.append(value)
            command_prompt = self._battle_command_prompt(battle_contexts)
            move_prompt = self._battle_move_prompt(battle_contexts)
            party_switch_prompt = self._battle_party_switch_prompt(battle_contexts)
            prompt_marker = tuple(
                (context.get("printer_address"), context.get("current_char"), context.get("text"))
                for context in battle_contexts
            )
            if after_action and initial_prompt_marker is None:
                initial_prompt_marker = prompt_marker
            if after_action and prompt_marker != initial_prompt_marker:
                prompt_transition_seen = True
            field_mode = state["ui"].get("field_message_box_mode", 0)
            ram_menu_state = battle.get("menu", {}).get("state")
            player_fainted = self._active_player_fainted(battle)

            def confirmed_boundary(name: str) -> dict[str, Any] | None:
                """Require the menu and battlers to remain stable before input."""
                nonlocal elapsed
                expected_signature = battle_signature
                # Run & Bun can render a command prompt before delayed
                # residual HP changes (notably Leech Seed) are committed.
                # Five frame samples keep that transient prompt from owning
                # the next action while adding no host-side idle time.
                for _ in range(5):
                    self.gba.wait_frames(sample_frames)
                    elapsed += sample_frames
                    settled = self.observe()
                    settled_signature = tuple(
                        (
                            item["slot"], item["state"].get("species"),
                            item["state"].get("current_hp"), item["state"].get("pp"),
                        )
                        for item in settled["battle"]["mons"]
                        if item.get("present")
                    )
                    if (
                        settled_signature != expected_signature
                        or settled["battle"].get("menu", {}).get("state") != name
                        or (name in {"command_menu", "move_menu"} and self._active_player_fainted(settled["battle"]))
                    ):
                        return None
                return {
                    "state": name,
                    "frames": elapsed,
                    "presses": presses,
                    "feedback": "\n".join(feedback_parts),
                }
            if ram_menu_state == "target_menu":
                # Target choice is a gameplay decision, never battle text to
                # auto-advance. Hand it back to the tactical controller.
                return {
                    "state": "target_menu",
                    "frames": elapsed,
                    "presses": presses,
                    "feedback": "\n".join(feedback_parts),
                }
            if ram_menu_state == "move_menu" and not player_fainted and (not after_action or action_transition_seen):
                if confirmed := confirmed_boundary("move_menu"):
                    return confirmed
                continue
            if ram_menu_state == "party_switch" and (not after_action or action_transition_seen):
                if confirmed := confirmed_boundary("party_switch"):
                    return confirmed
                continue
            if ram_menu_state == "command_menu" and not player_fainted and (not after_action or action_transition_seen):
                if confirmed := confirmed_boundary("command_menu"):
                    return confirmed
                continue
            # A stale Type/PP printer can survive the transition back to the
            # command selector.  When both markers are present, the live
            # ``What will ... do?`` command prompt wins; otherwise open_fight
            # can skip Fight and spend a turn pressing into an old move menu.
            if (
                move_prompt
                and ram_menu_state != "target_menu"
                and not command_prompt
                and not player_fainted
                and (not after_action or action_transition_seen)
            ):
                if confirmed := confirmed_boundary("move_menu"):
                    return confirmed
                continue
            if party_switch_prompt and (not after_action or action_transition_seen):
                if confirmed := confirmed_boundary("party_switch"):
                    return confirmed
                continue
            battle_hud = bool(visual and visual.battle_hud)
            if (
                opponent
                and opponent["present"]
                and opponent["state"]["current_hp"] == 0
                and (battle["active"] or battle_hud or field_mode in BATTLE_KO_FIELD_MESSAGE_MODES)
            ):
                # A trainer can immediately send out another Pokémon.  Keep
                # advancing the RAM-backed battle printer until the new
                # command prompt appears instead of treating the first KO as
                # the end of the whole battle.
                if field_mode != 0:
                    if ram_menu_state in {"command_menu", "move_menu", "party_switch", "target_menu"}:
                        self.gba.wait_frames(sample_frames)
                    elif (text_ready or not text.get("active")) and text_signature != pending_text_signature:
                        self.gba.press("A", frames=1)
                        presses += 1
                        pending_text_signature = text_signature
                        self.gba.wait_frames(sample_frames)
                    else:
                        self.gba.wait_frames(sample_frames)
                    elapsed += sample_frames
                    continue
                if not battle["active"] and not battle_hud:
                    return {
                        "state": "battle_end",
                        "frames": elapsed,
                        "presses": presses,
                        "feedback": "\n".join(feedback_parts),
                    }
            # The command menu can be rendered with field mode 0, which is
            # indistinguishable from stale post-battle structs in RAM alone.
            # A battle HUD is the only case where the narrow visual fallback
            # can promote that ambiguous state back to an active battle.
            battle_active = battle["active"] or battle_hud or command_prompt or party_switch_prompt
            if not battle_active and self._inactive_battle_is_terminal(state):
                return {
                    "state": "not_in_battle",
                    "frames": elapsed,
                    "presses": presses,
                    "feedback": "\n".join(feedback_parts),
                }
            if not player_fainted and (command_prompt or (visual and visual.battle_command_menu)) and (
                prompt_transition_seen or action_transition_seen
            ):
                if confirmed := confirmed_boundary("command_menu"):
                    return confirmed
                continue
            # Run & Bun leaves the battle text mode latched at 50 while both
            # battle messages and the command selector are on screen.  The
            # text buffer is a different runtime buffer here, but the live
            # printer layout is stable: the command selector is ready only
            # when one printer remains active at its cleared (0, 1) cursor.
            # This avoids treating "Go! ..." or a fainting message as a move
            # menu and sending an accidental input too early.
            if not player_fainted and field_mode == 50 and self._battle_command_prompt(battle_contexts) and (
                prompt_transition_seen or action_transition_seen
            ):
                if confirmed := confirmed_boundary("command_menu"):
                    return confirmed
                continue
            if field_mode != 0:
                if ram_menu_state in {"command_menu", "move_menu", "party_switch", "target_menu"}:
                    self.gba.wait_frames(sample_frames)
                elif (text_ready or not text.get("active")) and text_signature != pending_text_signature:
                    self.gba.press("A", frames=1)
                    presses += 1
                    pending_text_signature = text_signature
                    self.gba.wait_frames(sample_frames)
                else:
                    self.gba.wait_frames(sample_frames)
            elif text.get("active"):
                self.gba.wait_frames(sample_frames)
            elif player_fainted:
                self.gba.wait_frames(sample_frames)
            else:
                if confirmed := confirmed_boundary("command_menu"):
                    return confirmed
                continue
            elapsed += sample_frames
        return {
            "state": "timeout",
            "frames": elapsed,
            "presses": presses,
            "feedback": "\n".join(feedback_parts),
        }

    @staticmethod
    def _inactive_battle_is_terminal(state: dict[str, Any]) -> bool:
        """A battle-active flicker during dialogue is not a battle end."""
        return state.get("mode") == "overworld"

    @staticmethod
    def _battle_text_ready(text: dict[str, Any]) -> bool:
        current = text.get("current") or {}
        ready_states = (1, 2, 3, 5, 6)
        return current.get("state") in ready_states or any(
            printer.get("active") and printer.get("state") in ready_states
            for printer in text.get("printers", [])
        )

    def resolve_battle(
        self,
        *,
        move_slot: int | None = None,
        max_turns: int = 24,
        sample_frames: int = 24,
        allow_switch: bool = True,
        low_hp_fraction: float = 0.25,
        item_index: int | None = None,
        item_hp_fraction: float = 0.35,
    ) -> dict[str, Any]:
        """Finish the current battle with RAM-only menu and party feedback.

        The helper handles both wild battles and trainer send-outs.  It uses
        the compatibility state decoder only for cursor-safe Fight/move input;
        all stop conditions remain the local adapter's RAM-backed battle and
        text-printer observations.
        """
        if max_turns < 1:
            raise ValueError("max_turns must be >= 1")
        from games.run_and_bun.state import RunBun

        state = RunBun(self.gba)
        turns = 0
        effectiveness_memory: dict[tuple[int, int], float] = {}
        type_chart = self.rom_data().type_chart()
        while turns <= max_turns:
            observation = self.observe()
            if not observation["battle"]["active"]:
                return {"state": "overworld", "turns": turns, "observation": observation}
            status = self.advance_battle_until_menu(
                sample_frames=sample_frames,
                max_frames=1800,
                visual_fallback=False,
            )
            if status["state"] == "battle_end":
                drained = self.finish_battle_after_ko(max_frames=1800)
                if drained["state"] == "overworld":
                    return {"state": "overworld", "turns": turns, "drain": drained}
                continue
            if status["state"] not in {"command_menu", "move_menu"}:
                return {"state": status["state"], "turns": turns, "status": status}

            observation = self.observe()
            active = next(
                (mon["state"] for mon in observation["battle"]["mons"] if mon.get("slot") == 0 and mon.get("present")),
                None,
            )
            if active is None:
                return {"state": "battle_party_transition", "turns": turns}

            # Items are opt-in because some challenge battles explicitly reject
            # Bag actions.  A rejected item is treated as a normal lost menu
            # action; the printer is drained before the next decision.
            if (
                item_index is not None
                and active["max_hp"]
                and active["current_hp"] / active["max_hp"] <= item_hp_fraction
            ):
                plan = {"action": "item", "item_index": item_index}
                state.use_battle_item(item_index)
            else:
                plan = self.choose_battle_action(
                    observation,
                    effectiveness_memory=effectiveness_memory,
                    type_chart=type_chart,
                    damage_memory=self._damage_memory,
                    move_data=self.battle_move_data(observation),
                    low_hp_fraction=low_hp_fraction,
                    allow_switch=allow_switch,
                )
                if move_slot is not None and plan.get("action") == "move":
                    plan["slot"] = move_slot
                if plan.get("action") == "switch":
                    if plan.get("species") is not None:
                        state.switch_pokemon(species_id=plan["species"])
                    else:
                        state.switch_pokemon(plan["slot"])
                elif plan.get("action") == "move":
                    state.open_fight_menu()
                    state.choose_move(plan["slot"])
                else:
                    raise RuntimeError(f"battle strategy produced no action: {plan}")
            # Do not accept the old prompt marker left by the selected action.
            resolved = self.advance_battle_until_menu(
                sample_frames=sample_frames,
                max_frames=1800,
                visual_fallback=False,
                after_action=True,
            )
            if resolved["state"] not in {"command_menu", "battle_end", "not_in_battle"}:
                return {"state": resolved["state"], "turns": turns, "status": resolved}
            if resolved["state"] == "not_in_battle" and not self.observe()["battle"]["active"]:
                return {"state": "overworld", "turns": turns + 1, "status": resolved}
            feedback = resolved.get("feedback", "")
            post_observation = self.observe()
            post_player = next(
                (mon["state"] for mon in post_observation["battle"]["mons"] if mon.get("slot") == 0 and mon.get("present")),
                None,
            )
            post_opponent = next(
                (mon["state"] for mon in post_observation["battle"]["mons"] if mon.get("slot") == 1 and mon.get("present")),
                None,
            )
            pre_opponent = next(
                (mon["state"] for mon in observation["battle"]["mons"] if mon.get("slot") == 1 and mon.get("present")),
                None,
            )
            if plan.get("action") == "move":
                opponent = post_opponent
                if opponent:
                    effectiveness = self._battle_effectiveness(feedback)
                    if effectiveness is not None:
                        effectiveness_memory[(opponent["species"], active["moves"][plan["slot"]])] = effectiveness
                if pre_opponent and post_opponent and post_opponent["species"] == pre_opponent["species"]:
                    damage = pre_opponent["current_hp"] - post_opponent["current_hp"]
                    if damage > 0:
                        key = (active["species"], active["moves"][plan["slot"]], post_opponent["species"])
                        self._remember_damage(key, damage, feedback=feedback)
            # Learn the opponent's actual hit when its move name appears in the
            # printer feedback and the active identity survived the turn.
            if post_player and post_player["species"] == active["species"]:
                damage = active["current_hp"] - post_player["current_hp"]
                if damage > 0 and post_opponent:
                    for move_id, move_name in zip(post_opponent["moves"], post_opponent.get("move_names", ())):
                        if move_id and move_name and move_name.lower() in feedback.lower():
                            key = (post_opponent["species"], move_id, active["species"])
                            self._remember_damage(key, damage, feedback=feedback)
            turns += 1
        raise RuntimeError(f"battle exceeded {max_turns} turns")

    def hunt_wild_species(
        self,
        target_species: int,
        *,
        max_steps: int = 2000,
        max_encounters: int = 100,
    ) -> dict[str, Any]:
        """Walk a verified grass pair, flee non-targets, and stop at target command."""
        if target_species <= 0 or max_steps < 1 or max_encounters < 1:
            raise ValueError("target species and hunt bounds must be positive")
        from games.run_and_bun.live_map import (
            is_land_encounter_tile,
            read_live_map,
            read_live_map_type,
        )

        start = self.observe()
        if start.get("battle", {}).get("active") or start.get("mode") != "overworld":
            raise RuntimeError("wild_hunt_requires_clean_overworld")
        map_state = start.get("map") or {}
        map_id = (int(map_state["group"]), int(map_state["number"]))
        current = (int(map_state["x"]), int(map_state["y"]))
        live = read_live_map(self.gba)
        map_type = read_live_map_type(self.gba)
        pairs = []
        directions = ((0, -1, "UP", "DOWN"), (1, 0, "RIGHT", "LEFT"), (0, 1, "DOWN", "UP"), (-1, 0, "LEFT", "RIGHT"))
        for y in range(live.active_height):
            for x in range(live.active_width):
                if not is_land_encounter_tile(live, x, y, map_type):
                    continue
                try:
                    path = live.path_to(current, (x, y), allow_nonwalkable_start=True, grass_penalty=0)
                except ValueError:
                    continue
                for dx, dy, outward, inward in directions:
                    neighbor = (x + dx, y + dy)
                    if (
                        0 <= neighbor[0] < live.active_width
                        and 0 <= neighbor[1] < live.active_height
                        and live.step_allowed((x, y), neighbor)
                    ):
                        pairs.append((len(path), (x, y), neighbor, outward, inward))
        if not pairs:
            raise RuntimeError("no_reachable_land_encounter_pair")
        _, encounter_tile, neighbor, outward, inward = min(pairs)

        encounters: list[dict[str, Any]] = []
        steps = 0
        while steps < max_steps and len(encounters) < max_encounters:
            state = self.observe()
            if state.get("battle", {}).get("active"):
                status = self.advance_battle_until_menu(
                    sample_frames=24, max_frames=1200, visual_fallback=False
                )
                if status.get("state") != "command_menu":
                    raise RuntimeError(f"wild_hunt_unstable_battle_boundary: {status.get('state')}")
                state = self.observe()
                opponent = self._present_battle_mon(state, 1)
                encountered = int(opponent["species"])
                encounters.append({
                    "species": encountered, "level": int(opponent["level"]),
                    "hp": int(opponent["current_hp"]), "max_hp": int(opponent["max_hp"]),
                })
                if encountered == target_species:
                    return {
                        "found": True, "target_species": target_species, "map": map_id,
                        "encounter_pair": [encounter_tile, neighbor], "steps": steps,
                        "encounters": encounters, "state": state,
                    }
                escaped = self.escape_battle()
                if escaped.get("state") != "overworld":
                    raise RuntimeError(f"wild_hunt_escape_failed: {escaped}")
                continue

            position_state = state.get("map") or {}
            actual_map = (position_state.get("group"), position_state.get("number"))
            if actual_map != map_id:
                raise RuntimeError(f"wild_hunt_map_changed: {actual_map} != {map_id}")
            position = (int(position_state["x"]), int(position_state["y"]))
            if position not in {encounter_tile, neighbor}:
                result = self.follow_live_path_adaptive(
                    encounter_tile, expected_map=map_id, grass_penalty=0, chunk_steps=6
                )
                if result.get("reason") == "interrupted":
                    continue
                position = tuple(result["position"])
            key = outward if position == encounter_tile else inward
            self.gba.sequence([
                {"keys": [key], "frames": 12},
                {"keys": [], "frames": 4},
            ])
            steps += 1
        return {
            "found": False, "target_species": target_species, "map": map_id,
            "encounter_pair": [encounter_tile, neighbor], "steps": steps,
            "encounters": encounters, "state": self.observe(),
        }

    def escape_battle(
        self,
        *,
        max_attempts: int = 6,
        sample_frames: int = 60,
    ) -> dict[str, Any]:
        """Attempt to flee a random encounter using RAM-backed cursors."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        from games.run_and_bun.state import RunBun

        state = RunBun(self.gba)
        attempts = 0
        while attempts < max_attempts:
            observation = self.observe()
            if not observation["battle"]["active"]:
                return {"state": "overworld", "attempts": attempts}
            status = self.advance_battle_until_menu(
                sample_frames=sample_frames,
                max_frames=1200,
                visual_fallback=False,
            )
            if status["state"] == "move_menu":
                # A stale/queued Fight confirmation can leave an otherwise
                # untouched random encounter in move selection. Back out to
                # the command grid; never turn a flee request into an attack.
                self.gba.press("B", frames=3)
                self.gba.wait_frames(sample_frames)
                continue
            if status["state"] != "command_menu":
                if status["state"] in {"not_in_battle", "battle_end"}:
                    return {"state": "overworld", "attempts": attempts, "status": status}
                raise RuntimeError(f"escape controller stopped in {status['state']}")
            state.set_action_cursor(3)
            self.gba.press("A", frames=3)
            self.gba.wait_frames(sample_frames)
            attempts += 1
        raise RuntimeError(f"failed to escape battle after {max_attempts} attempts")

    @staticmethod
    def _poke_ball_quantity(inventory: dict[str, Any]) -> int:
        """Return item-ID 1 only from the verified Poké Ball pockets."""
        return sum(
            int(item.get("quantity", 0))
            for pocket in ("poke_balls", "ui_poke_balls")
            for entries in (inventory.get("pockets", {}).get(pocket, []),)
            if isinstance(entries, list)
            for item in entries
            if item.get("item_id") == 1
        )

    @classmethod
    def capture_decision_certificate(
        cls,
        observation: dict[str, Any],
        *,
        poke_balls: int,
        type_chart: dict[int, dict[int, float]] | None = None,
        damage_memory: dict[tuple[int, int, int], list[int]] | None = None,
        move_data: dict[int, RomMove] | None = None,
    ) -> dict[str, Any]:
        """Choose a conservative wild-capture action from canonical RAM state.

        A weakening move is legal for this policy only when its modeled normal
        maximum is strictly below live target HP. Moves with lock-in or
        residual-KO risk are excluded even if their first hit is nonlethal.
        Unknown damage is uncertainty, never evidence that a move is safe.
        """
        battle = observation.get("battle", {})
        if not battle.get("active"):
            raise RuntimeError("capture_requires_active_battle")
        # Old allied/enemy slots 2/3 can remain populated after a prior double
        # battle. A verified Wild printer takes precedence over that stale
        # struct residue for this single-target helper.
        menu = battle.get("menu", {})
        stale_double_slots = (
            menu.get("state") == "command_menu"
            and menu.get("command_subject")
            and menu.get("command_battler") is None
        )
        if (
            battle.get("format") == "double"
            and battle.get("kind") != "wild"
            and not stale_double_slots
        ):
            raise RuntimeError("capture_helper_does_not_support_double_battles")
        mons = battle.get("mons", [])
        player = next(
            (item for item in mons if item.get("slot") == 0 and item.get("present")),
            None,
        )
        opponent = next(
            (item for item in mons if item.get("slot") == 1 and item.get("present")),
            None,
        )
        if player is None or opponent is None:
            raise RuntimeError("capture_requires_live_player_and_wild_target")
        player_state = player["state"]
        opponent_state = opponent["state"]
        target_hp = int(opponent_state.get("current_hp", 0))
        target_max_hp = max(int(opponent_state.get("max_hp", 0)), 1)
        if target_hp <= 0:
            raise RuntimeError("capture_target_is_fainted")

        legal_moves: list[dict[str, Any]] = []
        unknown_moves: list[int] = []
        accuracy_stages = player_state.get("stat_stages") or ()
        accuracy_stage = int(accuracy_stages[6]) if len(accuracy_stages) > 6 else 6
        accuracy_multiplier = cls._accuracy_stage_multiplier(player_state)
        for slot, move_id in enumerate(player_state.get("moves", ())):
            pp = (player_state.get("pp") or (0, 0, 0, 0))[slot]
            if not move_id or not pp:
                continue
            damage_min, damage_max = cls._damage_bounds(
                move_id,
                player,
                opponent,
                type_chart=type_chart,
                damage_memory=damage_memory,
                move_data=move_data,
            )
            if damage_max <= 0 and move_id not in STATUS_MOVE_IDS:
                unknown_moves.append(int(move_id))
            # Gen III criticals can double normal damage. Use that larger
            # bound even though later-generation mechanics often use 1.5x;
            # a move is capture-safe only when every modeled crit remains
            # nonlethal.
            critical_damage_max = damage_max * 2
            metadata = (move_data or {}).get(int(move_id))
            status_effect = CAPTURE_STATUS_MOVES.get((metadata.name or "") if metadata else "")
            status_effectiveness = 1.0
            if metadata is not None:
                for defender_type in cls._mon_types(opponent):
                    status_effectiveness *= (type_chart or TYPE_EFFECTIVENESS).get(
                        metadata.type_id, {}
                    ).get(defender_type, 1.0)
            guaranteed_status = bool(
                status_effect
                and status_effectiveness > 0
                and (metadata.category == "status" or metadata.secondary_chance >= 100)
            ) if metadata is not None else False
            safe = (
                damage_min > 0
                and critical_damage_max < target_hp
                and move_id not in CAPTURE_UNSAFE_MOVE_IDS
            )
            legal_moves.append({
                "kind": "move",
                "slot": slot,
                "move_id": int(move_id),
                "pp": int(pp),
                "damage_range": [round(damage_min, 2), round(damage_max, 2)],
                "critical_damage_max": round(critical_damage_max, 2),
                "accuracy_stage": accuracy_stage,
                "accuracy_multiplier": round(accuracy_multiplier, 4),
                "expected_damage_after_accuracy": round(
                    ((damage_min + damage_max) / 2) * accuracy_multiplier,
                    2,
                ),
                "guaranteed_nonlethal": safe,
                "post_hp_range": [
                    max(1, round(target_hp - critical_damage_max, 2)),
                    max(1, round(target_hp - damage_min, 2)),
                ] if safe else None,
                "excluded_for_residual_risk": move_id in CAPTURE_UNSAFE_MOVE_IDS,
                "capture_status": status_effect[0] if guaranteed_status else None,
                "capture_status_multiplier": status_effect[1] if guaranteed_status else None,
            })

        target_status = int(opponent_state.get("status", 0))
        residual_status = bool(target_status & (0x08 | 0x10 | 0x80))
        safe_moves = [move for move in legal_moves if move["guaranteed_nonlethal"]]
        # Maximize the conservative minimum reduction, then the modeled
        # maximum reduction; this increases catch odds without accepting a KO
        # roll. Stable slot ordering makes ties deterministic.
        safest_weaken = max(
            safe_moves,
            key=lambda move: (
                move["capture_status_multiplier"] if target_status == 0 and move["capture_status"] else 0,
                move["damage_range"][0],
                move["damage_range"][1],
                -move["slot"],
            ),
            default=None,
        )
        hp_fraction = target_hp / target_max_hp
        status_setup = max(
            (
                move for move in legal_moves
                if move["capture_status"] and move["damage_range"][1] == 0
            ),
            key=lambda move: (
                move["capture_status_multiplier"],
                move["accuracy_multiplier"],
                -move["slot"],
            ),
            default=None,
        )
        if safest_weaken is not None and not residual_status:
            decision = {"kind": "move", **safest_weaken}
            claim = "minimax-safe weakening line; no ball is used while a crit-safe catch-factor improvement remains"
        elif target_status == 0 and status_setup is not None:
            decision = {"kind": "move", **status_setup}
            claim = "expected-best sleep/paralysis setup after reaching the lowest crit-safe HP"
        elif poke_balls > 0:
            decision = {"kind": "throw_ball", "button": "L"}
            claim = "minimax-safe throw: no verified action can further improve catch factor without KO risk"
        else:
            decision = {"kind": "blocked", "reason": "no_poke_balls"}
            claim = "capture is impossible with verified inventory"
        status_multiplier = (
            2.0 if target_status & (0x07 | 0x20)
            else 1.5 if target_status & (0x08 | 0x10 | 0x40 | 0x80)
            else 1.0
        )
        catch_factor_score = ((3 * target_max_hp - 2 * target_hp) / (3 * target_max_hp)) * status_multiplier
        return {
            "state": {
                "player": {
                    "species": player_state.get("species"),
                    "hp": player_state.get("current_hp"),
                    "max_hp": player_state.get("max_hp"),
                },
                "target": {
                    "species": opponent_state.get("species"),
                    "hp": target_hp,
                    "max_hp": target_max_hp,
                    "status": opponent_state.get("status", 0),
                    "hp_fraction": round(hp_fraction, 4),
                    "relative_catch_factor": round(catch_factor_score, 4),
                    "status_multiplier": status_multiplier,
                },
                "poke_balls": int(poke_balls),
            },
            "legal_actions": {
                "moves": legal_moves,
                "throw_ball": poke_balls > 0,
                "switch": any(
                    mon.get("present")
                    and mon.get("slot") != 0
                    and mon.get("state", {}).get("current_hp", 0) > 0
                    for mon in observation.get("party", {}).get("mons", [])
                ),
                "run": True,
            },
            "decision": decision,
            "proof": {
                "level": (
                    "forced" if decision["kind"] == "blocked"
                    else "expected-best" if decision.get("capture_status")
                    else "minimax"
                ),
                "claim": claim,
                "material_uncertainty": {
                    "catch_rng": decision["kind"] == "throw_ball",
                    "unknown_damage_move_ids": sorted(set(unknown_moves)),
                    "critical_hit_multiplier_bound": 2.0 if decision["kind"] == "move" else None,
                    "accuracy_stage": accuracy_stage if decision["kind"] == "move" else None,
                    "accuracy_multiplier": round(accuracy_multiplier, 4) if decision["kind"] == "move" else None,
                    "status_effect_model": (
                        "ROM move name/category/chance identify the effect; the battle script is verified after execution"
                        if decision.get("capture_status") else None
                    ),
                    "volatile_infatuation": (
                        "not decoded in canonical battler RAM; recent feedback may cause action failure"
                        if decision["kind"] == "move"
                        else None
                    ),
                    "full_paralysis": (
                        "25% action-failure chance"
                        if decision["kind"] == "move"
                        and int(player_state.get("status", 0)) & PARALYSIS_STATUS
                        else None
                    ),
                },
            },
        }

    def throw_poke_ball_hotkey(
        self,
        *,
        nickname: str,
        max_frames: int = 1800,
    ) -> dict[str, Any]:
        """Throw the current Poké Ball with L and verify the complete result.

        Run & Bun exposes a battle hotkey on L. This avoids opening the Bag;
        quantity delta, capture text, and party/PC RAM deltas are the
        authoritative acknowledgments. A full party is supported: the game
        sends the captured mon to PC storage, whose pointed storage image must
        change before the helper returns success.
        """
        from games.run_and_bun.state import RunBun

        requested_nickname = self._nickname_keyboard_plan(nickname)[0]
        state = RunBun(self.gba)
        before = self.observe()
        if before.get("battle", {}).get("menu", {}).get("state") == "move_menu":
            self.gba.press("B", frames=3)
            self.gba.wait_frames(30)
            before = self.observe()
        if before.get("battle", {}).get("menu", {}).get("state") != "command_menu":
            raise RuntimeError("poke_ball_hotkey_requires_command_menu")
        before_count = state.party_count()
        before_storage = self._pokemon_storage_digest()
        before_storage_personalities = {
            int(record["state"]["personality"])
            for record in self.pokemon_storage()["records"]
        } if before_count >= 6 else set()
        before_balls = self._poke_ball_quantity(self.inventory())
        if before_balls <= 0:
            raise RuntimeError("no_poke_balls")
        target = self._present_battle_mon(before, 1)
        active = self._present_battle_mon(before, 0)

        self.gba.press("L", frames=3)
        self.gba.wait_frames(180)
        resolution = self.advance_battle_until_menu(
            sample_frames=24,
            max_frames=max_frames,
            visual_fallback=False,
            after_action=True,
        )
        after_balls = self._poke_ball_quantity(self.inventory())
        if after_balls != before_balls - 1:
            raise RuntimeError(
                f"poke_ball_hotkey_not_acknowledged: balls {before_balls}->{after_balls}"
            )
        feedback = resolution.get("feedback", "")
        caught = "was caught" in feedback or "Gotcha!" in feedback
        captured_nickname = None
        if caught:
            # A first capture may show Pokédex registration before the naming
            # question. After catch text is verified, A may only advance that
            # post-capture flow; B is the deterministic No/cancel shortcut at
            # the nickname prompt.
            nickname_screen_resolved = False
            for _ in range(16):
                party_inserted = before_count < 6 and state.party_count() == before_count + 1
                pc_changed = before_count >= 6 and self._pokemon_storage_digest() != before_storage
                if party_inserted or pc_changed:
                    break
                observed = self.observe()
                rendered = "\n".join(self._active_field_page_texts(observed))
                if self.gba.read8(FIELD_MESSAGE_BOX_MODE) == 10:
                    if nickname_screen_resolved:
                        if "transferred to" in rendered:
                            self._pc_tap("A")
                        else:
                            self.gba.wait_frames(120)
                        continue
                    self._type_nickname_keyboard(requested_nickname)
                    nickname_screen_resolved = True
                    continue
                if "Give a nickname" in rendered:
                    for _ in range(4):
                        if self.gba.read8(FIELD_MESSAGE_BOX_MODE) == 10:
                            break
                        if self.gba.read8(YES_NO_CURSOR) == 1:
                            self._pc_tap("UP", hold_frames=12)
                        self._pc_tap("A", hold_frames=12)
                    else:
                        raise RuntimeError("capture_nickname_screen_not_ready")
                    self._type_nickname_keyboard(requested_nickname)
                    nickname_screen_resolved = True
                    continue
                self._pc_tap("A")
            if before_count < 6:
                if state.party_count() != before_count + 1:
                    raise RuntimeError("capture_text_seen_but_party_insertion_not_verified")
                inserted = state.party_mon(before_count)
                if inserted.species_id != target.get("species"):
                    raise RuntimeError(
                        f"captured_species_mismatch: expected {target.get('species')} got {inserted.species_id}"
                    )
                captured_nickname = inserted.nickname
                outcome = "caught"
            elif self._pokemon_storage_digest() == before_storage:
                raise RuntimeError("capture_text_seen_but_pc_storage_change_not_verified")
            else:
                added = [
                    record for record in self.pokemon_storage()["records"]
                    if int(record["state"]["personality"]) not in before_storage_personalities
                ]
                if len(added) != 1 or int(added[0]["state"]["species"]) != int(target.get("species", 0)):
                    raise RuntimeError(f"captured_storage_identity_not_unique: {added}")
                captured_nickname = str(added[0]["state"].get("nickname", ""))
                outcome = "caught_to_pc"
            if captured_nickname != requested_nickname:
                raise RuntimeError(
                    f"captured_nickname_mismatch: expected={requested_nickname!r} actual={captured_nickname!r}"
                )
        elif resolution.get("state") == "command_menu":
            outcome = "escaped_ball"
        elif resolution.get("state") == "party_switch":
            outcome = "party_switch"
        else:
            raise RuntimeError(
                f"poke_ball_outcome_unresolved: state={resolution.get('state')} feedback={feedback!r}"
            )
        final_observation = self.observe()
        active_after = next(
            (
                mon["state"].get("current_hp")
                for mon in final_observation.get("battle", {}).get("mons", [])
                if mon.get("slot") == 0 and mon.get("present")
            ),
            None,
        )
        if active_after is None:
            active_after = next(
                (
                    mon["state"].get("current_hp")
                    for mon in final_observation.get("party", {}).get("mons", [])
                    if mon.get("present")
                    and mon.get("state", {}).get("species") == active.get("species")
                ),
                None,
            )
        return {
            "outcome": outcome,
            "target_species": target.get("species"),
            "target_hp": target.get("current_hp"),
            "active_hp_before": active.get("current_hp"),
            "active_hp_after": active_after,
            "balls_before": before_balls,
            "balls_after": after_balls,
            "party_count_before": before_count,
            "party_count_after": state.party_count(),
            "storage_changed": self._pokemon_storage_digest() != before_storage,
            "nickname_requested": requested_nickname,
            "nickname": captured_nickname,
            "resolution": resolution,
        }

    def _pokemon_storage_digest(self) -> str:
        """Hash the live 14-box storage image for full-party capture proof."""
        pointer = self.gba.read32(PC_STORAGE_PTR)
        if not (0x02000000 <= pointer < 0x02040000):
            raise RuntimeError(f"invalid_pokemon_storage_pointer: {pointer:#x}")
        # Gen III storage is 14 boxes × 30 BoxPokemon records × 80 bytes.
        raw = self.gba.read_range(pointer, 14 * 30 * 80, name="pokemon_storage")
        return hashlib.sha256(raw).hexdigest()

    def finish_battle_after_ko(
        self,
        *,
        sample_frames: int = 24,
        max_frames: int = 900,
    ) -> dict[str, Any]:
        """Drain post-KO battle messages until the overworld is restored."""
        if sample_frames < 1 or max_frames < 1:
            raise ValueError("sample_frames and max_frames must be positive")
        elapsed = 0
        presses = 0
        while elapsed <= max_frames:
            state = self.observe()
            field_mode = state["ui"].get("field_message_box_mode", 0)
            text = state.get("text") or {}
            if not state["battle"]["active"] and field_mode == 0:
                # May's post-battle conversation can begin with field mode 0;
                # hand that semantic dialogue back to the caller instead of
                # misreporting it as a clean overworld transition.
                if text.get("visible") or text.get("active"):
                    return {"state": "dialogue", "frames": elapsed, "presses": presses}
                return {"state": "overworld", "frames": elapsed, "presses": presses}
            if field_mode != 0:
                if text.get("active"):
                    self.gba.wait_frames(sample_frames)
                else:
                    self.gba.press("A")
                    presses += 1
                    self.gba.wait_frames(sample_frames)
            else:
                self.gba.wait_frames(sample_frames)
            elapsed += sample_frames
        return {"state": "timeout", "frames": elapsed, "presses": presses}

    def walk(self, direction: str, tiles: int, frames: int = 12) -> list[dict[str, Any]]:
        """Walk with per-tile coordinate confirmation from SaveBlock1."""
        if tiles < 0:
            raise ValueError("tiles must be non-negative")
        positions: list[dict[str, Any]] = []
        for _ in range(tiles):
            before = self.observe()
            if before["mode"] == "dialogue":
                raise RuntimeError("cannot walk while dialogue is active")
            self.gba.press(direction, frames=frames)
            after = self.observe()
            positions.append(after["save"]["block1"])
            if after["mode"] == "dialogue":
                break
            if after["save"]["block1"] == before["save"]["block1"]:
                break
        return positions

    def follow_route(
        self,
        directions: list[str] | tuple[str, ...],
        *,
        frames: int = 12,
        settle_frames: int = 8,
        transition_frames: int = 30,
        expected_map: tuple[int, int] | None = None,
        expected_position: tuple[int, int] | None = None,
        verified_defeated_trainer_local_ids: set[int] | None = None,
        allow_damaged_trainer_sight_lines: bool = False,
    ) -> dict[str, Any]:
        """Execute a known route as one bridge action and verify its endpoint.

        This is the fast counterpart to :meth:`walk`: route planning happens
        from known map geometry/warps, while the emulator still returns one
        frame-synchronized action record and one semantic endpoint observation.
        """
        normalized = [direction.upper() for direction in directions]
        if not normalized:
            raise ValueError("route must contain at least one direction")
        if any(direction not in {"UP", "DOWN", "LEFT", "RIGHT"} for direction in normalized):
            raise ValueError(f"invalid route direction: {directions!r}")
        if frames < 1 or settle_frames < 0 or transition_frames < 0:
            raise ValueError("route timing values must be non-negative, with frames >= 1")
        before = self.observe()
        if before.get("battle", {}).get("active"):
            raise RuntimeError("cannot navigate while battle is active")
        if before.get("mode") != "overworld" or before.get("ui", {}).get("field_bag_open") or before.get("ui", {}).get("field_start_menu_open"):
            raise RuntimeError(f"cannot navigate while field UI is active: mode={before.get('mode')} ui={before.get('ui')}")
        if self.enforce_live_trainer_gate:
            gate = self._trainer_route_gate(
                normalized,
                verified_defeated_trainer_local_ids=verified_defeated_trainer_local_ids,
                allow_damaged_trainer_sight_lines=allow_damaged_trainer_sight_lines,
            )
            if not gate["allowed"]:
                raise RuntimeError(f"trainer_engagement_blocked: {gate}")
        steps: list[dict[str, Any]] = []
        index = 0
        while index < len(normalized):
            direction = normalized[index]
            end = index + 1
            while end < len(normalized) and normalized[end] == direction:
                end += 1
            run_length = end - index
            # Holding a direction lets the game consume a clear straight run
            # without a host round trip per tile.  A short tail of held input
            # absorbs the movement animation between repeated steps; the
            # explicit release still prevents this run leaking into the next
            # turn or across a warp.
            inter_tile_frames = frames + max(4, settle_frames // 2)
            held_frames = frames + inter_tile_frames * (run_length - 1)
            steps.append({"keys": [direction], "frames": held_frames})
            if settle_frames:
                steps.append({"keys": [], "frames": settle_frames})
            index = end
        action_timeout = max(5.0, sum(step["frames"] for step in steps) / 30.0 + 2.0)
        action = self.gba.sequence(steps, timeout=action_timeout)
        if transition_frames:
            self.gba.wait_frames(transition_frames)
        state = self.observe()
        block = state.get("save", {}).get("block1") or {}
        actual_map = (block.get("map_group"), block.get("map_number"))
        actual_position = (block.get("x"), block.get("y"))
        if expected_map is not None and actual_map != expected_map:
            raise RuntimeError(f"route ended on map {actual_map}, expected {expected_map}")
        if expected_position is not None and actual_position != expected_position:
            raise RuntimeError(
                f"route ended at {actual_position}, expected {expected_position}"
            )
        return {"action": action, "state": state, "map": actual_map, "position": actual_position}

    def _trainer_route_gate(
        self,
        directions: list[str],
        *,
        verified_defeated_trainer_local_ids: set[int] | None = None,
        allow_damaged_trainer_sight_lines: bool = False,
    ) -> dict[str, Any]:
        """Prevent raw live movement from entering a classified hard trainer ray."""
        from games.run_and_bun.objects import read_live_objects

        state = self.observe()
        if state.get("mode") != "overworld":
            return {"allowed": True, "reason": "not_overworld"}
        map_state = state.get("map") or {}
        map_id = (map_state.get("group"), map_state.get("number"))
        position = (map_state.get("x"), map_state.get("y"))
        if None in map_id or None in position:
            return {"allowed": False, "reason": "trainer_gate_state_incomplete"}
        map_id = (int(map_id[0]), int(map_id[1]))
        position = (int(position[0]), int(position[1]))
        ignored = {int(local_id) for local_id in (verified_defeated_trainer_local_ids or set())}
        live_objects = read_live_objects(self.gba)
        active_live_ids = {
            int(obj.local_id)
            for obj in live_objects
            if obj.active and not obj.invisible and not obj.is_player
            and obj.trainer_type and obj.map_id == map_id
        }
        targets: dict[int, Any] = {}
        # A live object is the only proof that a sightline is currently active.
        # Event templates remain useful for identity/range lookup, but may
        # describe a beaten trainer after the runtime object is removed.
        for target in live_objects:
            local_id = int(getattr(target, "local_id", -1))
            if (
                getattr(target, "trainer_type", 0)
                and getattr(target, "map_id", None) == map_id
                and getattr(target, "active", True)
                and local_id not in ignored
            ):
                targets.setdefault(local_id, target)
        if not targets:
            return {"allowed": True, "reason": "no_trainer_on_map"}
        blocked = self._trainer_sight_tiles(
            self.gba, map_id, current=position, ignored_local_ids=ignored,
            active_only=True,
        )
        deltas = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}
        traversed: list[tuple[int, int]] = []
        current = position
        for direction in directions:
            dx, dy = deltas[direction]
            current = (current[0] + dx, current[1] + dy)
            traversed.append(current)
        if not blocked.intersection(traversed):
            return {"allowed": True, "reason": "trainer_sightline_clear"}
        traversed_set = set(traversed)
        intersecting = {
            local_id: target
            for local_id, target in targets.items()
            if self._trainer_sight_tiles(
                self.gba,
                map_id,
                current=position,
                target=getattr(target, "position", None),
                ignored_local_ids=set(targets) - {local_id},
                active_only=True,
            ).intersection(traversed_set)
        }
        preflights = {
            str(local_id): self.trainer_preflight(target)
            for local_id, target in intersecting.items()
        }
        if allow_damaged_trainer_sight_lines:
            for local_id, report in preflights.items():
                if (
                    int(local_id) in active_live_ids
                    and report.get("classification") in {"easy", "unclassified"}
                    and report.get("reason") in {"healing_required", "unclassified_trainer_allowed"}
                ):
                    report["ready"] = True
                    report["reason"] = "damaged_trainer_sightline_explicitly_allowed"
        denied = {
            local_id: report
            for local_id, report in preflights.items()
            if not report.get("ready")
        }
        return {
            "allowed": not denied,
            "reason": "trainer_sightline_preflight_verified" if not denied else "trainer_sightline_preflight_required",
            "preflights": preflights,
        }

    def follow_live_path(
        self,
        target: tuple[int, int],
        *,
        expected_map: tuple[int, int] | None = None,
        transition_frames: int = 30,
        grass_penalty: int = 100,
    ) -> dict[str, Any]:
        """Solve the loaded map from its live collision grid and execute it once."""
        from games.run_and_bun.live_map import read_live_map

        before = self.observe()
        map_state = before.get("map")
        if not map_state:
            raise RuntimeError("cannot pathfind without a loaded map position")
        start = (map_state["x"], map_state["y"])
        path = read_live_map(self.gba).path_to(
            start,
            target,
            allow_nonwalkable_start=True,
            grass_penalty=grass_penalty,
        )
        if not path:
            return {
                "action": None,
                "state": before,
                "map": (map_state["group"], map_state["number"]),
                "position": start,
            }
        return self.follow_route(
            path,
            expected_map=expected_map,
            expected_position=target,
            transition_frames=transition_frames,
        )

    @classmethod
    def _trainer_sight_tiles(
        cls,
        gba: Any,
        map_id: tuple[int, int],
        *,
        current: tuple[int, int] | None = None,
        target: tuple[int, int] | None = None,
        ignored_local_ids: set[int] | None = None,
        active_only: bool = False,
    ) -> set[tuple[int, int]]:
        """Return RAM-derived tiles that can trigger a trainer sight battle.

        Runtime objects expose the current facing ray.  Immediately after a
        map connection, the object table can still contain the source map, so
        stationary trainer event templates are also read as a conservative
        four-way exclusion using their ROM sight radius.  NPC interaction has
        its own seeker; ordinary navigation should never walk through a
        trainer ray by accident.
        """
        from games.run_and_bun.objects import read_live_event_targets, read_live_objects

        blocked: set[tuple[int, int]] = set()
        ignored = ignored_local_ids or set()
        event_radii: dict[int, int] = {}
        event_positions: dict[int, tuple[int, int]] = {}
        for event in read_live_event_targets(gba, map_id=map_id):
            if not event.trainer_type or event.local_id in ignored:
                continue
            if current is not None or target is not None:
                distances = [
                    abs(event.current_x - point[0]) + abs(event.current_y - point[1])
                    for point in (current, target)
                    if point is not None
                ]
                # During a connection rebuild templates for the entire map
                # are visible. Only nearby trainers can intersect the next
                # bounded route segment; later replans refresh this filter.
                if min(distances, default=10**9) > 16:
                    continue
            radius = max(1, int(event.trainer_sight_radius or 1))
            event_radii[event.local_id] = radius
            event_positions[event.local_id] = event.position

        live_trainer_ids: set[int] = set()
        for obj in read_live_objects(gba):
            if obj.is_player or not obj.trainer_type or obj.map_id != map_id or obj.local_id in ignored:
                continue
            live_trainer_ids.add(obj.local_id)
            # The trainer's own tile is occupied as well as the facing ray.
            # Without this, a detour can route directly into the NPC and the
            # adaptive bridge will repeatedly retry the same impossible edge.
            blocked.add(obj.position)
            # In this ROM the live object byte is shared with another field
            # and can contain 33 even when the trainer's event template says
            # the actual sight radius is 5 or 7. Treat implausibly large
            # values as non-range payload and use the ROM-backed template;
            # otherwise navigation can manufacture an entire map-wide wall.
            live_range = int(obj.trainer_range_or_berry_id)
            template_range = int(event_radii.get(obj.local_id, 5))
            radius = live_range if 0 < live_range <= 16 else template_range
            radius = max(1, radius)
            facing = cls._trainer_facing_delta(obj.facing_direction)
            if facing is None:
                continue
            for distance in range(1, radius + 1):
                blocked.add((obj.current_x + facing[0] * distance, obj.current_y + facing[1] * distance))

        # Template coordinates are authoritative during connection rebuilds,
        # but do not carry the current facing direction.  Movement type 8 is
        # verified in this ROM as the stationary trainer orientation used by
        # Gavi and faces down.  For other template movement types, wait for a
        # live object (which carries facing_direction) instead of creating a
        # four-way wall that can make a legitimate route unreachable.
        if active_only:
            return blocked
        for local_id, position in event_positions.items():
            if local_id in live_trainer_ids:
                continue
            event = next(
                event
                for event in read_live_event_targets(gba, map_id=map_id)
                if event.local_id == local_id
            )
            if event.movement_type != 8:
                continue
            radius = event_radii[local_id]
            # Event templates are the only reliable coordinates during a map
            # connection rebuild.  Block the template tile too, otherwise a
            # route can walk into the stale trainer position while only
            # avoiding the inferred downward ray.
            blocked.add(position)
            for distance in range(1, radius + 1):
                blocked.add((position[0], position[1] + distance))
        return blocked

    def follow_live_path_adaptive(
        self,
        target: tuple[int, int],
        *,
        expected_map: tuple[int, int] | None = None,
        chunk_steps: int = 8,
        frames: int = 12,
        settle_frames: int = 4,
        transition_frames: int = 30,
        max_replans: int = 32,
        blocked_wait_frames: int = 12,
        grass_penalty: int = 100,
        blocked_edges: set[tuple[tuple[int, int], str]] | None = None,
        avoid_trainer_sight_lines: bool = True,
        verified_defeated_trainer_local_ids: set[int] | None = None,
        allow_damaged_trainer_sight_lines: bool = False,
    ) -> dict[str, Any]:
        """Navigate by short compressed chunks and replan around blockers.

        The static runtime grid provides the initial route, while SaveBlock1
        coordinates provide feedback after each bridge action.  If a moving
        object prevents the first planned step, that directed edge is avoided
        for the next local search and the game gets a brief chance to advance
        the NPC.  This preserves batched input for normal movement while
        avoiding the brittle behavior of one long macro ending at an NPC.
        """
        if chunk_steps < 1:
            raise ValueError("chunk_steps must be >= 1")
        if max_replans < 1:
            raise ValueError("max_replans must be >= 1")
        if blocked_wait_frames < 0:
            raise ValueError("blocked_wait_frames must be >= 0")

        from games.run_and_bun.live_map import read_live_map

        persistent_blocked_edges = set(blocked_edges or ())
        dynamic_blocked_edges: set[tuple[tuple[int, int], str]] = set()
        stalled: dict[tuple[tuple[int, int], str], int] = {}
        actions: list[dict[str, Any]] = []
        last_state = self.observe()

        for replan in range(max_replans):
            map_state = last_state.get("map") or {}
            actual_map = (map_state.get("group"), map_state.get("number"))
            current = (map_state.get("x"), map_state.get("y"))
            if None in current:
                raise RuntimeError("cannot adaptively pathfind without a live map position")
            if expected_map is not None and actual_map != expected_map:
                raise RuntimeError(f"route ended on map {actual_map}, expected {expected_map}")
            if current == target:
                return {
                    "state": last_state,
                    "map": actual_map,
                    "position": current,
                    "actions": actions,
                    "replans": replan,
                    "reason": "target",
                }
            if last_state.get("mode") != "overworld":
                return {
                    "state": last_state,
                    "map": actual_map,
                    "position": current,
                    "actions": actions,
                    "replans": replan,
                    "reason": "interrupted",
                }

            live = read_live_map(self.gba)
            if not live.walkable(*target):
                raise ValueError(f"target is not walkable in live grid: {target!r}")
            trainer_sight_tiles = (
                self._trainer_sight_tiles(
                    self.gba,
                    actual_map,
                    current=current,
                    target=target,
                    ignored_local_ids=verified_defeated_trainer_local_ids,
                    active_only=True,
                )
                if avoid_trainer_sight_lines
                else set()
            )
            # Block every active same-map object tile, not just trainers.
            # This prevents the planner from selecting a route that ends on a
            # blocking NPC and then spending its replan budget retrying it.
            from games.run_and_bun.objects import read_live_objects

            occupied_tiles = {
                obj.position
                for obj in read_live_objects(self.gba)
                if obj.active
                and not obj.invisible
                and not obj.is_player
                and obj.map_id == actual_map
            }
            path = None
            try:
                path = live.path_to(
                    current,
                    target,
                    blocked_edges=persistent_blocked_edges | dynamic_blocked_edges,
                    blocked_tiles=trainer_sight_tiles | occupied_tiles,
                    allow_nonwalkable_start=True,
                    grass_penalty=grass_penalty,
                )
            except ValueError as error:
                if not str(error).startswith("no live-grid path"):
                    raise
                if allow_damaged_trainer_sight_lines:
                    try:
                        path = live.path_to(
                            current,
                            target,
                            blocked_edges=persistent_blocked_edges | dynamic_blocked_edges,
                            blocked_tiles=occupied_tiles,
                            allow_nonwalkable_start=True,
                            grass_penalty=grass_penalty,
                        )
                    except ValueError:
                        path = None
                if path is None:
                    # A dynamic obstruction can temporarily make the current
                    # tile look unusable. Let the map task/NPC advance, then
                    # retry the authoritative read instead of consulting a
                    # screenshot.
                    if blocked_wait_frames:
                        self.gba.wait_frames(blocked_wait_frames)
                    dynamic_blocked_edges.clear()
                    stalled.clear()
                    last_state = self.observe()
                    continue

            if not path:
                continue
            # Validate the entire planned route before issuing its first
            # chunk. The chunk-level gate below still protects against
            # moving trainers, but a denied route must not partially advance
            # the live player before the denial is reported.
            if self.enforce_live_trainer_gate:
                gate = self._trainer_route_gate(
                    path,
                    verified_defeated_trainer_local_ids=verified_defeated_trainer_local_ids,
                    allow_damaged_trainer_sight_lines=allow_damaged_trainer_sight_lines,
                )
                if not gate["allowed"]:
                    raise RuntimeError(f"trainer_engagement_blocked: {gate}")
            first_edge = (current, path[0])
            chunk = path[:chunk_steps]
            result = self.follow_route(
                chunk,
                frames=frames,
                settle_frames=settle_frames,
                transition_frames=transition_frames,
                verified_defeated_trainer_local_ids=verified_defeated_trainer_local_ids,
                allow_damaged_trainer_sight_lines=allow_damaged_trainer_sight_lines,
            )
            actions.append(result["action"])
            next_state = result["state"]
            next_map_state = next_state.get("map") or {}
            next_map = (next_map_state.get("group"), next_map_state.get("number"))
            next_position = (next_map_state.get("x"), next_map_state.get("y"))

            # A random encounter or scripted battle can become visible during
            # the bridge transition wait even when the endpoint coordinate did
            # change.  Return immediately so a macro never keeps feeding
            # overworld directions into a battle/menu.
            if next_state.get("battle", {}).get("active") or next_state.get("mode") != "overworld":
                return {
                    "state": next_state,
                    "map": next_map,
                    "position": next_position,
                    "actions": actions,
                    "replans": replan + 1,
                    "reason": "interrupted",
                }

            if next_map != actual_map:
                # A route chunk can legitimately cross a warp.  The caller can
                # inspect the returned state and continue with a new target.
                return {
                    "state": next_state,
                    "map": next_map,
                    "position": next_position,
                    "actions": actions,
                    "replans": replan + 1,
                    "reason": "map_transition",
                }
            if next_position == current:
                stalled[first_edge] = stalled.get(first_edge, 0) + 1
                if stalled[first_edge] >= 3:
                    raise RuntimeError(
                        f"adaptive route stalled at {current} on {path[0]} after "
                        f"{stalled[first_edge]} retries"
                    )
                dynamic_blocked_edges.add(first_edge)
                if blocked_wait_frames:
                    self.gba.wait_frames(blocked_wait_frames)
            else:
                # Dynamic blockers are transient.  Once movement resumes,
                # discard their directed-edge hints and solve from reality.
                dynamic_blocked_edges.clear()
                stalled.clear()
            last_state = self.observe()

        raise RuntimeError(
            f"adaptive route exceeded {max_replans} replans at "
            f"{last_state.get('map')} targeting {target!r}"
        )

    def live_objects(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        """Return the current runtime object table as semantic dictionaries."""
        from games.run_and_bun.objects import read_live_objects

        return [
            object_event.as_dict()
            for object_event in read_live_objects(self.gba, include_inactive=include_inactive)
        ]

    def live_map_layout(
        self,
        *,
        include_tiles: bool = True,
        include_ascii: bool = True,
    ) -> dict[str, Any]:
        """Discover the currently loaded map directly from the RAM tile buffer."""
        from games.run_and_bun.live_map import read_live_map

        return read_live_map(self.gba).layout(
            include_tiles=include_tiles,
            include_ascii=include_ascii,
        )

    def live_warps(self) -> list[dict[str, Any]]:
        """Return loaded-map warp destinations from the runtime event table."""
        from games.run_and_bun.live_map import read_live_warps

        return [warp.as_dict() for warp in read_live_warps(self.gba)]

    def live_map_transitions(self) -> dict[str, Any]:
        """Return direct map connections plus event warps from live RAM."""
        from games.run_and_bun.live_map import read_live_connections, read_live_warps

        state = self.observe()
        map_state = state.get("map") or {}
        map_id = (map_state.get("group"), map_state.get("number"))
        return {
            "map": map_state,
            "connections": [connection.as_dict() for connection in read_live_connections(self.gba)],
            "warps": [warp.as_dict() for warp in read_live_warps(self.gba)],
            "selection_rule": "walk to a reachable source edge, press its direction once, then verify destination map id",
            "source_map": map_id,
        }

    def live_transit_options(self) -> dict[str, Any]:
        """Return scripted ferry/transit actors from the loaded map's ROM scripts."""
        from games.run_and_bun.transit import read_live_transit_options

        state = self.observe()
        map_state = state.get("map") or {}
        map_id = (map_state.get("group"), map_state.get("number"))
        if None in map_id:
            raise RuntimeError("cannot inspect transit actors without a loaded map id")
        options = read_live_transit_options(self.gba, map_id=(int(map_id[0]), int(map_id[1])))
        return {
            "map": map_state,
            "source_map": (int(map_id[0]), int(map_id[1])),
            "options": [option.as_dict() for option in options],
            "selection_rule": "select a stable event-template local_id whose ROM script contains voyage text",
        }

    def travel_live_transit(
        self,
        *,
        local_id: int | None = None,
        graphics_id: int | None = None,
        expected_destination: tuple[int, int] | None = None,
        max_pages: int = 32,
        max_wait_frames: int = 3600,
        stable_reads: int = 2,
        wait_chunk_frames: int = 120,
        verified_defeated_trainer_local_ids: set[int] | None = None,
    ) -> dict[str, Any]:
        """Interact with one ROM-identified ferry and verify its final map.

        The ferry script may cross several intermediate water maps.  The
        controller therefore advances visible text, waits in bounded frame
        chunks while the script is moving, and returns only after the map and
        mode are stable.  No screenshot or guessed water path is used.
        """
        if local_id is None and graphics_id is None:
            raise ValueError("transit selection requires local_id or graphics_id")
        if max_pages < 1 or max_wait_frames < 1 or stable_reads < 1 or wait_chunk_frames < 1:
            raise ValueError("transit bounds must be positive")

        before = self.observe()
        before_map_state = before.get("map") or {}
        source_map = (before_map_state.get("group"), before_map_state.get("number"))
        if None in source_map:
            raise RuntimeError("cannot travel by transit without a loaded source map")
        source_map = (int(source_map[0]), int(source_map[1]))
        options = self.live_transit_options()["options"]
        matching = [
            option for option in options
            if (local_id is None or option.get("local_id") == local_id)
            and (graphics_id is None or option.get("graphics_id") == graphics_id)
        ]
        if not matching:
            raise RuntimeError(
                f"no ROM-identified transit actor on {source_map}: "
                f"local_id={local_id!r} graphics_id={graphics_id!r}"
            )
        if len(matching) != 1:
            raise RuntimeError("transit selector is ambiguous; specify local_id or graphics_id")
        selected = matching[0]

        interaction = self.follow_live_path_to_npc(
            local_id=selected["local_id"],
            graphics_id=selected["graphics_id"],
            expected_map=source_map,
            interact=True,
            require_trainer_ready=False,
            avoid_trainer_sight_lines=False,
            interaction_gap=1,
            chunk_steps=6,
            transition_frames=20,
            verified_defeated_trainer_local_ids=verified_defeated_trainer_local_ids,
        )
        state = interaction["state"]
        pages: list[str] = []
        remaining_frames = max_wait_frames
        stable = 0
        previous_key: tuple[Any, ...] | None = None
        while remaining_frames > 0:
            if state.get("battle", {}).get("active"):
                raise RuntimeError("transit interrupted by an unexpected battle")
            if state.get("mode") == "dialogue":
                if len(pages) >= max_pages:
                    raise RuntimeError("transit dialogue exceeded max_pages")
                pages.extend(self.advance_dialogue(max_pages=max_pages - len(pages)))
                state = self.observe()
                stable = 0
                previous_key = None
                continue
            map_state = state.get("map") or {}
            key = (
                map_state.get("group"), map_state.get("number"),
                map_state.get("x"), map_state.get("y"), state.get("mode"),
            )
            if state.get("mode") == "overworld" and key == previous_key:
                stable += 1
            else:
                stable = 0
            if state.get("mode") == "overworld" and stable >= stable_reads:
                break
            step = min(wait_chunk_frames, remaining_frames)
            self.gba.wait_frames(step)
            remaining_frames -= step
            previous_key = key
            state = self.observe()
        else:
            raise RuntimeError("transit did not reach a stable overworld destination")

        final_map_state = state.get("map") or {}
        final_map = (final_map_state.get("group"), final_map_state.get("number"))
        if expected_destination is not None:
            expected_destination = (int(expected_destination[0]), int(expected_destination[1]))
            if final_map != expected_destination:
                raise RuntimeError(
                    f"transit reached {final_map}, expected {expected_destination}"
                )
        return {
            "verified": final_map != source_map or bool(pages),
            "source_map": source_map,
            "selected": selected,
            "interaction": {
                "reason": interaction.get("reason"),
                "approach": interaction.get("approach"),
                "replans": interaction.get("replans"),
            },
            "dialogue": pages,
            "destination": final_map,
            "state": state,
        }

    def travel_live_transition(
        self,
        *,
        direction: str | None = None,
        destination: tuple[int, int] | None = None,
        max_candidates: int = 24,
        grass_penalty: int = 100,
        verified_defeated_trainer_local_ids: set[int] | None = None,
        allow_damaged_trainer_sight_lines: bool = False,
    ) -> dict[str, Any]:
        """Select one decoded map connection and verify the loaded destination.

        The connection record identifies the edge and destination, but the
        exact crossing tile is map-specific.  Candidate edge tiles are ranked
        by a live-grid path from the current position; each attempted crossing
        is verified by the authoritative SaveBlock map group/number.
        """
        from games.run_and_bun.live_map import read_live_connections, read_live_map

        if max_candidates < 1:
            raise ValueError("max_candidates must be >= 1")
        state = self.observe()
        map_state = state.get("map") or {}
        source_map = (map_state.get("group"), map_state.get("number"))
        if None in source_map:
            raise RuntimeError("cannot select a map transition without a loaded map id")
        source_map = (int(source_map[0]), int(source_map[1]))
        normalized_direction = direction.lower() if direction is not None else None
        if normalized_direction not in {None, "north", "south", "west", "east"}:
            raise ValueError(f"unsupported map transition direction: {direction!r}")
        if destination is not None:
            destination = (int(destination[0]), int(destination[1]))

        connections = [
            connection
            for connection in read_live_connections(self.gba)
            if connection.direction in {"north", "south", "west", "east"}
            and (normalized_direction is None or connection.direction == normalized_direction)
            and (destination is None or connection.destination == destination)
        ]
        if not connections:
            raise ValueError(
                f"no matching live map connection from {source_map}: "
                f"direction={normalized_direction!r}, destination={destination!r}"
            )
        if len(connections) > 1:
            raise ValueError("map transition selector is ambiguous; specify direction or destination")
        connection = connections[0]
        live = read_live_map(self.gba)
        current = (int(map_state["x"]), int(map_state["y"]))
        edge = connection.direction
        input_direction = {"north": "UP", "south": "DOWN", "west": "LEFT", "east": "RIGHT"}[edge]
        if edge == "north":
            edge_positions = [(x, 0) for x in range(live.active_width)]
        elif edge == "south":
            edge_positions = [(x, live.active_height - 1) for x in range(live.active_width)]
        elif edge == "west":
            edge_positions = [(0, y) for y in range(live.active_height)]
        else:
            edge_positions = [(live.active_width - 1, y) for y in range(live.active_height)]

        candidates: list[tuple[int, tuple[int, int], list[str]]] = []
        for position in edge_positions:
            if not live.walkable(*position):
                continue
            try:
                path = live.path_to(
                    current,
                    position,
                    allow_nonwalkable_start=True,
                    grass_penalty=grass_penalty,
                )
            except ValueError:
                continue
            candidates.append((len(path), position, path))
        candidates.sort(key=lambda item: (item[0], item[1][1], item[1][0]))
        if not candidates:
            raise RuntimeError(f"no reachable {edge} edge tile for map connection {connection.as_dict()}")

        attempts: list[dict[str, Any]] = []
        for path_steps, position, path in candidates[:max_candidates]:
            before = self.observe()
            before_map_state = before.get("map") or {}
            before_map = (before_map_state.get("group"), before_map_state.get("number"))
            if before_map != source_map:
                raise RuntimeError(f"map changed before transition attempt: {before_map} != {source_map}")
            if (before_map_state.get("x"), before_map_state.get("y")) != position:
                self.follow_live_path_adaptive(
                    position,
                    expected_map=source_map,
                    chunk_steps=6,
                    max_replans=32,
                    grass_penalty=grass_penalty,
                    verified_defeated_trainer_local_ids=verified_defeated_trainer_local_ids,
                    allow_damaged_trainer_sight_lines=allow_damaged_trainer_sight_lines,
                )
            result = self.follow_route(
                [input_direction],
                transition_frames=120,
                verified_defeated_trainer_local_ids=verified_defeated_trainer_local_ids,
                allow_damaged_trainer_sight_lines=allow_damaged_trainer_sight_lines,
            )
            after = result["state"]
            after_map_state = after.get("map") or {}
            actual_map = (after_map_state.get("group"), after_map_state.get("number"))
            attempt = {
                "edge": edge,
                "coordinate": position,
                "path_steps": path_steps,
                "actual_map": actual_map,
                "actual_position": (after_map_state.get("x"), after_map_state.get("y")),
                "mode": after.get("mode"),
            }
            attempts.append(attempt)
            if actual_map == connection.destination:
                return {
                    "verified": True,
                    "source_map": source_map,
                    "connection": connection.as_dict(),
                    "attempts": attempts,
                    "state": after,
                }
            if actual_map != source_map:
                raise RuntimeError(
                    f"unexpected map transition {actual_map}; expected {connection.destination}"
                )

        return {
            "verified": False,
            "source_map": source_map,
            "connection": connection.as_dict(),
            "attempts": attempts,
            "state": self.observe(),
        }

    def inventory(self) -> dict[str, Any]:
        """Decode the current bag pockets directly from SaveBlock1 RAM."""
        from games.run_and_bun.inventory import read_inventory

        return read_inventory(self.gba)

    @staticmethod
    def health_preflight(observation: dict[str, Any]) -> dict[str, Any]:
        """Return the compact party-health gate used before trainer contact.

        A trainer is still a required objective; this gate only prevents
        walking into its sight line with a damaged or fainted party.  Party
        identity comes from the live party structs, not battle-slot order.
        """
        mons = []
        for entry in observation.get("party", {}).get("mons", []) or []:
            if not entry.get("present", True):
                continue
            mon = dict(entry.get("state", entry))
            mon.setdefault("slot", entry.get("slot"))
            mons.append(mon)
        damaged = [
            {
                "slot": mon.get("slot"),
                "species": mon.get("species"),
                "nickname": mon.get("nickname"),
                "hp": mon.get("current_hp", 0),
                "max_hp": mon.get("max_hp", 0),
            }
            for mon in mons
            if mon.get("current_hp", 0) != mon.get("max_hp", 0)
        ]
        fainted = [mon for mon in damaged if mon["hp"] <= 0]
        return {
            "ready": bool(mons) and not damaged,
            "party_count": len(mons),
            "damaged": damaged,
            "fainted": fainted,
            "reason": "ready" if mons and not damaged else "healing_required",
        }

    def trainer_preflight(self, target: Any | None = None) -> dict[str, Any]:
        """Gate trainer contact on health and any known hard-fight strategy."""
        report = self.health_preflight(self.observe())
        try:
            pockets = self.inventory().get("pockets", {})
            report["medicine"] = {
                name: pockets.get(name, [])
                for name in ("runbun_medicine", "ui_medicine")
            }
        except Exception as error:
            report["medicine"] = None
            report["inventory_error"] = f"{type(error).__name__}: {error}"
        if target is not None:
            try:
                from games.run_and_bun.trainer_database import is_classified_hard, lookup_trainer
                from games.run_and_bun.battle_review import battle_continuation_gate

                continuation = battle_continuation_gate()
                report["battle_continuation_gate"] = continuation
                if not continuation["allowed"]:
                    report["ready"] = False
                    report["reason"] = continuation["reason"]
                    return report

                map_group, map_number = target.map_id
                trainer = lookup_trainer(
                    map_group=map_group,
                    map_number=map_number,
                    local_id=target.local_id,
                    graphics_id=getattr(target, "graphics_id", None),
                    script_address=getattr(target, "script_address", None),
                )
                report["trainer"] = trainer
                classified_hard = is_classified_hard(trainer["key"])
                report["classification"] = "hard" if classified_hard else "unclassified"

                # The review/clone gate belongs to hard-fight engagement, not
                # ordinary travel past unrelated or unknown easy trainers.
                # ponytail: keep the global gate out of the navigation path;
                # classify first, then enforce it only for a known hard fight.
                if classified_hard:
                    from games.run_and_bun.battle_review import clone_review_gate

                    review_gate = clone_review_gate(trainer_key=trainer["key"])
                    report["review_gate"] = review_gate
                    if not review_gate["allowed"]:
                        report["ready"] = False
                        report["reason"] = "agent_battle_review_required"
                        return report
                if not trainer["found"] and classified_hard:
                    report["ready"] = False
                    report["reason"] = "hard_fight_trainer_record_required"
                elif not trainer["found"]:
                    report["reason"] = "unclassified_trainer_allowed"
                elif not trainer["trusted"]:
                    report["ready"] = False
                    report["reason"] = "trainer_identity_mismatch"
                else:
                    record = trainer["record"] or {}
                    battle = record.get("battle", {})
                    strategy = record.get("strategy", {})
                    classified_hard = classified_hard or battle.get("hard_fight") is True
                    report["classification"] = "hard" if classified_hard else "easy"
                    if classified_hard and strategy.get("status") != "ready":
                        report["ready"] = False
                        report["reason"] = "hard_fight_plan_required"
                    elif classified_hard:
                        profile_name = record.get("policy_profile")
                        if not isinstance(profile_name, str):
                            report["ready"] = False
                            report["reason"] = "hard_fight_policy_profile_missing"
                        else:
                            try:
                                from games.run_and_bun.battle_policy import load_profile, load_strategies, policy_bundle_hash
                                from games.run_and_bun.battle_review import live_qualification_gate

                                profile_path = Path(__file__).resolve().parents[1] / profile_name
                                behavior_hash = policy_bundle_hash(load_profile(profile_path), load_strategies())
                                qualification = live_qualification_gate(
                                    behavior_hash,
                                    trainer_key=trainer["key"],
                                )
                                report["qualification"] = qualification
                                report["policy_profile"] = profile_name
                                report["behavior_hash"] = behavior_hash
                                if not qualification["allowed"]:
                                    report["ready"] = False
                                    report["reason"] = "three_clean_clone_wins_required"
                            except Exception as error:
                                report["ready"] = False
                                report["reason"] = "hard_fight_qualification_error"
                                report["qualification_error"] = f"{type(error).__name__}: {error}"
            except Exception as error:
                report["ready"] = False
                report["reason"] = "trainer_database_error"
                report["trainer_error"] = f"{type(error).__name__}: {error}"
        if report["ready"]:
            report["action"] = "engage_trainer"
        elif report["reason"] == "healing_required":
            report["action"] = "heal_before_engaging"
        elif report["reason"] == "trainer_database_unknown":
            report["action"] = "checkpoint_then_reconnaissance"
        else:
            report["action"] = "prepare_and_validate_strategy"
        return report

    def _field_bag_task(self) -> dict[str, Any]:
        """Return the live Bag task that owns pocket/item cursors."""
        tasks = self.gba.inspect_tasks().get("tasks", [])
        for task in tasks:
            if self._is_field_bag_task(task):
                return task
        raise RuntimeError("field_bag_not_open: Bag task is not active")

    @staticmethod
    def _is_field_bag_task(task: dict[str, Any]) -> bool:
        """Recognize the Bag owner from its verified cursor payload."""
        data = task.get("data") or []
        # The task function ID is allocator/state dependent in this hack
        # (the same Bag used 23472 outdoors and 10876 in the Center).
        # Its cursor payload is stable: two ROM script pointers, the pocket
        # selector at +6, menu mode 8 at +9, and item cursor +13.
        return bool(
            task.get("active")
            and len(data) >= 14
            and data[1] == 512
            and data[3] == 2077
            and data[4] == 51445
            and data[5] == 2077
            and 0 <= data[6] <= 4
            and data[9] == 8
        )

    def close_field_bag(self, *, max_layers: int = 3, wait_frames: int = 90) -> dict[str, Any]:
        """Close a verified Bag/Start-menu stack without selecting an item."""
        if max_layers < 1 or wait_frames < 1:
            raise ValueError("close_field_bag bounds must be positive")
        before = self.observe()
        before_ui = before.get("ui", {})
        if (
            not before_ui.get("field_bag_open")
            and not before_ui.get("field_start_menu_open")
            and not before_ui.get("field_message_box_mode")
        ):
            raise RuntimeError("field_ui_not_open")
        closed = 0
        for _ in range(max_layers):
            state = self.observe()
            bag_open = bool(state.get("ui", {}).get("field_bag_open"))
            start_open = self._field_start_menu_open()
            if not bag_open and not start_open:
                if state.get("mode") == "overworld" and state.get("ui", {}).get("field_message_box_mode") == 0:
                    return {"closed_layers": closed, "state": state}
                raise RuntimeError(f"field_menu_cleanup_stopped_in_mode: {state.get('mode')}")
            self.gba.press("B", frames=3)
            self.gba.wait_frames(wait_frames)
            closed += 1
        final = self.observe()
        if (
            final.get("ui", {}).get("field_bag_open")
            or self._field_start_menu_open()
            or final.get("mode") != "overworld"
            or final.get("ui", {}).get("field_message_box_mode") != 0
        ):
            raise RuntimeError("field_bag_cleanup_failed")
        return {"closed_layers": closed, "state": final}

    def _field_start_menu_open(self) -> bool:
        """Whether the verified Start-menu input owner is still active."""
        return any(
            task.get("active") and task.get("function_address") == 0x080BD7B9
            for task in self.gba.inspect_tasks().get("tasks", [])
        )

    def _move_field_cursor(self, address: int, target: int, *, max_steps: int = 8) -> int:
        """Move a small RAM-backed vertical cursor and verify every step."""
        for _ in range(max_steps):
            current = self.gba.read8(address)
            if current == target:
                return current
            if current > target:
                direction = "UP"
            else:
                direction = "DOWN"
            self.gba.press(direction, frames=3)
            self.gba.wait_frames(30)
        final = self.gba.read8(address)
        if final != target:
            raise RuntimeError(f"field_cursor_failed: address={address:#x} target={target} got={final}")
        return final

    @staticmethod
    def _active_field_page_texts(observation: dict[str, Any]) -> tuple[str, ...]:
        """Return text belonging to a currently active field printer.

        The custom decoder retains old printer pages for forensics and may
        expose one of those as ``text.current`` while a newer field printer
        is drawing the real page.  Menu input must be gated by the active
        printer, otherwise an old ``A`` boundary can consume another item.
        """
        text = observation.get("text") or {}
        active_addresses = {
            printer.get("address")
            for printer in text.get("printers", [])
            if printer.get("active") and printer.get("window_id") in {5, 6}
        }
        pages: list[str] = []
        for entry in text.get("pages", []):
            printer = entry.get("printer") or {}
            if printer.get("address") in active_addresses:
                value = (entry.get("page") or {}).get("text") or ""
                if value:
                    pages.append(value)
        if not pages:
            pages.extend(
                context.get("text", "")
                for context in text.get("battle_printers", [])
                if context.get("text")
            )
        if not pages:
            current = (text.get("current") or {}).get("text") or ""
            if current:
                pages.append(current)
        return tuple(dict.fromkeys(pages))

    @classmethod
    def _move_learning_page_texts(cls, observation: dict[str, Any]) -> tuple[str, ...]:
        """Prefer the input-owning printer, with a bounded current-page fallback.

        During the first level-up page the field engine can briefly expose no
        window-5/6 entry even though the current RAM text is the live
        "already knows four moves" acknowledgement.  The fallback is limited
        to move-learning callers and never treats arbitrary overworld text as
        an input boundary.
        """
        pages = tuple(" ".join(page.split()) for page in cls._active_field_page_texts(observation))
        if pages:
            return pages
        current = (observation.get("text") or {}).get("current") or {}
        value = current.get("text") or ""
        if value and any(
            marker in value
            for marker in (
                "wants to learn",
                "already knows four moves",
                "Should a move be deleted",
                "Which move should be forgotten",
                "Poof!",
                "forgot how to",
                "learned",
            )
        ):
            return (" ".join(value.split()),)
        return ()

    @classmethod
    def _field_move_learning_pending(cls, observation: dict[str, Any]) -> bool:
        """Detect move-learning ownership before any automatic menu cleanup."""
        rendered = "\n".join(cls._move_learning_page_texts(observation))
        phrases = (
            "wants to learn",
            "already knows four moves",
            "Should a move be deleted",
            "Which move should be forgotten",
            "Stop trying to teach",
        )
        # Mode 33 is the verified five-row move-forget summary screen.
        return observation.get("ui", {}).get("field_message_box_mode") == 33 or any(
            phrase in rendered for phrase in phrases
        )

    def resolve_field_move_learning(
        self,
        *,
        target_species: int,
        forget_slot: int,
        expected_move_id: int | None = None,
        max_frames: int = 1800,
    ) -> dict[str, Any]:
        """Resolve a pending four-move learn screen with RAM verification.

        Endless Candy can pause on the standard five-row forget screen.  The
        caller supplies the strategic replacement slot; this method never
        guesses which existing move to discard and verifies the resulting
        party move tuple before closing the reusable item target screen.
        """
        if not 0 <= forget_slot < 4:
            raise ValueError("forget_slot must be in 0..3")
        if max_frames < 1:
            raise ValueError("max_frames must be positive")
        start = self.observe()
        target = next(
            (
                mon
                for mon in start.get("party", {}).get("mons", [])
                if mon.get("present") and mon.get("state", {}).get("species") == target_species
            ),
        )
        if target is None:
            raise ValueError(f"move_learning_target_species_not_unique: {target_species}")
        old_moves = tuple(target["state"].get("moves", ()))
        elapsed = 0
        while elapsed <= max_frames:
            state = self.observe()
            field_mode = state.get("ui", {}).get("field_message_box_mode")
            if field_mode == 33:
                break
            if state.get("mode") == "overworld":
                raise RuntimeError("move_learning_not_pending")
            # Pages leading to the forget screen are ordinary field text. A
            # single verified A advances one page; no blind repeat is used.
            pages = self._move_learning_page_texts(state)
            if any(
                marker in page
                for page in pages
                for marker in (
                    "wants to learn",
                    "already knows four moves",
                    "Should a move be deleted",
                )
            ):
                self.gba.press("A", frames=3)
                self.gba.wait_frames(120)
                elapsed += 120
            else:
                if any("Use on which Pokémon?" in page for page in pages):
                    raise RuntimeError("move_learning_target_prompt_before_forget_screen")
                self.gba.wait_frames(30)
                elapsed += 30
        if self.gba.read8(FIELD_MESSAGE_BOX_MODE) != 33:
            raise TimeoutError("move_learning_forget_screen_not_ready")

        # Mode 33 is shared by the question page and the five-row selector.
        # The question must be acknowledged before directional input is legal.
        question = any(
            "Which move should be forgotten" in page
            for page in self._active_field_page_texts(self.observe())
        )
        if question:
            self.gba.press("A", frames=3)
            self.gba.wait_frames(180)

        # The five-row move-forget task uses a separate cursor byte from the
        # ordinary field party cursor. Its initial position is the first move;
        # verify the requested slot by checking the resulting move tuple
        # below, rather than trusting an unrelated menu cursor address.
        for _ in range(forget_slot):
            self.gba.press("DOWN", frames=3)
            self.gba.wait_frames(60)
        selected = forget_slot
        self.gba.press("A", frames=3)
        self.gba.wait_frames(180)
        elapsed += 180
        new_moves: tuple[int, ...] = old_moves
        while elapsed <= max_frames:
            state = self.observe()
            target = next(
                (
                    mon
                    for mon in state.get("party", {}).get("mons", [])
                    if mon.get("present") and mon.get("state", {}).get("species") == target_species
                ),
                None,
            )
            if target is not None:
                new_moves = tuple(target["state"].get("moves", ()))
                if (
                    new_moves != old_moves
                    and new_moves[forget_slot] != old_moves[forget_slot]
                    and (expected_move_id is None or expected_move_id in new_moves)
                ):
                    break

            # The ROM applies the replacement only after a short sequence of
            # field-message pages ("Poof!", "forgot ...", "And...", then
            # "learned ..."). Waiting for the party tuple without advancing
            # those pages stalls on the correct acknowledgement boundary.
            # Advance only a verified move-learning page; an A on the reusable
            # target prompt would consume another Endless Candy.
            pages = self._move_learning_page_texts(state)
            if any("Use on which Pokémon?" in page for page in pages):
                raise RuntimeError("move_learning_target_prompt_before_party_ack")
            ready = any(
                marker in page
                for page in pages
                for marker in (
                    "Poof!",
                    "forgot how to",
                    "And…",
                    "And...",
                    "learned",
                )
            )
            if ready:
                self.gba.press("A", frames=3)
                self.gba.wait_frames(120)
                elapsed += 120
            else:
                self.gba.wait_frames(30)
                elapsed += 30
        if (
            new_moves == old_moves
            or new_moves[forget_slot] == old_moves[forget_slot]
            or (expected_move_id is not None and expected_move_id not in new_moves)
            or (expected_move_id is not None and new_moves[forget_slot] != expected_move_id)
        ):
            raise RuntimeError(
                f"move_learning_party_ack_missing: old={old_moves} new={new_moves} expected={expected_move_id}"
            )

        # Finish only the remaining learn-message pages. Stop at the reusable
        # target prompt; pressing A there would silently use another Candy.
        for _ in range(8):
            state = self.observe()
            if state.get("mode") == "overworld":
                break
            pages = self._move_learning_page_texts(state)
            if any("Use on which Pokémon?" in page for page in pages):
                break
            # Only these pages belong to the current replacement.  A generic
            # state-0/state-2 check is unsafe here: after the learned page the
            # field task may expose another Candy target layer with a fresh
            # printer, and A would consume the item again.
            learning_page = any(
                marker in page
                for page in pages
                for marker in (
                    "Poof!",
                    "forgot how to",
                    "And…",
                    "And...",
                    "learned",
                )
            )
            if not learning_page:
                # Let the target-layer task settle; it will be closed by the
                # B-only cleanup below. Never guess an A on an unknown mode.
                self.gba.wait_frames(30)
                if pages:
                    break
                continue
            self.gba.press("A", frames=3)
            self.gba.wait_frames(90)
        for _ in range(6):
            state = self.observe()
            try:
                bag_open = self._field_bag_task() is not None
            except RuntimeError:
                bag_open = False
            start_open = self._field_start_menu_open()
            if state.get("mode") == "overworld" and not bag_open and not start_open:
                break
            pages = self._move_learning_page_texts(state)
            field_mode = state.get("ui", {}).get("field_message_box_mode")
            if field_mode == 33 or any(
                phrase in page
                for page in pages
                for phrase in (
                    "wants to learn",
                    "already knows four moves",
                    "Should a move be deleted",
                    "Which move should be forgotten",
                    "Stop trying to teach",
                )
            ):
                raise RuntimeError("nested_move_learning_prompt_after_replacement")
            self.gba.press("B", frames=3)
            self.gba.wait_frames(120)
        final = self.observe()
        try:
            bag_open = self._field_bag_task() is not None
        except RuntimeError:
            bag_open = False
        if final.get("mode") != "overworld" or bag_open or self._field_start_menu_open():
            raise RuntimeError("move_learning_cleanup_failed")
        return {
            "target_species": target_species,
            "forgotten_slot": forget_slot,
            "selected_cursor": selected,
            "old_moves": old_moves,
            "new_moves": new_moves,
            "expected_move_id": expected_move_id,
            "state": final,
        }

    def use_field_item(
        self,
        item_name: str,
        *,
        target_slot: int | None = None,
        target_species: int | None = None,
        target_nickname: str | None = None,
    ) -> dict[str, Any]:
        """Use a verified field item through RAM-backed Bag/party cursors.

        Run & Bun's current field Bag exposes Endless Candy in Key Items and
        Potion in the Medicine pocket. The operation intentionally rejects
        unknown names instead of selecting an arbitrary row. Party selection
        is by identity when a species or nickname is supplied, then the live
        field cursor is verified before A.
        """
        item_key = item_name.casefold()
        if item_key not in {"endless candy", "potion"}:
            raise ValueError(f"unsupported field item: {item_name!r}")
        if sum(value is not None for value in (target_slot, target_species, target_nickname)) != 1:
            raise ValueError("field item target requires exactly one of slot, species, or nickname")

        before = self.observe()
        if before.get("mode") != "overworld":
            raise RuntimeError(f"field_item_unavailable_in_mode: {before.get('mode')}")
        party = before.get("party", {}).get("mons", [])
        if target_species is not None:
            matches = [
                mon.get("slot")
                for mon in party
                if mon.get("present") and mon.get("state", {}).get("species") == target_species
            ]
            if len(matches) != 1:
                raise ValueError(f"field_item_target_species_not_unique: {target_species}")
            target_slot = matches[0]
        elif target_nickname is not None:
            matches = [
                mon.get("slot")
                for mon in party
                if mon.get("present") and mon.get("state", {}).get("nickname", "").casefold() == target_nickname.casefold()
            ]
            if len(matches) != 1:
                raise ValueError(f"field_item_target_nickname_not_unique: {target_nickname!r}")
            target_slot = matches[0]
        if target_slot is None or target_slot < 0 or target_slot >= len(party):
            raise ValueError(f"field_item_target_slot_invalid: {target_slot}")
        target_mon = next((mon for mon in party if mon.get("slot") == target_slot), None)
        if not target_mon or not target_mon.get("present"):
            raise ValueError(f"field_item_target_slot_invalid: {target_slot}")
        hp_before = target_mon.get("state", {}).get("current_hp")
        item_cursor = 0
        if item_key == "potion":
            medicine = self.inventory().get("pockets", {}).get("runbun_medicine", [])
            potion = next(
                (item for item in medicine if item.get("item_id") == 28 and item.get("quantity", 0) > 0),
                None,
            )
            if potion is None:
                raise RuntimeError("potion_unavailable")

        # A prior use can return visually to the overworld one frame before
        # the Bag task is destroyed. Clear that stale task with B before START
        # would otherwise be ignored by the field engine.
        for _ in range(3):
            try:
                self._field_bag_task()
            except RuntimeError:
                break
            if self.gba.read8(FIELD_MESSAGE_BOX_MODE) != 0:
                raise RuntimeError("field_item_menu_already_open")
            self.gba.press("B", frames=3)
            self.gba.wait_frames(90)
        if self._field_start_menu_open():
            self.gba.press("B", frames=3)
            self.gba.wait_frames(90)

        # Open Start -> Bag. The live menu has three entries and Bag is cursor 2.
        menu_ready = False
        for _ in range(2):
            self.gba.wait_frames(30)
            self.gba.press("START", frames=3)
            self.gba.wait_frames(60)
            if self.gba.read8(FIELD_MESSAGE_BOX_MODE) == 2:
                menu_ready = True
                break
        if not menu_ready:
            raise RuntimeError("field_start_menu_not_ready")
        self._move_field_cursor(FIELD_MENU_CURSOR, 2, max_steps=4)
        self.gba.press("A", frames=3)
        self.gba.wait_frames(180)

        # Bag opens on Poké Balls (2) in this save. Read the task instead of
        # assuming that state; RIGHT advances to Key Items (4).
        desired_pocket = 4 if item_key == "endless candy" else 1
        for _ in range(4):
            task = self._field_bag_task()
            pocket = task["data"][6]
            if pocket == desired_pocket:
                break
            direction = "LEFT" if pocket > desired_pocket else "RIGHT"
            self.gba.press(direction, frames=3)
            self.gba.wait_frames(60)
        task = self._field_bag_task()
        if task["data"][6] != desired_pocket:
            raise RuntimeError(f"field_bag_pocket_failed: expected={desired_pocket} got={task['data'][6]}")

        # Reset and select the RAM-backed item slot; Run & Bun preserves sparse
        # medicine slots, so Potion is not necessarily cursor zero.
        for _ in range(24):
            task = self._field_bag_task()
            cursor = task["data"][13]
            if cursor == 0:
                break
            self.gba.press("UP", frames=3)
            self.gba.wait_frames(30)
        task = self._field_bag_task()
        if task["data"][13] != 0:
            raise RuntimeError(f"field_bag_item_cursor_failed: expected=0 got={task['data'][13]}")
        for _ in range(item_cursor):
            self.gba.press("DOWN", frames=3)
            self.gba.wait_frames(30)
        task = self._field_bag_task()
        if task["data"][13] != item_cursor:
            raise RuntimeError(
                f"field_bag_item_cursor_failed: expected={item_cursor} got={task['data'][13]}"
            )

        # One A selects the row; the second confirms Use and opens the party
        # target prompt. The field prompt exposes its own RAM cursor.
        self.gba.press("A", frames=3)
        # Medicine opens its action submenu through a slower field task than
        # Key Items; let the task acknowledge the first A before confirming.
        self.gba.wait_frames(120 if item_key == "potion" else 30)
        # The item-action cursor shares the verified field-menu cursor and can
        # retain a stale Toss/Cancel position from an earlier Bag use.
        self._move_field_cursor(FIELD_MENU_CURSOR, 0, max_steps=4)
        self.gba.press("A", frames=3)
        self.gba.wait_frames(120)
        target_mode = self.gba.read8(FIELD_MESSAGE_BOX_MODE)
        # The same live party target screen rotates through party_menu (11),
        # party_prompt (13), field_item_target (15), and the five-member party
        # transition (19) as its printer/task settles. All were verified
        # against the visible Endless Candy
        # target screen; requiring only one exact sampling instant is brittle.
        if target_mode not in {11, 13, 15, 19}:
            raise RuntimeError(f"field_item_target_prompt_missing: mode={target_mode}")
        selected_cursor = self._move_field_cursor(FIELD_PARTY_CURSOR, target_slot)
        self.gba.press("A", frames=3)
        self.gba.wait_frames(300)
        effect_state = self.observe()
        text = ((effect_state.get("text") or {}).get("current") or {}).get("text")
        # Endless Candy leaves a reusable target screen, then the Bag and
        # Start layers remain stacked behind it. Close one layer at a time and
        # re-observe after every B. A level-up can branch into move learning;
        # continuing to spam B there would silently decline a move.
        move_learning_pending = self._field_move_learning_pending(effect_state)
        for _ in range(6):
            if move_learning_pending:
                break
            self.gba.press("B", frames=3)
            self.gba.wait_frames(90)
            layer_state = self.observe()
            move_learning_pending = self._field_move_learning_pending(layer_state)
            bag_open = True
            try:
                self._field_bag_task()
            except RuntimeError:
                bag_open = False
            if (
                layer_state.get("mode") == "overworld"
                and not bag_open
                and not self._field_start_menu_open()
            ):
                break
        # Field scripts keep a short post-menu lock even after mode returns
        # to overworld. Let the RAM task settle before handing control back;
        # otherwise the first navigation chunk can be silently ignored.
        self.gba.wait_frames(150)
        after = self.observe()
        after_target = next(
            (mon for mon in after.get("party", {}).get("mons", []) if mon.get("slot") == target_slot),
            None,
        )
        hp_after = (after_target or {}).get("state", {}).get("current_hp")
        if item_key == "potion" and (hp_before is None or hp_after is None or hp_after <= hp_before):
            raise RuntimeError(f"potion_no_hp_change: before={hp_before} after={hp_after}")
        compact_party = [
            {
                "slot": mon.get("slot"),
                "species": mon.get("state", {}).get("species"),
                "level": mon.get("state", {}).get("level"),
                "hp": mon.get("state", {}).get("current_hp"),
                "max_hp": mon.get("state", {}).get("max_hp"),
            }
            for mon in after.get("party", {}).get("mons", [])
        ]
        return {
            "item": item_name,
            "target_slot": target_slot,
            "target_species": target_mon.get("state", {}).get("species"),
            "cursor": selected_cursor,
            "text": text,
            "hp_before": hp_before,
            "hp_after": hp_after,
            "move_learning_pending": move_learning_pending,
            "state": {
                "frame": after.get("frame"),
                "mode": after.get("mode"),
                "map": after.get("map"),
                "field_message_box_mode": after.get("ui", {}).get("field_message_box_mode"),
                "party": compact_party,
            },
        }

    def progress(self) -> dict[str, Any]:
        """Return raw progression flag IDs and non-zero vars from SaveBlock1."""
        from games.run_and_bun.inventory import read_progress

        return read_progress(self.gba)

    def find_npc(
        self,
        *,
        slot: int | None = None,
        local_id: int | None = None,
        graphics_id: int | None = None,
        predicate: Any = None,
        nearest: bool = True,
    ) -> dict[str, Any] | None:
        """Find an active NPC by runtime identity and current map position.

        ``local_id`` is the strongest map-local identity. ``graphics_id`` is a
        useful fallback when a target's event ID is not yet known. With no
        filter this returns the nearest non-player object, which is useful for
        discovery but intentionally not used by the playthrough controller.
        """
        from games.run_and_bun.objects import read_live_objects, select_live_object

        state = self.observe()
        map_state = state.get("map") or {}
        map_id = (map_state.get("group"), map_state.get("number"))
        if None in map_id:
            raise RuntimeError("cannot seek an NPC without a loaded map")
        objects = read_live_objects(self.gba)
        selected = select_live_object(
            objects,
            map_id=map_id,  # type: ignore[arg-type]
            slot=slot,
            local_id=local_id,
            graphics_id=graphics_id,
            predicate=predicate,
            nearest_to=(map_state.get("x"), map_state.get("y")) if nearest else None,
        )
        return selected.as_dict() if selected is not None else None

    @staticmethod
    def _cardinal_direction(dx: int, dy: int) -> str:
        """Convert a cardinal delta into the corresponding GBA input key."""
        unit = (0 if dx == 0 else (1 if dx > 0 else -1), 0 if dy == 0 else (1 if dy > 0 else -1))
        directions = {
            (0, -1): "UP",
            (1, 0): "RIGHT",
            (0, 1): "DOWN",
            (-1, 0): "LEFT",
        }
        try:
            return directions[unit]
        except KeyError as exc:
            raise ValueError(f"expected a cardinal delta, got {(dx, dy)}") from exc

    @staticmethod
    def _trainer_facing_delta(direction: int) -> tuple[int, int] | None:
        # Gen III object-event directions: 1 down, 2 up, 3 left, 4 right.
        return {1: (0, 1), 2: (0, -1), 3: (-1, 0), 4: (1, 0)}.get(direction)

    @classmethod
    def _trainer_front_range(cls, current: tuple[int, int], target: Any) -> int | None:
        """Return range only when a trainer is in its facing ray."""
        if not getattr(target, "trainer_type", 0):
            return None
        facing = cls._trainer_facing_delta(getattr(target, "facing_direction", 0))
        if facing is None:
            return None
        dx = current[0] - target.current_x
        dy = current[1] - target.current_y
        if (dx, dy) == (0, 0) or (dx, dy) != (facing[0] * abs(dx or dy), facing[1] * abs(dx or dy)):
            return None
        distance = abs(dx) + abs(dy)
        return distance if 1 <= distance <= 2 else None

    def _npc_approach_target(
        self,
        current: tuple[int, int],
        target: Any,
        objects: list[Any],
        *,
        grass_penalty: int,
        interaction_gap: int,
        prefer_open_gap: bool = False,
        avoid_trainer_sight_lines: bool = True,
        ignored_trainer_ids: set[int] | None = None,
    ) -> tuple[tuple[int, int], list[str], int]:
        """Choose the cheapest reachable tile from which an object can talk.

        Service NPCs such as the Pokémon Center nurse stand behind a
        one-tile counter. Their event object is two tiles from the player, so
        requiring direct adjacency would incorrectly declare them unreachable.
        A gap of two is accepted only when the intervening tile is
        non-walkable, which preserves ordinary collision safety.
        """
        from games.run_and_bun.live_map import read_live_map
        from games.run_and_bun.objects import object_occupied_edges

        if interaction_gap < 1:
            raise ValueError("interaction_gap must be >= 1")
        live = read_live_map(self.gba)
        blocked = object_occupied_edges(objects)
        # The NPC seeker has its own path builder, so merge the same
        # RAM/template-backed trainer sight exclusion used by the general
        # adaptive navigator.  Without this, approaching a stationary trainer
        # from the south can enter its ray before the final interaction tile.
        map_state = self.observe().get("map") or {}
        actual_map = (map_state.get("group"), map_state.get("number"))
        if avoid_trainer_sight_lines and None not in actual_map:
            blocked_tiles = self._trainer_sight_tiles(
                self.gba,
                (int(actual_map[0]), int(actual_map[1])),
                current=current,
                target=(target.current_x, target.current_y),
                ignored_local_ids=ignored_trainer_ids,
            )
        else:
            blocked_tiles = set()
        directions = ((0, -1), (1, 0), (0, 1), (-1, 0))
        candidates: list[tuple[int, int, int, int, tuple[int, int], list[str]]] = []
        for dx, dy in directions:
            for gap in range(1, interaction_gap + 1):
                approach = (target.current_x - dx * gap, target.current_y - dy * gap)
                if not (0 <= approach[0] < live.active_width and 0 <= approach[1] < live.active_height):
                    continue
                if not live.walkable(*approach):
                    continue
                through_block = False
                if gap > 1:
                    # Only cross a counter/wall gap; a two-tile range through
                    # open floor could select an unintended nearby object. An
                    # explicit interaction request may intentionally use the
                    # open trainer range; prefer that over a sign/counter tile.
                    between = (target.current_x - dx, target.current_y - dy)
                    if live.walkable(*between):
                        if not prefer_open_gap and not getattr(target, "trainer_type", 0):
                            continue
                    else:
                        through_block = True
                try:
                    path = live.path_to(
                        current,
                        approach,
                        blocked_edges=blocked,
                        blocked_tiles=blocked_tiles,
                        allow_nonwalkable_start=True,
                        grass_penalty=grass_penalty,
                    )
                except ValueError:
                    continue
                # Dijkstra's grass penalty is represented in the path choice,
                # and Manhattan distance breaks equal-cost ties without
                # screenshots.
                if getattr(target, "trainer_type", 0) and gap > 1:
                    # A side-adjacent tile is a valid talk boundary for a
                    # stationary trainer.  Only open two-tile shortcuts stay
                    # restricted to the trainer's facing ray; allowing a
                    # side tile here is what makes a sight-ray-safe approach
                    # possible when the front tile is intentionally blocked.
                    facing = self._trainer_facing_delta(getattr(target, "facing_direction", 0))
                    if facing is not None and (approach[0] - target.current_x, approach[1] - target.current_y) != (facing[0] * gap, facing[1] * gap):
                        continue
                candidates.append((int(through_block), len(path), gap, abs(approach[0] - current[0]) + abs(approach[1] - current[1]), approach, path))
        if not candidates:
            raise RuntimeError(
                f"no reachable interaction tile for NPC slot {target.slot} at {target.position}"
            )
        _, _, gap, _, approach, path = min(candidates, key=lambda item: (item[0], item[1], item[2], item[3], item[4]))
        return approach, path, gap

    @staticmethod
    def _npc_interaction_gap(
        current: tuple[int, int],
        target: Any,
        live: Any,
        *,
        max_gap: int,
        allow_open_gap: bool = False,
    ) -> int | None:
        """Return a cardinal interaction range, including a counter gap."""
        dx = target.current_x - current[0]
        dy = target.current_y - current[1]
        distance = abs(dx) + abs(dy)
        if distance < 1 or distance > max_gap or (dx and dy):
            return None
        if distance == 1:
            return 1
        if allow_open_gap:
            return distance
        # Emerald's counter interaction extension is vertical: the player
        # stands south of a service NPC with one counter tile between them.
        # Treating a horizontal wall as the same range made the seeker press
        # A at an unrelated blocked tile beside the utility NPC.
        if dx != 0 or dy >= 0:
            return None
        step_x = 0 if dx == 0 else (1 if dx > 0 else -1)
        step_y = 0 if dy == 0 else (1 if dy > 0 else -1)
        between = (current[0] + step_x, current[1] + step_y)
        if not live.walkable(*between):
            return distance
        return None

    def follow_live_path_to_npc(
        self,
        *,
        slot: int | None = None,
        local_id: int | None = None,
        graphics_id: int | None = None,
        predicate: Any = None,
        expected_map: tuple[int, int] | None = None,
        interact: bool = False,
        chunk_steps: int = 6,
        frames: int = 12,
        settle_frames: int = 4,
        transition_frames: int = 20,
        max_replans: int = 24,
        grass_penalty: int = 100,
        blocked_wait_frames: int = 8,
        interaction_gap: int = 2,
        require_trainer_ready: bool = True,
        avoid_trainer_sight_lines: bool = True,
        verified_defeated_trainer_local_ids: set[int] | None = None,
    ) -> dict[str, Any]:
        """Seek a live NPC, re-reading its position while walking.

        The target is selected by object-event identity, not by a guessed
        screen coordinate. Each short movement chunk re-reads the object
        table, blocks all occupied object tiles, and selects a fresh reachable
        approach tile. This handles wandering NPCs and scripted movement while
        retaining the grass-avoidance policy of the normal pathfinder.

        When interaction is requested, trainer targets are health-gated before
        movement begins. A failed gate returns a compact report so the caller
        can route to healing and then retry the same trainer identity.
        """
        from games.run_and_bun.objects import read_live_objects, select_live_object

        if max_replans < 1:
            raise ValueError("max_replans must be >= 1")
        if interaction_gap < 1:
            raise ValueError("interaction_gap must be >= 1")
        if not any(value is not None for value in (slot, local_id, graphics_id, predicate)):
            raise ValueError("NPC seeker needs slot, local_id, graphics_id, or predicate")

        actions: list[dict[str, Any]] = []
        last_state = self.observe()
        for attempt in range(max_replans):
            map_state = last_state.get("map") or {}
            actual_map = (map_state.get("group"), map_state.get("number"))
            if None in actual_map:
                raise RuntimeError("cannot seek an NPC without a loaded map")
            if expected_map is not None and actual_map != expected_map:
                raise RuntimeError(f"NPC seeker reached map {actual_map}, expected {expected_map}")
            if last_state.get("mode") != "overworld":
                return {
                    "state": last_state,
                    "target": None,
                    "actions": actions,
                    "replans": attempt,
                    "reason": "interrupted",
                }

            objects = read_live_objects(self.gba)
            current = (map_state.get("x"), map_state.get("y"))
            if None in current:
                raise RuntimeError("cannot seek an NPC without a player position")
            target = select_live_object(
                objects,
                map_id=actual_map,  # type: ignore[arg-type]
                slot=slot,
                local_id=local_id,
                graphics_id=graphics_id,
                predicate=predicate,
                nearest_to=current,  # type: ignore[arg-type]
            )
            if target is None:
                # Map connections can preserve an object's source-map bytes
                # while the player is already on the connected map.  When
                # the identity is present, active, visible, and physically
                # inside this freshly read grid, treat it as current-map
                # state instead of rejecting a valid trainer/NPC.
                target = select_live_object(
                    objects,
                    slot=slot,
                    local_id=local_id,
                    graphics_id=graphics_id,
                    predicate=predicate,
                    nearest_to=current,  # type: ignore[arg-type]
                )
            if target is None:
                # If the runtime object array is still from the source map,
                # use the loaded map's event templates for stationary actors.
                # This is especially important for trainers immediately after
                # a route connection, where their battle scripts are already
                # known even though gObjectEvents has not been rebuilt.
                from games.run_and_bun.objects import read_live_event_targets

                event_targets = read_live_event_targets(self.gba, map_id=actual_map)  # type: ignore[arg-type]
                target = select_live_object(  # type: ignore[assignment]
                    event_targets,
                    slot=slot,
                    local_id=local_id,
                    graphics_id=graphics_id,
                    predicate=predicate,
                    nearest_to=current,  # type: ignore[arg-type]
                )
            if target is None:
                raise RuntimeError(
                    f"target NPC not present on map {actual_map}: "
                    f"slot={slot} local_id={local_id} graphics_id={graphics_id}"
                )
            if interact and require_trainer_ready and getattr(target, "trainer_type", 0):
                preflight = self.trainer_preflight(target)
                if not preflight["ready"]:
                    return {
                        "state": last_state,
                        "target": target.as_dict(),
                        "actions": actions,
                        "replans": attempt,
                        "reason": "trainer_preflight_failed",
                        "preflight": preflight,
                    }
            from games.run_and_bun.live_map import read_live_map

            live = read_live_map(self.gba)
            interaction_distance = self._npc_interaction_gap(
                current, target, live, max_gap=interaction_gap
            )
            if getattr(target, "trainer_type", 0) and getattr(target, "facing_direction", 0):
                interaction_distance = self._trainer_front_range(current, target)
                if interaction_distance is None:
                    interaction_distance = self._npc_interaction_gap(
                        current, target, live, max_gap=1, allow_open_gap=interact
                    )
            if interaction_distance is not None:
                target_dict = target.as_dict()
                if not interact:
                    return {
                        "state": last_state,
                        "target": target_dict,
                        "approach": current,
                        "actions": actions,
                        "replans": attempt,
                        "interaction_distance": interaction_distance,
                        "reason": "in_range",
                    }
                # Re-read immediately before interacting. A wandering target
                # can move during the final observation-to-input round trip.
                if blocked_wait_frames:
                    self.gba.wait_frames(blocked_wait_frames)
                refreshed = read_live_objects(self.gba)
                refreshed_target = select_live_object(
                    refreshed,
                    map_id=actual_map,  # type: ignore[arg-type]
                    slot=slot,
                    local_id=local_id,
                    graphics_id=graphics_id,
                    predicate=predicate,
                    nearest_to=current,  # type: ignore[arg-type]
                )
                if refreshed_target is None:
                    from games.run_and_bun.objects import read_live_event_targets

                    refreshed_target = select_live_object(
                        read_live_event_targets(self.gba, map_id=actual_map),  # type: ignore[arg-type]
                        slot=slot,
                        local_id=local_id,
                        graphics_id=graphics_id,
                        predicate=predicate,
                        nearest_to=current,  # type: ignore[arg-type]
                    )
                    if refreshed_target is None:
                        last_state = self.observe()
                        continue
                current = ((last_state.get("map") or {}).get("x"), (last_state.get("map") or {}).get("y"))
                live = read_live_map(self.gba)
                interaction_distance = self._npc_interaction_gap(
                    current, refreshed_target, live, max_gap=interaction_gap
                )
                if getattr(refreshed_target, "trainer_type", 0) and getattr(refreshed_target, "facing_direction", 0):
                    interaction_distance = self._trainer_front_range(current, refreshed_target)
                    if interaction_distance is None:
                        interaction_distance = self._npc_interaction_gap(
                            current, refreshed_target, live, max_gap=1, allow_open_gap=interact
                        )
                if interaction_distance is None:
                    last_state = self.observe()
                    continue
                dx = refreshed_target.current_x - current[0]
                dy = refreshed_target.current_y - current[1]
                direction = self._cardinal_direction(dx, dy)
                self.gba.press(direction, frames=2)
                self.gba.press("A", frames=3)
                self.gba.wait_frames(transition_frames)
                final = self.observe()
                return {
                    "state": final,
                    "target": refreshed_target.as_dict(),
                    "approach": current,
                    "interaction_distance": interaction_distance,
                    "actions": actions,
                    "replans": attempt,
                    "reason": "interacted",
                }

            approach, path, interaction_distance = self._npc_approach_target(
                current,
                target,
                objects,
                grass_penalty=grass_penalty,
                interaction_gap=interaction_gap,
                prefer_open_gap=False,
                avoid_trainer_sight_lines=avoid_trainer_sight_lines,
                ignored_trainer_ids=(
                    {int(target.local_id)}
                    if interact and getattr(target, "trainer_type", 0) else None
                ),
            )
            if not path:
                last_state = self.observe()
                continue
            # Preflight the complete planned path before the first movement
            # chunk.  The per-chunk gate remains a defense against wandering
            # trainers, but cannot be the only gate: a rejected hard-trainer
            # check must never leave a partially advanced live position.
            if self.enforce_live_trainer_gate:
                gate = self._trainer_route_gate(
                    path,
                    verified_defeated_trainer_local_ids=verified_defeated_trainer_local_ids,
                )
                if not gate["allowed"]:
                    raise RuntimeError(f"trainer_engagement_blocked: {gate}")
            result = self.follow_route(
                path[:chunk_steps],
                expected_map=actual_map,  # type: ignore[arg-type]
                frames=frames,
                settle_frames=settle_frames,
                transition_frames=transition_frames,
                verified_defeated_trainer_local_ids=verified_defeated_trainer_local_ids,
            )
            actions.append(result["action"])
            last_state = result["state"]
            if last_state.get("mode") != "overworld":
                return {
                    "state": last_state,
                    "target": target.as_dict(),
                    "approach": approach,
                    "interaction_distance": interaction_distance,
                    "actions": actions,
                    "replans": attempt + 1,
                    "reason": "interrupted",
                }

        raise RuntimeError(
            f"NPC seeker exceeded {max_replans} replans for "
            f"slot={slot} local_id={local_id} graphics_id={graphics_id}"
        )
