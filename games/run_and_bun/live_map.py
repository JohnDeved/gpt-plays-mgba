"""Read and pathfind the live Run & Bun collision buffer.

The ROM keeps the currently loaded map's metatile/collision words in EWRAM.
That buffer is more authoritative than an imported vanilla map file because
Run & Bun changes map geometry.  A caller can read it once, solve a path
locally, and send the resulting directions as one Lua-bridge input sequence.
"""

from __future__ import annotations

from dataclasses import dataclass
import heapq
import struct
from typing import Any


# Verified against Run & Bun v1.07 while Route 103 was loaded.
LIVE_MAP_STRUCT = 0x03005DD0
LIVE_MAP_GRID_PTR_OFFSET = 0x08
LIVE_MAP_GRID_PTR = 0x020318DC
LIVE_MAP_ORIGIN = 7
# The runtime buffer is not symmetrically padded in this ROM: seven tiles
# precede the layout and eight follow it horizontally.  The old hard-coded
# height cap was removed, but using ``width - 2 * origin`` still exposed one
# padding column as a fake walkable edge on narrow maps (notably the Gym).
LIVE_MAP_RIGHT_PADDING = 8
LIVE_MAP_ACTIVE_WIDTH = 80
LIVE_MAP_ACTIVE_HEIGHT = 22

# The active Gen III map header/event table was verified in Run & Bun v1.07
# while Oldale Town and its indoor maps were loaded.  The event table gives
# us warp destinations directly, eliminating trial-and-error door probing.
LIVE_MAP_HEADER = 0x020368DC
MAP_HEADER_EVENTS_OFFSET = 0x04
MAP_EVENTS_WARP_COUNT_OFFSET = 0x01
MAP_EVENTS_WARPS_PTR_OFFSET = 0x08
LIVE_WARP_STRIDE = 0x08

_DIRECTIONS = ((0, -1, "UP"), (1, 0, "RIGHT"), (0, 1, "DOWN"), (-1, 0, "LEFT"))

# Encounter-bearing metatile IDs used by the Gen III field behavior table.
# The low ten bits of a runtime map word are the metatile ID, so this
# classification comes directly from the loaded game grid rather than from
# framebuffer pixels.
GRASS_METATILE_IDS = frozenset(
    {
        0x00D,  # General tall grass
        0x015,  # General long grass
        0x025,  # General tall-grass tree top
        0x1C6,
        0x1C7,  # General tall-grass tree edges
        0x208,  # Fortree long-grass root
        *range(0x279, 0x284),  # Fortree secret-base long-grass pieces
    }
)


