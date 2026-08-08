"""Verified Pokémon Run & Bun v1.07 state decoding.

The addresses in this module are ROM-specific and come from
docs/RUNBUN_V107.md. They must not be reused for another ROM revision.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

try:
    from games.run_and_bun.visual import inspect_png
except ImportError:  # Pillow remains optional for RAM-only clients.
    inspect_png = None

from games.run_and_bun.rom_data import BattleRomData


ROM_TITLE = "POKEMON EMER"
ROM_CODE = "BPEE"

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
BATTLE_FIELD_MESSAGE_MODES = {16, 34, 36, 38, 42, 44, 46, 48, 50, 52, 54, 55, 59, 60, 68}
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
    98: 0,   # Quick Attack, Normal
    117: 0,  # Bide, delayed Normal
    182: 0,  # Protect, status
    183: 1,  # Mach Punch, Fighting
    205: 5,  # Rollout, Rock; unsafe for capture because it locks in
    229: 0,  # Rapid Spin, Normal (verified from the live move window)
    267: 0,  # Nature Power (terrain-dependent; conservative Normal)
    283: 0,  # Endeavor, fixed/conditional; never use for bounded weakening
    332: 2,  # Aerial Ace, Flying
    340: 2,  # Bounce, Flying
    342: 3,  # Poison Tail, Poison; residual-status risk for capture
    341: 4,  # Mud Shot, Ground
    450: 3,  # Venoshock, Poison
    453: 11, # Aqua Jet, Water
    512: 2,  # Acrobatics, Flying
    589: 0,  # Play Nice, Normal status move in this build
}
MOVE_POWER = {
    10: 35, 16: 40, 20: 15, 23: 65, 33: 40, 44: 60, 49: 20, 52: 40,
    71: 20, 75: 55, 88: 50, 98: 40, 183: 40, 205: 30, 229: 20,
    267: 80, 332: 60, 340: 85, 341: 55, 342: 50, 450: 65, 453: 40, 512: 60,
}
MOVE_SPECIAL_IDS = frozenset({16, 49, 52, 71, 267, 341, 450})
MOVE_PRIORITY_IDS = frozenset({98, 183, 453})
STATUS_MOVE_IDS = frozenset({28, 43, 117, 150, 182, 283, 589})
PHYSICAL_THREAT_DEBUFF_IDS = frozenset({589})  # Play Nice lowers Attack.
ABILITY_FLASH_FIRE = 18
PARALYSIS_STATUS = 0x40

# These moves can be nonlethal on the immediate roll but create an avoidable
# later KO (residual poison/bind or Rollout lock-in).  A capture planner must
# not call them a safe weakening line.
CAPTURE_UNSAFE_MOVE_IDS = frozenset({20, 205, 342})

# Species types are only a fallback for party entries: battle entries already
# carry live type bytes.  Keep this small and verified against this save.
SPECIES_TYPE_IDS = {
    16: (0, 2), 98: (11,), 193: (6, 2), 273: (12,),
    390: (10,), 761: (12,), 987: (17, 0),
}

# Only the interactions needed by the currently observed party/moves are
# listed here.  Unknown types are neutral rather than guessed, and the
# post-move battle message can refine the score for a species/ability pair.
TYPE_EFFECTIVENESS = {
    0: {7: 0.0},                         # Normal -> Ghost
    1: {0: 2.0, 2: 0.5, 3: 0.5, 5: 2.0, 6: 0.5, 8: 2.0, 11: 1.0, 14: 0.5, 15: 2.0, 17: 2.0},
    2: {1: 2.0, 6: 2.0, 12: 2.0, 10: 0.5, 11: 1.0},
    4: {10: 2.0, 11: 2.0, 12: 0.5, 2: 0.0, 6: 0.5, 13: 2.0},
    6: {12: 2.0, 10: 0.5, 1: 0.5, 2: 0.5, 17: 2.0, 18: 0.5},
    10: {12: 2.0, 6: 2.0, 10: 0.5, 11: 0.5, 5: 0.5, 15: 2.0, 8: 2.0},
    11: {10: 2.0, 4: 2.0, 12: 0.5, 11: 0.5},
    12: {11: 2.0, 4: 2.0, 10: 0.5, 12: 0.5, 2: 0.5, 6: 0.5},
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


def _named_values(reads: list[dict[str, Any]]) -> dict[str, int]:
    return {item["name"]: item["value"] for item in reads}


def _valid_ewram_pointer(value: int | None) -> bool:
    return value is not None and 0x02000000 <= value < 0x02040000


class RunBunAdapter:
    """Read a structured Run & Bun observation from an MGBA client."""

    def __init__(self, gba):
        self.gba = gba
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
                    "text": decode_gen3_text(raw),
                }
            )
        return contexts

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
        types = tuple(type_id for type_id in state.get("types", ()) if type_id != 9)
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
    ) -> tuple[float, float]:
        """Return a conservative normal-roll damage interval.

        Learned samples are evidence, not a point estimate: the minimum is
        safe for guaranteed outgoing KOs and the maximum is safe for the
        normal-hit incoming threat. Static estimates use the Gen III random
        roll interval; unknown move metadata remains zero and therefore
        cannot create a false forced KO.
        """
        if move_id in STATUS_MOVE_IDS:
            return (0.0, 0.0)
        attacker_state = attacker.get("state", attacker)
        defender_state = defender.get("state", defender)
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
                # Samples are observed at the then-live stages. Scale them
                # for the current attacker/defender stages before using them
                # as a bound; this keeps a successful Play Nice decrement
                # useful instead of treating it as cosmetic metadata.
                attack_key = "special_attack" if move_id in MOVE_SPECIAL_IDS else "attack"
                defense_key = "special_defense" if move_id in MOVE_SPECIAL_IDS else "defense"
                stage_scale = cls._stage_multiplier(attacker_state, attack_key) / max(
                    cls._stage_multiplier(defender_state, defense_key), 0.01
                )
                return (
                    max(1.0, float(min(samples)) * stage_scale),
                    max(1.0, float(max(samples)) * stage_scale),
                )
        move_type = MOVE_TYPE_IDS.get(move_id)
        if move_type is None:
            return (0.0, 0.0)
        power = MOVE_POWER.get(move_id, 40)
        if move_id == 49:  # Sonic Boom is fixed 20 damage in this battle.
            return (20.0, 20.0)
        attack_key = "special_attack" if move_id in MOVE_SPECIAL_IDS else "attack"
        defense_key = "special_defense" if move_id in MOVE_SPECIAL_IDS else "defense"
        attack = attacker_state.get(attack_key, 0)
        defense = defender_state.get(defense_key, 0)
        level = attacker_state.get("level", 0)
        if not attack or not defense or not level:
            return (0.0, 0.0)
        attack *= cls._stage_multiplier(attacker_state, attack_key)
        defense *= cls._stage_multiplier(defender_state, defense_key)
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
        base = (((2 * level / 5) + 2) * power * attack / max(defense, 1)) / 50 + 2
        high = max(1.0, base * effectiveness * stab)
        # Cartridge damage is integral. Flooring the low roll avoids claiming
        # a fractional guaranteed minimum that the game can round below;
        # ceiling the high roll preserves a conservative nonlethal/threat
        # bound despite the omitted intermediate integer floors.
        return (
            max(1.0, float(math.floor(high * 0.85))),
            max(1.0, float(math.ceil(high))),
        )

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
    ) -> float:
        """Return the midpoint of the bounded interval for ranking only."""
        low, high = cls._damage_bounds(
            move_id,
            attacker,
            defender,
            effectiveness_memory=effectiveness_memory,
            type_chart=type_chart,
            damage_memory=damage_memory,
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
            key=lambda item: (item["damage"], item["move_id"] not in STATUS_MOVE_IDS, -item["slot"]),
        )
        opponent_hp = opponent_state.get("current_hp", 0)
        player_hp = player_state.get("current_hp", 0)
        active_fraction = player_hp / max(player_state.get("max_hp", 1), 1)
        incoming = max(
            (cls._damage_bounds(move_id, opponent, player, type_chart=type_chart, damage_memory=damage_memory)[1] for move_id in opponent_state.get("moves", ()) if move_id),
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
                    (cls._damage_bounds(move_id, opponent, mon, type_chart=type_chart, damage_memory=damage_memory)[1] for move_id in opponent_state.get("moves", ()) if move_id),
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
        low_hp_fraction: float = 0.25,
        allow_switch: bool = True,
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
        mons = observation["battle"].get("mons", [])
        player = next((m["state"] for m in mons if m.get("slot") == 0 and m.get("present")), None)
        opponent = next((m["state"] for m in mons if m.get("slot") == 1 and m.get("present")), None)
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
        low_hp_fraction: float = 0.25,
        allow_switch: bool = True,
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
        player = next((m["state"] for m in mons if m.get("slot") == 0 and m.get("present")), None)
        opponent = next((m["state"] for m in mons if m.get("slot") == 1 and m.get("present")), None)
        if player is None or opponent is None:
            return {"decision": {"action": "none", "reason": "battle_mons_incomplete"}, "proof": {"level": "none"}}

        memory = damage_memory or {}
        plan = cls.choose_battle_action(
            observation,
            effectiveness_memory=effectiveness_memory,
            type_chart=type_chart,
            damage_memory=memory,
            low_hp_fraction=low_hp_fraction,
            allow_switch=allow_switch,
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
            damage_min, damage_max = cls._damage_bounds(
                move_id, state, defender,
                effectiveness_memory=effectiveness_memory,
                type_chart=type_chart,
                damage_memory=memory,
            )
            damage = (damage_min + damage_max) / 2
            ko_in = int((defender.get("current_hp", 0) + max(damage_max, 1) - 1) // max(damage_max, 1))
            guaranteed_ko_in = int((defender.get("current_hp", 0) + max(damage_min, 1) - 1) // max(damage_min, 1))
            priority = move_id in MOVE_PRIORITY_IDS
            attacker_speed = cls._effective_speed(state)
            defender_speed = cls._effective_speed(defender)
            speed_order = (
                "first" if attacker_speed > defender_speed
                else "second" if attacker_speed < defender_speed
                else "tie"
            )
            order = "first" if priority else speed_order
            first = order == "first"
            return {
                "slot": slot,
                "move_id": move_id,
                "move": move_name(state, slot, move_id),
                "damage_est": round(damage, 2),
                "damage_range": [round(damage_min, 2), round(damage_max, 2)],
                "ko_in": ko_in,
                "guaranteed_ko_in": guaranteed_ko_in,
                "order": order,
                "acts_first": first,
                "ko_before_hit": damage_min >= defender.get("current_hp", 0) > 0 and first,
                "evidence": {"kind": "observed_samples", "samples": samples} if samples else {"kind": "static_model"},
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
        incoming_max = max((item["damage_range"][1] for item in incoming), default=0.0)
        alternatives = sorted(moves, key=lambda item: (-item["damage_est"], item["slot"]))
        chosen = next(
            (item for item in moves if item["move_id"] == plan.get("move_id") and item["slot"] == plan.get("slot")),
            None,
        )
        if chosen and chosen["ko_before_hit"]:
            proof_level = "forced_estimate"
            claim = "fastest visible one-turn KO; no slower move can improve the turn"
        elif chosen and plan.get("reason") == "safe_two_turn_finish":
            proof_level = "safe_two_turn_estimate"
            claim = "estimated two-turn finish while surviving the modeled intervening hit"
        elif chosen and plan.get("reason") == "defensive_status_vs_physical_threat":
            proof_level = "best_estimate"
            claim = "only live status line that can reduce the modeled physical KO threat; every switch candidate is also KO'd"
        elif chosen and plan.get("reason") == "last_damage_line":
            proof_level = "heuristic"
            claim = "no legal line guarantees survival; selected the highest observed damage chance"
        else:
            proof_level = "best_estimate"
            claim = "highest modeled damage/survival score among legal actions"
        return {
            "state": {
                "player": {"species": player_key, "hp": player.get("current_hp"), "max_hp": player.get("max_hp"), "speed": player_speed},
                "opponent": {"species": opponent_key, "hp": opponent_hp, "max_hp": opponent.get("max_hp"), "speed": opponent_speed},
            },
            "decision": plan,
            "chosen": chosen,
            "alternatives": alternatives,
            "incoming": {"max_damage_est": round(incoming_max, 2), "moves": incoming},
            "proof": {
                "level": proof_level,
                "claim": claim,
                "checks": {
                    "legal_move_count": len(moves),
                    "opponent_hp": opponent_hp,
                    "chosen_damage_est": chosen["damage_est"] if chosen else None,
                    "chosen_acts_first": chosen["acts_first"] if chosen else None,
                },
                "caveat": "RNG, hidden AI, and unverified move metadata can invalidate non-forced estimates.",
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
        battle_format = (
            "double"
            if any(mon.get("present") for mon in battle_mons[2:4])
            else "single"
        )
        opponent = next((mon for mon in battle_mons if mon["slot"] == 1), None)
        battle_mode = values["field_message_box_mode"]
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
        active_species = {
            self._present_battle_mon(observation, slot)["species"]
            for slot in (0, 2)
        }
        if target["species"] in active_species:
            raise ValueError(f"party slot {party_slot} is already active")
        acting_party_slots = [
            mon["slot"] for mon in party if mon["species"] == acting["species"]
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
        initial_battle_signature = None
        action_transition_seen = not after_action
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
            if after_action and initial_battle_signature is None:
                initial_battle_signature = battle_signature
            if after_action and battle_signature != initial_battle_signature:
                # The prompt printer is reused at the same address after a
                # turn, so its pointer/text tuple can be identical even
                # though the turn already resolved.  HP/PP/species RAM is the
                # authoritative transition signal.
                action_transition_seen = True
            text = state.get("text") or {}
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
            if ram_menu_state == "target_menu":
                # Target choice is a gameplay decision, never battle text to
                # auto-advance. Hand it back to the tactical controller.
                return {
                    "state": "target_menu",
                    "frames": elapsed,
                    "presses": presses,
                    "feedback": "\n".join(feedback_parts),
                }
            if ram_menu_state == "move_menu" and (not after_action or action_transition_seen):
                return {
                    "state": "move_menu",
                    "frames": elapsed,
                    "presses": presses,
                    "feedback": "\n".join(feedback_parts),
                }
            if ram_menu_state == "party_switch" and (not after_action or action_transition_seen):
                return {
                    "state": "party_switch",
                    "frames": elapsed,
                    "presses": presses,
                    "feedback": "\n".join(feedback_parts),
                }
            if ram_menu_state == "command_menu" and (not after_action or action_transition_seen):
                return {
                    "state": "command_menu",
                    "frames": elapsed,
                    "presses": presses,
                    "feedback": "\n".join(feedback_parts),
                }
            # A stale Type/PP printer can survive the transition back to the
            # command selector.  When both markers are present, the live
            # ``What will ... do?`` command prompt wins; otherwise open_fight
            # can skip Fight and spend a turn pressing into an old move menu.
            if (
                move_prompt
                and ram_menu_state != "target_menu"
                and not command_prompt
                and (not after_action or action_transition_seen)
            ):
                return {
                    "state": "move_menu",
                    "frames": elapsed,
                    "presses": presses,
                    "feedback": "\n".join(feedback_parts),
                }
            if party_switch_prompt and (not after_action or action_transition_seen):
                return {
                    "state": "party_switch",
                    "frames": elapsed,
                    "presses": presses,
                    "feedback": "\n".join(feedback_parts),
                }
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
                    if text.get("active"):
                        self.gba.wait_frames(sample_frames)
                    else:
                        self.gba.press("A")
                        presses += 1
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
            if not battle_active:
                return {
                    "state": "not_in_battle",
                    "frames": elapsed,
                    "presses": presses,
                    "feedback": "\n".join(feedback_parts),
                }
            if (command_prompt or (visual and visual.battle_command_menu)) and (
                prompt_transition_seen or action_transition_seen
            ):
                return {
                    "state": "command_menu",
                    "frames": elapsed,
                    "presses": presses,
                    "feedback": "\n".join(feedback_parts),
                }
            # Run & Bun leaves the battle text mode latched at 50 while both
            # battle messages and the command selector are on screen.  The
            # text buffer is a different runtime buffer here, but the live
            # printer layout is stable: the command selector is ready only
            # when one printer remains active at its cleared (0, 1) cursor.
            # This avoids treating "Go! ..." or a fainting message as a move
            # menu and sending an accidental input too early.
            if field_mode == 50 and self._battle_command_prompt(battle_contexts) and (
                prompt_transition_seen or action_transition_seen
            ):
                return {
                    "state": "command_menu",
                    "frames": elapsed,
                    "presses": presses,
                    "feedback": "\n".join(feedback_parts),
                }
            if field_mode != 0:
                if text.get("active"):
                    self.gba.wait_frames(sample_frames)
                else:
                    self.gba.press("A")
                    presses += 1
                    self.gba.wait_frames(sample_frames)
            elif text.get("active"):
                self.gba.wait_frames(sample_frames)
            else:
                return {"state": "command_menu", "frames": elapsed, "presses": presses}
            elapsed += sample_frames
        return {
            "state": "timeout",
            "frames": elapsed,
            "presses": presses,
            "feedback": "\n".join(feedback_parts),
        }

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
        # Keep the raw ROM chart available through ``rom_data()``. Its table
        # has a legacy reserved-type index that still needs a complete mapping
        # proof before it is allowed to steer decisions; live feedback and
        # learned damage are safer for this battle loop today.
        type_chart = None
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
        """Return the verified item-ID 1 quantity across decoded pockets."""
        return sum(
            int(item.get("quantity", 0))
            for entries in inventory.get("pockets", {}).values()
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
        target_hp_fraction: float = 0.35,
        type_chart: dict[int, dict[int, float]] | None = None,
        damage_memory: dict[tuple[int, int, int], list[int]] | None = None,
    ) -> dict[str, Any]:
        """Choose a conservative wild-capture action from canonical RAM state.

        A weakening move is legal for this policy only when its modeled normal
        maximum is strictly below live target HP. Moves with lock-in or
        residual-KO risk are excluded even if their first hit is nonlethal.
        Unknown damage is uncertainty, never evidence that a move is safe.
        """
        if not 0 < target_hp_fraction < 1:
            raise ValueError("target_hp_fraction must be between 0 and 1")
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
            )
            if damage_max <= 0 and move_id not in STATUS_MOVE_IDS:
                unknown_moves.append(int(move_id))
            # Gen III criticals can double normal damage. Use that larger
            # bound even though later-generation mechanics often use 1.5x;
            # a move is capture-safe only when every modeled crit remains
            # nonlethal.
            critical_damage_max = damage_max * 2
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
                "guaranteed_nonlethal": safe,
                "post_hp_range": [
                    max(1, round(target_hp - critical_damage_max, 2)),
                    max(1, round(target_hp - damage_min, 2)),
                ] if safe else None,
                "excluded_for_residual_risk": move_id in CAPTURE_UNSAFE_MOVE_IDS,
            })

        safe_moves = [move for move in legal_moves if move["guaranteed_nonlethal"]]
        # Maximize the conservative minimum reduction, then the modeled
        # maximum reduction; this increases catch odds without accepting a KO
        # roll. Stable slot ordering makes ties deterministic.
        safest_weaken = max(
            safe_moves,
            key=lambda move: (
                move["damage_range"][0],
                move["damage_range"][1],
                -move["slot"],
            ),
            default=None,
        )
        hp_fraction = target_hp / target_max_hp
        should_weaken = hp_fraction > target_hp_fraction and safest_weaken is not None
        if should_weaken:
            decision = {"kind": "move", **safest_weaken}
            claim = "expected-best safe weakening line; modeled maximum remains strictly nonlethal"
        elif poke_balls > 0:
            decision = {"kind": "throw_ball", "button": "L"}
            claim = (
                "expected-best throw after reaching the configured HP threshold"
                if hp_fraction <= target_hp_fraction
                else "heuristic throw because no damaging move has a verified nonlethal bound"
            )
        else:
            decision = {"kind": "blocked", "reason": "no_poke_balls"}
            claim = "capture is impossible with verified inventory"
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
                "level": "expected-best" if decision["kind"] != "blocked" else "forced",
                "claim": claim,
                "material_uncertainty": {
                    "catch_rng": decision["kind"] == "throw_ball",
                    "unknown_damage_move_ids": sorted(set(unknown_moves)),
                    "critical_hit_multiplier_bound": 2.0 if decision["kind"] == "move" else None,
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
        max_frames: int = 1800,
    ) -> dict[str, Any]:
        """Throw the current Poké Ball with L and verify the complete result.

        Run & Bun exposes a battle hotkey on L. This avoids opening the Bag;
        quantity delta, capture text, and party insertion are the authoritative
        acknowledgments. The helper currently requires an open party slot so a
        successful capture can be verified without relying on PC UI state.
        """
        from games.run_and_bun.state import RunBun

        state = RunBun(self.gba)
        before = self.observe()
        if before.get("battle", {}).get("menu", {}).get("state") != "command_menu":
            raise RuntimeError("poke_ball_hotkey_requires_command_menu")
        before_count = state.party_count()
        if before_count >= 6:
            raise RuntimeError("poke_ball_hotkey_requires_open_party_slot")
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
        if caught:
            # A first capture may show Pokédex registration before the naming
            # question. After catch text is verified, A may only advance that
            # post-capture flow; B is the deterministic No/cancel shortcut at
            # the nickname prompt.
            for _ in range(16):
                if state.party_count() == before_count + 1:
                    break
                observed = self.observe()
                contexts = (observed.get("text") or {}).get("battle_printers", [])
                rendered = "\n".join(context.get("text", "") for context in contexts)
                self.gba.press("B" if "Give a nickname" in rendered else "A", frames=3)
                self.gba.wait_frames(120)
            if state.party_count() != before_count + 1:
                raise RuntimeError("capture_text_seen_but_party_insertion_not_verified")
            inserted = state.party_mon(before_count)
            if inserted.species_id != target.get("species"):
                raise RuntimeError(
                    f"captured_species_mismatch: expected {target.get('species')} got {inserted.species_id}"
                )
            outcome = "caught"
        elif resolution.get("state") == "command_menu":
            outcome = "escaped_ball"
        else:
            raise RuntimeError(
                f"poke_ball_outcome_unresolved: state={resolution.get('state')} feedback={feedback!r}"
            )
        return {
            "outcome": outcome,
            "target_species": target.get("species"),
            "target_hp": target.get("current_hp"),
            "active_hp_before": active.get("current_hp"),
            "active_hp_after": state.party_mon(0).hp,
            "balls_before": before_balls,
            "balls_after": after_balls,
            "party_count_before": before_count,
            "party_count_after": state.party_count(),
            "resolution": resolution,
        }

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
            try:
                path = live.path_to(
                    current,
                    target,
                    blocked_edges=persistent_blocked_edges | dynamic_blocked_edges,
                    allow_nonwalkable_start=True,
                    grass_penalty=grass_penalty,
                )
            except ValueError as error:
                if not str(error).startswith("no live-grid path"):
                    raise
                # A dynamic obstruction can temporarily make the current tile
                # look unusable.  Let the map task/NPC advance, then retry the
                # authoritative read instead of consulting a screenshot.
                if blocked_wait_frames:
                    self.gba.wait_frames(blocked_wait_frames)
                dynamic_blocked_edges.clear()
                stalled.clear()
                last_state = self.observe()
                continue

            if not path:
                continue
            first_edge = (current, path[0])
            chunk = path[:chunk_steps]
            result = self.follow_route(
                chunk,
                frames=frames,
                settle_frames=settle_frames,
                transition_frames=transition_frames,
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
                from games.run_and_bun.trainer_database import lookup_trainer

                map_group, map_number = target.map_id
                trainer = lookup_trainer(
                    map_group=map_group,
                    map_number=map_number,
                    local_id=target.local_id,
                    graphics_id=getattr(target, "graphics_id", None),
                    script_address=getattr(target, "script_address", None),
                )
                report["trainer"] = trainer
                if not trainer["found"]:
                    report["ready"] = False
                    report["reason"] = "trainer_database_unknown"
                elif not trainer["trusted"]:
                    report["ready"] = False
                    report["reason"] = "trainer_identity_mismatch"
                else:
                    record = trainer["record"] or {}
                    battle = record.get("battle", {})
                    strategy = record.get("strategy", {})
                    if battle.get("hard_fight") and strategy.get("status") != "ready":
                        report["ready"] = False
                        report["reason"] = "hard_fight_plan_required"
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
            data = task.get("data") or []
            # The task function ID is allocator/state dependent in this hack
            # (the same Bag used 23472 outdoors and 10876 in the Center).
            # Its cursor payload is stable: two ROM script pointers, the
            # pocket selector at +6, menu mode 8 at +9, and item cursor +13.
            if (
                task.get("active")
                and len(data) >= 14
                and data[1] == 512
                and data[3] == 2077
                and data[4] == 51445
                and data[5] == 2077
                and 0 <= data[6] <= 4
                and data[9] == 8
            ):
                return task
        raise RuntimeError("field_bag_not_open: Bag task is not active")

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
    def _field_move_learning_pending(observation: dict[str, Any]) -> bool:
        """Detect move-learning ownership before any automatic menu cleanup."""
        text = observation.get("text") or {}
        rendered = "\n".join(
            [((text.get("current") or {}).get("text") or "")]
            + [context.get("text", "") for context in text.get("battle_printers", [])]
        )
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

    def use_field_item(
        self,
        item_name: str,
        *,
        target_slot: int | None = None,
        target_species: int | None = None,
        target_nickname: str | None = None,
    ) -> dict[str, Any]:
        """Use a verified field item through RAM-backed Bag/party cursors.

        Run & Bun's current field Bag exposes Endless Candy in Key Items. The
        operation intentionally rejects unknown names instead of selecting an
        arbitrary row. Party selection is by identity when a species or
        nickname is supplied, then the live field cursor is verified before A.
        """
        item_key = item_name.casefold()
        if item_key != "endless candy":
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
        desired_pocket = 4
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

        # Endless Candy is the first Key Item row. Reset the live item cursor
        # by observation, not by pressing a guessed number of UP inputs.
        for _ in range(8):
            task = self._field_bag_task()
            cursor = task["data"][13]
            if cursor == 0:
                break
            self.gba.press("UP", frames=3)
            self.gba.wait_frames(30)
        task = self._field_bag_task()
        if task["data"][13] != 0:
            raise RuntimeError(f"field_bag_item_cursor_failed: expected=0 got={task['data'][13]}")

        # One A selects the row; the second confirms Use and opens the party
        # target prompt. The field prompt exposes its own RAM cursor.
        self.gba.press("A", frames=3)
        self.gba.wait_frames(30)
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
                        allow_nonwalkable_start=True,
                        grass_penalty=grass_penalty,
                    )
                except ValueError:
                    continue
                # Dijkstra's grass penalty is represented in the path choice,
                # and Manhattan distance breaks equal-cost ties without
                # screenshots.
                if getattr(target, "trainer_type", 0):
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
        step_x = 0 if dx == 0 else (1 if dx > 0 else -1)
        step_y = 0 if dy == 0 else (1 if dy > 0 else -1)
        between = (current[0] + step_x, current[1] + step_y)
        if allow_open_gap or not live.walkable(*between):
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
                current, target, live, max_gap=interaction_gap, allow_open_gap=interact
            )
            if getattr(target, "trainer_type", 0) and getattr(target, "facing_direction", 0):
                interaction_distance = self._trainer_front_range(current, target)
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
                    current, refreshed_target, live, max_gap=interaction_gap, allow_open_gap=interact
                )
                if getattr(refreshed_target, "trainer_type", 0) and getattr(refreshed_target, "facing_direction", 0):
                    interaction_distance = self._trainer_front_range(current, refreshed_target)
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
                prefer_open_gap=interact,
            )
            if not path:
                last_state = self.observe()
                continue
            result = self.follow_route(
                path[:chunk_steps],
                expected_map=actual_map,  # type: ignore[arg-type]
                frames=frames,
                settle_frames=settle_frames,
                transition_frames=transition_frames,
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
