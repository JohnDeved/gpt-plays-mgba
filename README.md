# gpt-plays-mgba

A small control layer for driving a development build of [mGBA](https://mgba.io/) from Lua over a localhost socket.

The current setup is designed for the ChatGPT Linux sandbox and has been tested with mGBA development build `0.11-9122-afd6f14ea`.

## What it can do

- Launch a ROM with an mGBA Lua startup script
- Press/release GBA buttons
- Read/write 8-, 16-, and 32-bit bus memory
- Read the current frame, game title, game code, and key state
- Reset the emulator
- Capture screenshots through mGBA's Lua `emu:screenshot()` API
- Control the emulator from Python over `127.0.0.1:8765`

## Files

- `scripts/mgba_control.lua` — Lua bridge loaded with mGBA's `--script` flag
- `client/mgba_client.py` — Python client for the socket protocol

## Launch in the sandbox

The sandbox has no real X server, so mGBA is run on Xvfb. The AppImage can be extracted first because FUSE is unavailable.

```bash
chmod +x mGBA-build-latest-appimage-x64.appimage
./mGBA-build-latest-appimage-x64.appimage --appimage-extract

Xvfb :99 -screen 0 1024x768x24 &
DISPLAY=:99 ./squashfs-root/AppRun \
  --script scripts/mgba_control.lua \
  /path/to/game.gba
```

Then from Python:

```python
from client.mgba_client import MGBAClient

with MGBAClient() as gba:
    print(gba.title())
    gba.press("START")
    print(hex(gba.read8(0x02000000)))
    gba.screenshot("/mnt/data/game.png")
```

## Socket protocol

The bridge currently accepts:

```text
PING
FRAME
TITLE
CODE
KEYS
RESET
KEYDOWN <A|B|SELECT|START|RIGHT|LEFT|UP|DOWN|R|L>
KEYUP <key>
PRESS <key> [frames]
READ8 <address>
READ16 <address>
READ32 <address>
WRITE8 <address> <value>
WRITE16 <address> <value>
WRITE32 <address> <value>
SCREENSHOT <path>
```

Numeric addresses and values can be decimal or `0x`-prefixed hexadecimal.

## Repository policy

ROMs, save files, savestates, extracted AppImages, and generated screenshots are intentionally not tracked here. They live in the separate working Drive/runtime storage.
