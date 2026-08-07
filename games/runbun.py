"""Verified Pokémon Run & Bun v1.07 state decoding.

The addresses in this module are ROM-specific and come from
docs/RUNBUN_V107.md. They must not be reused for another ROM revision.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any


ROM_TITLE = "POKEMON EMER"
ROM_CODE = "BPEE"

SAVE_BLOCK1_PTR = 0x03005D9C
SAVE_BLOCK2_PTR = 0x03005DA0
PC_STORAGE_PTR = 0x03005DA4

NEW_GAME_CURSOR = 0x02023006
YES_NO_CURSOR = 0x0203C3C2
BATTLE_COMMAND_CURSOR = 0x02023A1C
BATTLE_MOVE_CURSOR = 0x02023A20

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
    10: "nickname_screen",
    16: "battle_intro",
    42: "battle_text",
    50: "battle_text_prompt",
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

    def observe(self, screenshot: str | bool = False) -> dict[str, Any]:
        scalar_reads = [
            {"name": "save_block1_ptr", "address": SAVE_BLOCK1_PTR, "width": 32},
            {"name": "save_block2_ptr", "address": SAVE_BLOCK2_PTR, "width": 32},
            {"name": "pc_storage_ptr", "address": PC_STORAGE_PTR, "width": 32},
            {"name": "new_game_cursor", "address": NEW_GAME_CURSOR, "width": 8},
            {"name": "yes_no_cursor", "address": YES_NO_CURSOR, "width": 8},
            {"name": "battle_command_cursor", "address": BATTLE_COMMAND_CURSOR, "width": 8},
            {"name": "battle_move_cursor", "address": BATTLE_MOVE_CURSOR, "width": 8},
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
            and battle_mode not in {16, 42, 50}
        ):
            battle_active = False
        text = decode_text_observation(base.get("text"))
        tasks = base.get("tasks")
        if text is not None:
            text["visible"] = (
                values["field_message_box_mode"] != 0
                or text["active"]
                or any(
                    page["printer"].get("window_id", 0) != 0
                    for page in text.get("pages", [])
                )
            )
            text["last_page"] = text.get("current")
            if not text["visible"] and not text["active"]:
                text["current"] = None
        if text and text["visible"]:
            mode = "dialogue"
        elif values["field_message_box_mode"] != 0:
            mode = "dialogue"
        elif save["block1"] is not None:
            mode = "overworld"
        else:
            mode = "unknown"
        return {
            "frame": base["frame"],
            "title": base["title"],
            "code": base["code"],
            "screenshot": base.get("screenshot"),
            "ui": {
                "new_game_option": values["new_game_cursor"],
                "yes_no": values["yes_no_cursor"],
                "battle_command": values["battle_command_cursor"],
                "battle_move": values["battle_move_cursor"],
                "field_message_box_mode": values["field_message_box_mode"],
                "field_message_box_mode_name": FIELD_MESSAGE_MODE_NAMES.get(
                    values["field_message_box_mode"],
                    f"unknown_{values['field_message_box_mode']}",
                ),
            },
            "save": save,
            "player": player,
            "mode": mode,
            "text": text,
            "party": party,
            "tasks": tasks,
            "battle": {
                "active": battle_active,
                "activity_detection": "battle_mons_species_nonzero",
                "menu": {
                    "command": values["battle_command_cursor"] if battle_active else None,
                    "move": values["battle_move_cursor"] if battle_active else None,
                    "command_name": (
                        ("fight", "bag", "pokemon", "run")[values["battle_command_cursor"]]
                        if battle_active and values["battle_command_cursor"] < 4
                        else None
                    ),
                },
                "mons": battle_mons,
            },
        }

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

    def advance_battle_until_menu(
        self,
        *,
        sample_frames: int = 24,
        max_frames: int = 900,
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
        while elapsed <= max_frames:
            state = self.observe()
            battle = state["battle"]
            opponent = next(
                (item for item in battle["mons"] if item["slot"] == 1),
                None,
            )
            if opponent and opponent["present"] and opponent["state"]["current_hp"] == 0:
                return {"state": "battle_end", "frames": elapsed, "presses": presses}
            if not battle["active"]:
                return {"state": "not_in_battle", "frames": elapsed, "presses": presses}
            field_mode = state["ui"].get("field_message_box_mode", 0)
            text = state.get("text") or {}
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
            return {"state": "timeout", "frames": elapsed, "presses": presses}

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
            if not state["battle"]["active"] and field_mode == 0:
                return {"state": "overworld", "frames": elapsed, "presses": presses}
            text = state.get("text") or {}
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
