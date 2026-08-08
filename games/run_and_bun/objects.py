"""Decode the live overworld object-event table for Run & Bun v1.07.

The object table is the authoritative runtime position of the player, NPCs,
items, and other map actors.  It is more useful to navigation than a sprite
location inferred from a screenshot: a moving actor can be re-read between
short path chunks and its map tile can be treated as a transient obstacle.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Callable, Iterable
import struct


# Verified in the live Run & Bun v1.07 process by matching the player object
# (isPlayer bit, map group/number, and currentCoords == SaveBlock1 + 7).
OBJECT_EVENTS_BASE = 0x02036914
OBJECT_EVENT_STRIDE = 0x24
OBJECT_EVENT_COUNT = 16
CAMERA_TILE_ORIGIN = 7

OBJECT_FLAG_ACTIVE = 1 << 0
OBJECT_FLAG_FROZEN = 1 << 8
OBJECT_FLAG_INVISIBLE = 1 << 13
OBJECT_FLAG_IS_PLAYER = 1 << 16
LIVE_MAP_HEADER = 0x020368DC
MAP_HEADER_EVENTS_OFFSET = 0x04
MAP_EVENTS_OBJECT_COUNT_OFFSET = 0x00
MAP_EVENTS_OBJECTS_PTR_OFFSET = 0x04
EVENT_TEMPLATE_STRIDE = 0x18


def _u16(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 2], "little")


def _s16(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 2], "little", signed=True)


@dataclass(frozen=True)
class LiveObject:
    """A decoded slot in the live Gen III object-event array.

    ``current_x/current_y`` are map-local coordinates.  The engine stores the
    camera-relative runtime coordinates in the object record, so ``x/y`` are
    those values minus the seven-tile camera origin used by this ROM.
    """

    slot: int
    flags: int
    active: bool
    is_player: bool
    frozen: bool
    invisible: bool
    sprite_id: int
    graphics_id: int
    movement_type: int
    trainer_type: int
    local_id: int
    map_number: int
    map_group: int
    elevation: int
    previous_elevation: int
    initial_x: int
    initial_y: int
    current_x: int
    current_y: int
    previous_x: int
    previous_y: int
    facing_direction: int
    movement_direction: int
    range_x: int
    range_y: int
    field_effect_sprite_id: int
    warp_arrow_sprite_id: int
    movement_action_id: int
    trainer_range_or_berry_id: int
    current_metatile_behavior: int
    previous_metatile_behavior: int
    previous_movement_direction: int
    direction_sequence_index: int
    player_copyable_movement: int

    @property
    def position(self) -> tuple[int, int]:
        return self.current_x, self.current_y

    @property
    def map_id(self) -> tuple[int, int]:
        return self.map_group, self.map_number

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["position"] = self.position
        value["map_id"] = self.map_id
        return value


@dataclass(frozen=True)
class LiveEventTarget:
    """Static map-event identity used when a connection has stale globals.

    Map connections can leave ``gObjectEvents`` populated with the source
    map's runtime records.  The loaded MapHeader's event template remains
    authoritative for stationary trainers and gives the seeker a deterministic
    fallback until the engine rebuilds its runtime object array.
    """

    slot: int
    local_id: int
    graphics_id: int
    movement_type: int
    trainer_type: int
    trainer_sight_radius: int
    current_x: int
    current_y: int
    map_id: tuple[int, int]

    active: bool = True
    invisible: bool = False
    is_player: bool = False

    @property
    def position(self) -> tuple[int, int]:
        return self.current_x, self.current_y

    def as_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "local_id": self.local_id,
            "graphics_id": self.graphics_id,
            "movement_type": self.movement_type,
            "trainer_type": self.trainer_type,
            "trainer_sight_radius": self.trainer_sight_radius,
            "position": self.position,
            "map_id": self.map_id,
            "source": "map_event_template",
        }


def read_live_event_targets(gba: Any, *, map_id: tuple[int, int]) -> list[LiveEventTarget]:
    """Read the active map's object-event templates through the Lua bridge."""
    header = gba.read_range(LIVE_MAP_HEADER, 8)
    events_ptr = struct.unpack_from("<I", header, MAP_HEADER_EVENTS_OFFSET)[0]
    if not events_ptr:
        return []
    events = gba.read_range(events_ptr, 8)
    count = events[MAP_EVENTS_OBJECT_COUNT_OFFSET]
    objects_ptr = struct.unpack_from("<I", events, MAP_EVENTS_OBJECTS_PTR_OFFSET)[0]
    if not count or not objects_ptr:
        return []
    raw = gba.read_range(objects_ptr, count * EVENT_TEMPLATE_STRIDE)
    result: list[LiveEventTarget] = []
    for slot in range(count):
        offset = slot * EVENT_TEMPLATE_STRIDE
        local_id = raw[offset]
        graphics_id = raw[offset + 1]
        # Run & Bun keeps an additional u16 template field at +2; the
        # coordinate pair starts at +4 (the standard Gen III event-template
        # layout used by this ROM).
        x = struct.unpack_from("<h", raw, offset + 4)[0]
        y = struct.unpack_from("<h", raw, offset + 6)[0]
        result.append(
            LiveEventTarget(
                slot=slot,
                local_id=local_id,
                graphics_id=graphics_id,
                movement_type=raw[offset + 9],
                # Gen III event templates store movement range at +0x0A/+0x0B;
                # trainer type and sight radius are u16 values at +0x0C/+0x0E.
                trainer_type=_u16(raw, offset + 0x0C),
                trainer_sight_radius=_u16(raw, offset + 0x0E),
                current_x=x,
                current_y=y,
                map_id=map_id,
            )
        )
    return result