@dataclass(frozen=True)
class LiveMap:
    """The runtime map grid and its coordinate transform."""

    width: int
    height: int
    grid_ptr: int
    words: tuple[int, ...]
    origin: int = LIVE_MAP_ORIGIN
    active_width: int = LIVE_MAP_ACTIVE_WIDTH
    active_height: int = LIVE_MAP_ACTIVE_HEIGHT

    def _index(self, x: int, y: int) -> int:
        gx = x + self.origin
        gy = y + self.origin
        if not (0 <= gx < self.width and 0 <= gy < self.height):
            raise ValueError(f"map position outside live grid: {(x, y)!r}")
        return gx + gy * self.width

    def word(self, x: int, y: int) -> int:
        return self.words[self._index(x, y)]

    def metatile_id(self, x: int, y: int) -> int:
        """Return the loaded map's raw metatile identity."""
        return self.word(x, y) & 0x03FF

    def collision(self, x: int, y: int) -> int:
        return (self.word(x, y) >> 10) & 0x03

    def elevation(self, x: int, y: int) -> int:
        return (self.word(x, y) >> 12) & 0x0F

    def walkable(self, x: int, y: int) -> bool:
        # Elevation is a movement layer, not a blocking flag.  Run & Bun's
        # bridges and connected-map tiles use elevations 0/1/4 while still
        # accepting ordinary movement; collision bits are the authoritative
        # static obstruction signal.
        return self.collision(x, y) == 0

    def step_allowed(self, start: tuple[int, int], target: tuple[int, int]) -> bool:
        """Return whether the runtime movement layer permits this edge.

        Collision-free tiles on different elevation layers are not
        automatically connected.  Run & Bun uses layers 0/1/4 for bridges
        and connected surfaces, while ordinary route ground is layer 3; the
        engine rejects a direct 3 -> 1 step even though both tiles have zero
        collision bits.  Keep the solver conservative until a per-metatile
        stair table is decoded.
        """
        if not self.walkable(*start) or not self.walkable(*target):
            return False
        source = self.elevation(*start)
        destination = self.elevation(*target)
        if source == destination:
            return True
        return source in {0, 1, 4} and destination in {0, 1, 4}

    def is_grass(self, x: int, y: int) -> bool:
        """Whether the loaded tile can trigger ordinary land encounters."""
        return self.metatile_id(x, y) in GRASS_METATILE_IDS

    def tile(self, x: int, y: int) -> dict[str, int | bool]:
        """Return all navigation-relevant fields for one runtime tile."""
        raw = self.word(x, y)
        collision = (raw >> 10) & 0x03
        elevation = (raw >> 12) & 0x0F
        metatile_id = raw & 0x03FF
        return {
            "x": x,
            "y": y,
            "raw": raw,
            "metatile_id": metatile_id,
            "collision": collision,
            "elevation": elevation,
            "walkable": collision == 0,
            "grass": metatile_id in GRASS_METATILE_IDS,
        }

    def ascii(self, *, start: tuple[int, int] | None = None, target: tuple[int, int] | None = None) -> str:
        """Render the decoded runtime layout as a compact text map."""
        rows: list[str] = []
        for y in range(self.active_height):
            row: list[str] = []
            for x in range(self.active_width):
                position = (x, y)
                if position == start:
                    row.append("S")
                elif position == target:
                    row.append("T")
                elif not self.walkable(x, y):
                    row.append("#")
                elif self.is_grass(x, y):
                    row.append('"')
                else:
                    row.append(".")
            rows.append("".join(row))
        return "\n".join(rows)

    def layout(self, *, include_tiles: bool = True, include_ascii: bool = True) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of the current map buffer."""
        result: dict[str, Any] = {
            "header_address": LIVE_MAP_STRUCT,
            "grid_ptr": self.grid_ptr,
            "width": self.width,
            "height": self.height,
            "origin": self.origin,
            "active_width": self.active_width,
            "active_height": self.active_height,
            "collision_bits": [10, 11],
            "elevation_bits": [12, 15],
            "walkable_rule": "collision == 0 (elevation retained for routing diagnostics)",
            "grass_metatile_ids": sorted(GRASS_METATILE_IDS),
        }
        if include_ascii:
            result["ascii"] = self.ascii()
        if include_tiles:
            result["tiles"] = [
                [self.tile(x, y) for x in range(self.active_width)]
                for y in range(self.active_height)
            ]
        return result

    def path_to(
        self,
        start: tuple[int, int],
        target: tuple[int, int],
        *,
        active_bounds: tuple[int, int] | None = None,
        blocked_edges: set[tuple[tuple[int, int], str]] | None = None,
        allow_nonwalkable_start: bool = False,
        grass_penalty: int = 100,
    ) -> list[str]:
        """Return a collision-safe four-way path within the active map area.

        ``blocked_edges`` is intentionally separate from the static collision
        words.  It lets the live navigator temporarily avoid an edge occupied
        by a moving NPC without pretending that the NPC permanently changed
        the map geometry.

        Grass is a traversable but expensive tile by default. A finite penalty
        makes the solver avoid wild encounters whenever a reasonable
        non-grass route exists, while still allowing grass when it is the only
        way to the target. Pass ``grass_penalty=0`` for deliberate training.
        """
        if grass_penalty < 0:
            raise ValueError("grass_penalty must be non-negative")
        width, height = active_bounds or (self.active_width, self.active_height)
        sx, sy = start
        tx, ty = target
        for position in (start, target):
            x, y = position
            if not (0 <= x < width and 0 <= y < height):
                raise ValueError(f"map position outside active bounds: {position!r}")
        if not allow_nonwalkable_start and not self.walkable(*start):
            raise ValueError(f"start is not walkable in live grid: {start!r}")
        if not self.walkable(*target):
            raise ValueError(f"target is not walkable in live grid: {target!r}")

        queue: list[tuple[int, int, tuple[int, int]]] = [(0, 0, start)]
        sequence = 1
        distance: dict[tuple[int, int], int] = {start: 0}
        previous: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
        previous_direction: dict[tuple[int, int], str] = {}
        while queue:
            cost, _, current = heapq.heappop(queue)
            if cost != distance[current]:
                continue
            if current == target:
                break
            cx, cy = current
            for dx, dy, direction in _DIRECTIONS:
                nx, ny = cx + dx, cy + dy
                neighbor = (nx, ny)
                if blocked_edges and ((cx, cy), direction) in blocked_edges:
                    continue
                if not (0 <= nx < width and 0 <= ny < height):
                    continue
                if not self.walkable(nx, ny):
                    continue
                if not self.step_allowed(current, neighbor):
                    continue
                next_cost = cost + 1 + (grass_penalty if self.is_grass(nx, ny) else 0)
                if self.elevation(nx, ny) != 3:
                    next_cost += 1
                if next_cost >= distance.get(neighbor, 1 << 60):
                    continue
                distance[neighbor] = next_cost
                previous[neighbor] = current
                previous_direction[neighbor] = direction
                heapq.heappush(queue, (next_cost, sequence, neighbor))
                sequence += 1

        if target not in previous:
            raise ValueError(f"no live-grid path from {start!r} to {target!r}")
        path: list[str] = []
        current = target
        while previous[current] is not None:
            path.append(previous_direction[current])
            current = previous[current]  # type: ignore[assignment]
        path.reverse()
        return path


@dataclass(frozen=True)
class LiveWarp:
    """A warp event from the currently loaded map's runtime event table."""

    x: int
    y: int
    warp_id: int
    map_num: int
    map_group: int

    @property
    def destination(self) -> tuple[int, int]:
        return self.map_group, self.map_num

    def as_dict(self) -> dict[str, int | tuple[int, int]]:
        return {
            "x": self.x,
            "y": self.y,
            "warp_id": self.warp_id,
            "map_num": self.map_num,
            "map_group": self.map_group,
            "destination": self.destination,
        }


