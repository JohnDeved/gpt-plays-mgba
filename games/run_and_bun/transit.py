"""RAM/ROM-backed ferry and scripted map-transit discovery.

Some Run & Bun destinations are not direct map connections: an overworld
object runs a ROM script, shows a short voyage dialogue, and moves the player
through several water maps before the destination settles.  This module
identifies those objects from the loaded event template and script text so a
caller can select the transit actor by stable identity instead of probing
doors or water boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Any

from .objects import LiveEventTarget, read_live_event_targets
from .state import decode_gen3


TRANSIT_TEXT_HINTS = (
    "anchors aweigh",
    "set sail",
    "where are we bound",
    "we've made land in",
    "you just tell me whenever",
)
SCRIPT_SCAN_BYTES = 256
ROM_POINTER_MIN = 0x08000000
ROM_POINTER_MAX = 0x0A000000
ROM_TEXT_READ_BYTES = 160


def is_transit_text(text: str) -> bool:
    """Return whether decoded script text describes a map transit."""
    normalized = " ".join(text.casefold().split())
    return any(hint in normalized for hint in TRANSIT_TEXT_HINTS)


def _decoded_rom_text(gba: Any, address: int) -> str:
    raw = gba.read_range(address, ROM_TEXT_READ_BYTES)
    end = next((index for index, value in enumerate(raw) if value == 0xFF), len(raw))
    return decode_gen3(raw[:end]).strip()


def is_plausible_script_text(text: str) -> bool:
    """Reject pointer/code noise while retaining ordinary sign dialogue."""
    normalized = " ".join(text.split())
    tag_count = normalized.count("<")
    return (
        2 <= len(normalized) <= 120
        and sum(character.isalpha() for character in normalized) >= 4
        and tag_count <= 8
    )


def script_texts(
    gba: Any, script_address: int, *, scan_bytes: int = SCRIPT_SCAN_BYTES
) -> list[str]:
    """Extract bounded, decodable Gen III strings referenced by an event script."""
    if not ROM_POINTER_MIN <= script_address < ROM_POINTER_MAX:
        return []
    # Background sign events in this ROM may point directly at encoded text,
    # while object scripts point at bytecode containing text pointers.
    direct = " ".join(_decoded_rom_text(gba, script_address).split())
    if is_plausible_script_text(direct):
        return [direct]
    raw = gba.read_range(script_address, min(SCRIPT_SCAN_BYTES, scan_bytes))
    texts: list[str] = []
    seen_addresses: set[int] = set()
    for offset in range(0, len(raw) - 3):
        address = struct.unpack_from("<I", raw, offset)[0]
        if not ROM_POINTER_MIN <= address < ROM_POINTER_MAX or address in seen_addresses:
            continue
        seen_addresses.add(address)
        try:
            text = " ".join(_decoded_rom_text(gba, address).split())
        except (RuntimeError, ValueError):
            continue
        if is_plausible_script_text(text) and text not in texts:
            texts.append(text)
    return texts


def script_transit_texts(
    gba: Any, script_address: int, *, scan_bytes: int = SCRIPT_SCAN_BYTES
) -> list[str]:
    """Extract unique transit-related strings referenced by one ROM script.

    The scan deliberately only promotes strings containing a known voyage
    phrase.  Random code/data pointers therefore remain diagnostic noise and
    cannot make an arbitrary NPC selectable as a ferry.
    """
    if not ROM_POINTER_MIN <= script_address < ROM_POINTER_MAX:
        return []
    return [
        text for text in script_texts(gba, script_address, scan_bytes=scan_bytes)
        if is_transit_text(text)
    ]


@dataclass(frozen=True)
class LiveTransitOption:
    """A stable event-template identity whose script contains voyage text."""

    target: LiveEventTarget
    texts: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.target.as_dict(),
            "transit_text": list(self.texts),
            "selection_identity": {
                "local_id": self.target.local_id,
                "graphics_id": self.target.graphics_id,
                "script_address": f"0x{self.target.script_address:08x}",
            },
        }


def read_live_transit_options(gba: Any, *, map_id: tuple[int, int]) -> list[LiveTransitOption]:
    """Discover scripted ferry/transit actors in the loaded map."""
    targets = read_live_event_targets(gba, map_id=map_id)
    script_starts = sorted({
        target.script_address for target in targets
        if ROM_POINTER_MIN <= target.script_address < ROM_POINTER_MAX
    })
    options: list[LiveTransitOption] = []
    for target in targets:
        next_start = next(
            (address for address in script_starts if address > target.script_address),
            target.script_address + SCRIPT_SCAN_BYTES,
        )
        texts = script_transit_texts(
            gba, target.script_address, scan_bytes=next_start - target.script_address
        )
        if texts:
            options.append(LiveTransitOption(target=target, texts=tuple(texts)))
    return options
