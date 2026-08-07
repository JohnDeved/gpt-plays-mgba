# gpt-plays-mgba

A control and observation layer for driving a development build of [mGBA](https://mgba.io/) from Lua over a localhost socket.

The project has two parallel goals: **complete Pokémon Run & Bun** and **continuously improve the interface used to observe and control the emulator**.

The current setup is designed for the ChatGPT Linux sandbox and has been tested with mGBA development build `0.11-9122-afd6f14ea` and Pokémon Run & Bun v1.07.

## Current interface: RPC v0.1

The recommended bridge is `scripts/mgba_rpc.lua`, with the Python client in `client/mgba_rpc.py`.

It currently provides:

- NDJSON RPC with request IDs and a capability handshake
- Frame-synchronized button presses and queued multi-step input sequences
- Action completion/status tracking
- 8-, 16-, and 32-bit bus memory reads/writes
- Batched memory reads and structured observations
- mGBA-native framebuffer screenshots through Lua
- Savestate save/load
- Reset and input clearing
- ROM title/code and frame metadata

The older `scripts/mgba_control.lua` / `client/mgba_client.py` text protocol is retained as a simple legacy/reference implementation.

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the interface-improvement plan.

## Launch in the sandbox

The sandbox has no real X server, so mGBA is run on Xvfb. The AppImage can be extracted first because FUSE is unavailable.

```bash
chmod +x mGBA-build-latest-appimage-x64.appimage
./mGBA-build-latest-appimage-x64.appimage --appimage-extract

Xvfb :99 -screen 0 1024x768x24 &
DISPLAY=:99 ./squashfs-root/AppRun \
  --script scripts/mgba_rpc.lua \
  /path/to/game.gba
```

Then from Python:

```python
from client.mgba_rpc import MGBA

with MGBA() as gba:
    print(gba.info())

    # Frame-synchronized input.
    gba.press("START")
    gba.sequence([
        {"keys": ["DOWN"], "frames": 2},
        {"wait": 4},
        {"keys": ["A"], "frames": 2},
    ])

    # One structured observation can include many reads and a screenshot.
    state = gba.observe(
        reads=[
            {"name": "ewram0", "address": 0x02000000, "width": 32},
            {"name": "iwram0", "address": 0x03000000, "width": 32},
        ],
        screenshot="/mnt/data/game.png",
    )
    print(state)
```

## RPC shape

Requests and responses are newline-delimited JSON.

```json
{"id":1,"op":"observe","params":{"reads":[{"name":"ewram0","address":33554432,"width":32}]}}
```

A response carries the matching request ID, success/error status, and emulator frame:

```json
{"id":1,"ok":true,"frame":12345,"result":{"title":"POKEMON EMER","code":"BPEE","frame":12345,"keys":0,"reads":[...]}}
```

Supported operations in v0.1 include:

- `ping`
- `info`
- `observe`
- `input.press`
- `input.sequence`
- `input.clear`
- `action.status`
- `memory.read`
- `memory.read_batch`
- `memory.write`
- `screenshot`
- `state.save`
- `state.load`
- `reset`

## Repository policy

ROMs, save files, savestates, extracted AppImages, generated screenshots, and other runtime artifacts are intentionally not tracked here. They live in separate working Drive/runtime storage.