def read_live_map(gba: Any) -> LiveMap:
    """Read the runtime map header and all collision words in one range call."""
    header = gba.read_range(LIVE_MAP_STRUCT, 12)
    width, height, grid_ptr = struct.unpack_from("<III", header)
    if not (1 <= width <= 256 and 1 <= height <= 256):
        raise RuntimeError(f"invalid live map dimensions: {(width, height)!r}")
    if not (0x02000000 <= grid_ptr < 0x02040000):
        raise RuntimeError(f"invalid live map pointer: {grid_ptr:#x}")
    raw = gba.read_range(grid_ptr, width * height * 2)
    if len(raw) != width * height * 2:
        raise RuntimeError("live map grid read was truncated")
    words = struct.unpack(f"<{width * height}H", raw)
    # Padded buffer dimensions vary by map. Fixed caps hid lower city tiles
    # and made valid warps unreachable on newly loaded maps.
    active_width = width - LIVE_MAP_ORIGIN - LIVE_MAP_RIGHT_PADDING
    active_height = height - 2 * LIVE_MAP_ORIGIN
    if active_width <= 0 or active_height <= 0:
        raise RuntimeError(f"live map is smaller than its border: {(width, height)!r}")
    return LiveMap(
        width=width,
        height=height,
        grid_ptr=grid_ptr,
        words=words,
        active_width=active_width,
        active_height=active_height,
    )


def read_live_warps(gba: Any) -> list[LiveWarp]:
    """Decode the loaded map's warp events directly from RAM/ROM pointers.

    The game stores the current ``MapHeader`` in EWRAM. Its event pointer is
    followed to the ROM event table, whose warp records use the verified
    eight-byte Run & Bun layout: ``x``, ``y``, ``warp_id``, reserved byte,
    ``map_num``, ``map_group``.
    """
    header = gba.read_range(LIVE_MAP_HEADER, 0x08)
    events_ptr = struct.unpack_from("<I", header, MAP_HEADER_EVENTS_OFFSET)[0]
    if not (0x08000000 <= events_ptr < 0x0A000000):
        raise RuntimeError(f"invalid live map events pointer: {events_ptr:#x}")
    events = gba.read_range(events_ptr, 0x0C)
    warp_count = events[MAP_EVENTS_WARP_COUNT_OFFSET]
    warps_ptr = struct.unpack_from("<I", events, MAP_EVENTS_WARPS_PTR_OFFSET)[0]
    if warp_count == 0:
        return []
    if not (0x08000000 <= warps_ptr < 0x0A000000):
        raise RuntimeError(f"invalid live warp pointer: {warps_ptr:#x}")
    raw = gba.read_range(warps_ptr, warp_count * LIVE_WARP_STRIDE)
    result: list[LiveWarp] = []
    for index in range(warp_count):
        offset = index * LIVE_WARP_STRIDE
        x, y = struct.unpack_from("<hh", raw, offset)
        result.append(
            LiveWarp(
                x=x,
                y=y,
                warp_id=raw[offset + 0x04],
                map_num=raw[offset + 0x06],
                map_group=raw[offset + 0x07],
            )
        )
    return result


def live_map_path(gba: Any, start: tuple[int, int], target: tuple[int, int]) -> list[str]:
    """Read the active map and return a path without probing individual tiles."""
    return read_live_map(gba).path_to(start, target)
