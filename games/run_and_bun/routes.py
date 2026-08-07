"""Verified static routes for Pokémon Run & Bun v1.07.

The Route 101 grid was directed-edge verified by the remote playthrough agent
and is kept here as offline knowledge. It plans a path immediately; the
emulator still confirms the resulting map and position after one macro action.
"""

from __future__ import annotations

from collections import deque


ROUTE101_COLLISION = (
    "11111111000011111111", "11111111000011111111",
    "11000000000000001111", "11000000000000001111",
    "00000000000000000011", "00000000000000000011",
    "11000011111110000000", "11111110000111100000",
    "11000000000111100000", "11110100000110000000",
    "11110000000000000000", "11110000000000000000",
    "11110000000000000000", "11000000111100000000",
    "00000000000011000011", "00000000000011000011",
    "11000000000011111111", "11000000000011111111",
    "11111111110011111111", "11111111110011111111",
)

ROUTE101_ELEVATION = (
    "00000000333300000000", "00000000333300000000",
    "00333333333333330000", "03333333333333330000",
    "33333333333333333300", "33333333333333333300",
    "00333300000003333333", "00000003333000033333",
    "00333333333000033333", "00003033333003333333",
    "00003333333333333333", "00003333333333333333",
    "00003333333333333333", "00333333000033333333",
    "33333333333300333300", "33333333333300333300",
    "00333333333300000000", "00333333333300000000",
    "00000000003300000000", "00000000003300000000",
)

_DIRECTIONS = ((0, -1, "UP"), (1, 0, "RIGHT"), (0, 1, "DOWN"), (-1, 0, "LEFT"))


def route101_path(
    start: tuple[int, int],
    *,
    target: tuple[int, int] | None = None,
) -> list[str]:
    """Return an offline collision-safe path to Route 101's north edge."""
    width = len(ROUTE101_COLLISION[0])
    height = len(ROUTE101_COLLISION)
    x, y = start
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError(f"Route 101 position outside grid: {start!r}")
    if target is None:
        goals = {
            (column, 0)
            for column in range(width)
            if ROUTE101_COLLISION[0][column] == "0"
            and ROUTE101_ELEVATION[0][column] == "3"
        }
    else:
        tx, ty = target
        if not (0 <= tx < width and 0 <= ty < height):
            raise ValueError(f"Route 101 target outside grid: {target!r}")
        goals = {target}

    queue = deque([start])
    previous: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    previous_direction: dict[tuple[int, int], str] = {}
    goal = None
    while queue:
        current = queue.popleft()
        if current in goals:
            goal = current
            break
        cx, cy = current
        for dx, dy, direction in _DIRECTIONS:
            nx, ny = cx + dx, cy + dy
            neighbor = (nx, ny)
            if not (0 <= nx < width and 0 <= ny < height):
                continue
            if ROUTE101_COLLISION[ny][nx] != "0":
                continue
            if ROUTE101_ELEVATION[ny][nx] != "3":
                continue
            if neighbor in previous:
                continue
            previous[neighbor] = current
            previous_direction[neighbor] = direction
            queue.append(neighbor)

    if goal is None:
        raise ValueError(f"No static Route 101 path from {start!r} to {target!r}")

    path: list[str] = []
    current = goal
    while previous[current] is not None:
        path.append(previous_direction[current])
        current = previous[current]  # type: ignore[assignment]
    path.reverse()
    return path
