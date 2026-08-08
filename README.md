# gpt-plays-mgba

A control and observation layer for playing Pokémon Run & Bun through a development build of [mGBA](https://mgba.io/). The project has two parallel goals: complete the game and continuously improve the emulator interface so gameplay relies on structured game knowledge instead of repeated screenshots.

The checked-out macOS runtime has been tested with mGBA development build `0.11-9122-afd6f14ea` and Pokémon Run & Bun v1.07.

## RPC v0.3 bridge

The canonical bridge is `scripts/mgba_rpc.lua`, with the Python client in `client/mgba_rpc.py`. It provides:

- NDJSON RPC with request IDs and capability negotiation
- Frame-synchronized button presses and queued input sequences
- 8-, 16-, and 32-bit reads/writes, batch reads, and compact byte ranges
- Named memory snapshots and grouped byte-level diffs
- Scalar/range watches, frame-recorded change events, and conditional waits
- Text-buffer and Gen III text-printer inspection
- Gen III task-scheduler inspection
- Atomic savestate experiments: load a state, apply input, and capture RAM at the exact completion frame
- mGBA-native screenshots, savestate save/load, reset, and input clearing

Byte ranges are returned as lowercase hex on the socket and decoded to `bytes` by the Python client. A typical observation can combine semantic telemetry with raw evidence:

```python
from client.mgba_rpc import MGBA

with MGBA() as gba:
    state = gba.observe(
        reads=[{"name": "map_group", "address": 0x02031DBC, "width": 8}],
        ranges=[{"name": "player", "address": 0x02031DB8, "length": 8}],
        text=True,
        tasks=True,
        watches=True,
    )
    print(state)
```

Atomic experiments are useful for collision probing and reverse engineering:

```python
result = gba.experiment(
    "/path/to/checkpoint.ss",
    steps=[{"keys": ["RIGHT"], "frames": 8}],
    captures=[
        {"name": "save_block1", "address": 0x02031DB8, "length": 0x40},
        {"name": "tasks", "address": 0x03005E10, "length": 0x100},
    ],
)
```

The bridge-side experiment removes host timing races: the savestate load, input frames, and final captures happen inside the emulator frame callback.

Supported v0.3 operations include `observe`, `text.inspect`, `tasks.inspect`, `input.press`, `input.sequence`, `action.status`, `experiment.run`, `experiment.status`, `memory.read`, `memory.read_batch`, `memory.read_range`, `memory.read_range_batch`, `memory.write`, `memory.snapshot`, `memory.diff`, `watch.add`, `watch.remove`, `watch.list`, `watch.read`, `events.poll`, `wait.until`, `wait.status`, `wait.cancel`, `screenshot`, `state.save`, `state.load`, `ping`, `info`, and `reset`.

The older `scripts/mgba_control.lua` / `client/mgba_client.py` text protocol is retained as a legacy reference.

## Run & Bun adapters

`games/runbun.py` is the semantic bridge adapter used during the current playthrough. It combines verified pointers, text decoding, text-printer state, task telemetry, field-message modes, party decryption, and battle heuristics.

`games/run_and_bun/` contains the remote branch's broader dataclass adapter and ROM profile:

- `symbols.json` records Run & Bun v1.07 addresses and their scope.
- `state.py` decodes player/map state, party data, battle state, menus, and movement.
- `visual.py` provides framebuffer-derived HUD/state classifiers as a diagnostic fallback.
- `live_map.py` reads the active map's runtime collision/elevation buffer in one Lua-bridge range call and solves paths locally with a grass-aware Dijkstra search. It is the preferred navigation primitive on maps that are loaded in memory; it avoids per-tile screenshot/probe loops and remains safe for hack-specific geometry.
- `objects.py` decodes the verified live object-event table, including NPC identity, map-local position, movement state, facing, and transient occupancy.

The preferred observation order is semantic RAM/task/text telemetry, then raw ranges or watches. The local control path does not request screenshots; `visual.py` is retained only as an explicit diagnostic fallback for unresolved ROM states.

### Native capability catalog

`games/run_and_bun/capabilities.py` is the model-facing source of truth for
task-level game operations. It powers compact search/inspection and the
optional stdio MCP adapter:

```bash
python3 tools/capabilities.py --search "find a trainer and walk to it"
python3 tools/capabilities.py --inspect game_tactical_report
python3 tools/runbun_mcp.py  # MCP JSON-RPC over stdin/stdout
```

Use `capability_search` before a generic shell, screenshot, or ad-hoc bridge
workaround. `capability_inspect` returns the full schema, boundaries,
side-effect, and retry policy for one match. `authorize_fallback` is the
controller policy hook: it rejects a generic fallback when a native capability
matches the intent. Outputs stay compact by default; full map tiles and ASCII
are explicit opt-ins.

## Navigation and progress

`games/run_and_bun/routes.py` contains the verified Route 101 collision/elevation grid and plans paths offline. `games/run_and_bun/live_map.py` handles loaded maps generically from the live runtime grid. `tools/nav_route101_live.py` executes paths as compressed Lua-bridge macros, resolving random battles from RAM and confirming map transitions. `tools/nav_probe.py` remains available for genuinely unknown maps, but it is not part of normal navigation.

For a loaded map, `RunBunAdapter.follow_live_path((x, y))` reads the runtime
grid, plans a collision-safe path, and sends one compressed input sequence. It
returns the semantic endpoint observation; random battles can be handled and
the path replanned from the interrupted position.

`follow_live_path_adaptive((x, y))` sends short compressed chunks, confirms
SaveBlock1 coordinates after each chunk, temporarily blocks edges where an NPC
stops progress, and replans around moving blockers. Grass is a high-cost tile
by default, so ordinary travel avoids wild encounters; pass
`grass_penalty=0` only for deliberate encounter/training routes. Battle input
is RAM/text-printer driven: `choose_battle_action()` accounts for PP, known
effectiveness, and Flash Fire, while the battle state adapter exposes explicit
switch and Bag primitives.

`follow_live_path_to_npc()` selects a live object by `local_id`, `graphics_id`,
or slot, chooses a reachable tile beside it, blocks occupied object tiles, and
re-reads the target after every short movement chunk. `tools/seek_npc.py` is a
command-line entry point; `--list` prints discovered objects without taking a
screenshot, and `--interact` faces and talks to the target.

`RunBunAdapter.live_map_layout()` and `tools/dump_live_map.py` expose the
currently loaded layout directly from RAM: map-buffer dimensions and pointer,
raw metatile words, collision/elevation fields, walkability, grass
classification, and a compact text rendering. This works immediately after a
warp onto an unknown map; `follow_live_path()` and
`follow_live_path_adaptive()` use the same freshly read buffer for navigation.

The launcher runs mGBA at the native 59.7275 Hz target; the bridge does not
toggle frontend speed or send focus-stealing shortcuts. `MGBA.paused_scope()`
remains available for explicit idle blocks and suspends the emulator process
without changing focus.

The current handoff is recorded in `data/session_progress.json`; synchronization metadata is in `data/sync_manifest_2026-08-07.json`. Runtime ROMs, saves, screenshots, and savestates live in the project-local `runtime/` directory and remain outside Git.

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the interface-improvement plan.

## Launch on macOS

Use the checked-out launcher with the native runtime:

```bash
scripts/launch_mgba_macos.sh "/path/to/game.gba"
```

The launcher uses `runtime/mGBA.app` automatically. Set `MGBA_BIN=/path/to/mGBA` when the emulator is installed elsewhere. It starts the bridge on `127.0.0.1:8765`.

For the sandbox, run the extracted AppImage under Xvfb:

```bash
chmod +x mGBA-build-latest-appimage-x64.appimage
./mGBA-build-latest-appimage-x64.appimage --appimage-extract
Xvfb :99 -screen 0 1024x768x24 &
DISPLAY=:99 ./squashfs-root/AppRun --script scripts/mgba_rpc.lua /path/to/game.gba
```

## Design rule

Prefer one structured observation followed by one reasoned action. Whenever gameplay exposes a weakness in the interface, improve the interface rather than repeatedly working around it.