def decode_live_objects(
    raw: bytes,
    *,
    base: int = OBJECT_EVENTS_BASE,
    stride: int = OBJECT_EVENT_STRIDE,
    count: int = OBJECT_EVENT_COUNT,
    camera_origin: int = CAMERA_TILE_ORIGIN,
    include_inactive: bool = False,
) -> list[LiveObject]:
    """Decode an object table returned by one contiguous bridge read."""
    expected = stride * count
    if len(raw) < expected:
        raise ValueError(f"object table too short: {len(raw)} < {expected}")
    result: list[LiveObject] = []
    for slot in range(count):
        offset = slot * stride
        flags = int.from_bytes(raw[offset : offset + 4], "little")
        active = bool(flags & OBJECT_FLAG_ACTIVE)
        if not include_inactive and not active:
            continue
        packed_elevation = raw[offset + 0x0B]
        direction = _u16(raw, offset + 0x18)
        range_value = raw[offset + 0x1A]
        current_raw_x = _s16(raw, offset + 0x10)
        current_raw_y = _s16(raw, offset + 0x12)
        previous_raw_x = _s16(raw, offset + 0x14)
        previous_raw_y = _s16(raw, offset + 0x16)
        result.append(
            LiveObject(
                slot=slot,
                flags=flags,
                active=active,
                is_player=bool(flags & OBJECT_FLAG_IS_PLAYER),
                frozen=bool(flags & OBJECT_FLAG_FROZEN),
                invisible=bool(flags & OBJECT_FLAG_INVISIBLE),
                sprite_id=raw[offset + 0x04],
                graphics_id=raw[offset + 0x05],
                movement_type=raw[offset + 0x06],
                trainer_type=raw[offset + 0x07],
                local_id=raw[offset + 0x08],
                map_number=raw[offset + 0x09],
                map_group=raw[offset + 0x0A],
                elevation=packed_elevation & 0x0F,
                previous_elevation=(packed_elevation >> 4) & 0x0F,
                initial_x=_s16(raw, offset + 0x0C) - camera_origin,
                initial_y=_s16(raw, offset + 0x0E) - camera_origin,
                current_x=current_raw_x - camera_origin,
                current_y=current_raw_y - camera_origin,
                previous_x=previous_raw_x - camera_origin,
                previous_y=previous_raw_y - camera_origin,
                facing_direction=direction & 0x0F,
                movement_direction=(direction >> 4) & 0x0F,
                range_x=range_value & 0x0F,
                range_y=(range_value >> 4) & 0x0F,
                field_effect_sprite_id=raw[offset + 0x1B],
                warp_arrow_sprite_id=raw[offset + 0x1C],
                movement_action_id=raw[offset + 0x1D],
                trainer_range_or_berry_id=raw[offset + 0x1E],
                current_metatile_behavior=raw[offset + 0x1F],
                previous_metatile_behavior=raw[offset + 0x20],
                previous_movement_direction=raw[offset + 0x21],
                direction_sequence_index=raw[offset + 0x22],
                player_copyable_movement=raw[offset + 0x23],
            )
        )
    return result


def read_live_objects(
    gba: Any,
    *,
    base: int = OBJECT_EVENTS_BASE,
    stride: int = OBJECT_EVENT_STRIDE,
    count: int = OBJECT_EVENT_COUNT,
    camera_origin: int = CAMERA_TILE_ORIGIN,
    include_inactive: bool = False,
) -> list[LiveObject]:
    """Read and decode all active runtime objects in one Lua-bridge call."""
    raw = gba.read_range(base, stride * count, name="object_events")
    return decode_live_objects(
        raw,
        base=base,
        stride=stride,
        count=count,
        camera_origin=camera_origin,
        include_inactive=include_inactive,
    )


def select_live_object(
    objects: Iterable[LiveObject],
    *,
    map_id: tuple[int, int] | None = None,
    slot: int | None = None,
    local_id: int | None = None,
    graphics_id: int | None = None,
    include_player: bool = False,
    predicate: Callable[[LiveObject], bool] | None = None,
    nearest_to: tuple[int, int] | None = None,
) -> LiveObject | None:
    """Select one object using stable runtime identity, not screen position."""
    candidates = []
    for obj in objects:
        if not obj.active or obj.invisible:
            continue
        if not include_player and obj.is_player:
            continue
        if map_id is not None and obj.map_id != map_id:
            continue
        if slot is not None and obj.slot != slot:
            continue
        if local_id is not None and obj.local_id != local_id:
            continue
        if graphics_id is not None and obj.graphics_id != graphics_id:
            continue
        if predicate is not None and not predicate(obj):
            continue
        candidates.append(obj)
    if not candidates:
        return None
    if nearest_to is None:
        return min(candidates, key=lambda obj: obj.slot)
    x, y = nearest_to
    return min(candidates, key=lambda obj: (abs(obj.current_x - x) + abs(obj.current_y - y), obj.slot))


def object_occupied_edges(
    objects: Iterable[LiveObject],
    *,
    ignore_slots: set[int] | frozenset[int] = frozenset(),
) -> set[tuple[tuple[int, int], str]]:
    """Return directed edges that would enter a currently occupied tile."""
    directions = ((0, -1, "UP"), (1, 0, "RIGHT"), (0, 1, "DOWN"), (-1, 0, "LEFT"))
    blocked: set[tuple[tuple[int, int], str]] = set()
    for obj in objects:
        if not obj.active or obj.invisible or obj.is_player or obj.slot in ignore_slots:
            continue
        x, y = obj.position
        for dx, dy, direction in directions:
            blocked.add(((x - dx, y - dy), direction))
    return blocked
