"""Decode Run & Bun inventory and progression data directly from RAM."""

from __future__ import annotations

from typing import Any


SAVE_BLOCK1_PTR = 0x03005D9C
SAVE_BLOCK2_PTR = 0x03005DA0
SAVE_BLOCK2_ENCRYPTION_KEY_OFFSET = 0xA4
MONEY_OFFSET = 0x490

# Verified against the live v1.07 SaveBlock1. Item IDs are plain u16 values;
# quantities are XORed with the low half of SaveBlock2.encryptionKey.
POCKETS = {
    "items": (0x560, 20),
    "key_items": (0x5B0, 20),
    "poke_balls": (0x600, 16),
    "tmhm": (0x640, 64),
    "berries": (0x740, 46),
    # Run & Bun v1.07's berry-tree script writes its confirmed Berries Pocket
    # entry here; the vanilla offsets above contain unrelated legacy data.
    "runbun_berries": (0x900, 46),
    # Promotional Potion dialogue writes the Medicine Pocket here.
    "runbun_medicine": (0xA00, 20),
}

# Expanded Run & Bun UI bases. Keep legacy names above for forensics: scripts
# and shop events can still leave entries in old-looking slots.
UI_POCKETS = {
    "ui_items": (0x560, 60),
    "ui_poke_balls": (0x650, 16),
    "ui_berries": (0x900, 64),
    "ui_medicine": (0xA00, 20),
}

VERIFIED_ITEM_NAMES = {
    1: "Poke Ball",
    28: "Potion",
    520: "Oran Berry",
    711: "Super Rod",
}
FLAGS_OFFSET = 0x1220
FLAGS_LENGTH = 0x120
VARS_OFFSET = 0x1340
VARS_COUNT = 0x100


def _pointer(gba: Any, address: int) -> int:
    pointer = int(gba.read32(address))
    if not 0x02000000 <= pointer < 0x02040000:
        raise RuntimeError(f"invalid save pointer at {address:#x}: {pointer:#x}")
    return pointer


def read_inventory(gba: Any) -> dict[str, Any]:
    """Read legacy pockets plus verified Run & Bun Items pocket."""
    save_block1 = _pointer(gba, SAVE_BLOCK1_PTR)
    save_block2 = _pointer(gba, SAVE_BLOCK2_PTR)
    key_raw = gba.read_range(save_block2 + SAVE_BLOCK2_ENCRYPTION_KEY_OFFSET, 4)
    encryption_key = int.from_bytes(key_raw, "little")
    money_raw = gba.read_range(save_block1 + MONEY_OFFSET, 4)
    money = int.from_bytes(money_raw, "little") ^ encryption_key
    pocket_end = max(
        offset + count * 4
        for offset, count in (*POCKETS.values(), *UI_POCKETS.values())
    )
    raw = gba.read_range(save_block1 + 0x560, pocket_end - 0x560)
    # Verified live v1.07 UI: Items starts at +0x4E8 and contains Potion,
    # Poke Ball, etc. Keep older offsets below for compatibility/forensics.
    live_raw = gba.read_range(save_block1 + 0x4E8, 20 * 4)
    quantity_mask = encryption_key & 0xFFFF
    pockets: dict[str, list[dict[str, int]]] = {}
    for name, (absolute_offset, count) in (*POCKETS.items(), *UI_POCKETS.items()):
        relative_offset = absolute_offset - 0x560
        entries: list[dict[str, int]] = []
        for slot in range(count):
            offset = relative_offset + slot * 4
            item_id = int.from_bytes(raw[offset : offset + 2], "little")
            encrypted_quantity = int.from_bytes(raw[offset + 2 : offset + 4], "little")
            quantity = encrypted_quantity ^ quantity_mask
            if item_id or quantity:
                entries.append({
                    "slot": slot,
                    "item_id": item_id,
                    "quantity": quantity,
                    "address": save_block1 + absolute_offset + slot * 4,
                })
        pockets[name] = entries
    live_items: list[dict[str, int]] = []
    for slot in range(20):
        offset = slot * 4
        item_id = int.from_bytes(live_raw[offset : offset + 2], "little")
        quantity = int.from_bytes(live_raw[offset + 2 : offset + 4], "little") ^ quantity_mask
        if item_id or quantity:
            live_items.append({
                "slot": slot,
                "item_id": item_id,
                "quantity": quantity,
                "address": save_block1 + 0x4E8 + offset,
            })
    pockets["live_items"] = live_items
    return {
        "save_block1": save_block1,
        "save_block2": save_block2,
        "encryption_key_address": save_block2 + SAVE_BLOCK2_ENCRYPTION_KEY_OFFSET,
        "encryption_key_low16": quantity_mask,
        "money_address": save_block1 + MONEY_OFFSET,
        "money": money,
        "pockets": pockets,
    }


def read_progress(gba: Any) -> dict[str, Any]:
    """Return set flag IDs and non-zero vars from the live SaveBlock1."""
    save_block1 = _pointer(gba, SAVE_BLOCK1_PTR)
    flags = gba.read_range(save_block1 + FLAGS_OFFSET, FLAGS_LENGTH)
    vars_raw = gba.read_range(save_block1 + VARS_OFFSET, VARS_COUNT * 2)
    set_flags = [
        byte_index * 8 + bit
        for byte_index, value in enumerate(flags)
        for bit in range(8)
        if value & (1 << bit)
    ]
    variables = {
        index: int.from_bytes(vars_raw[index * 2 : index * 2 + 2], "little")
        for index in range(VARS_COUNT)
        if vars_raw[index * 2 : index * 2 + 2] != b"\x00\x00"
    }
    return {
        "save_block1": save_block1,
        "flags_address": save_block1 + FLAGS_OFFSET,
        "flags_length": FLAGS_LENGTH,
        "set_flag_ids": set_flags,
        "vars_address": save_block1 + VARS_OFFSET,
        "variables": variables,
    }
