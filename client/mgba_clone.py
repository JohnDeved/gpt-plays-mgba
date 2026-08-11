"""Disposable muted mGBA clones for savestate-backed interface tests."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
from typing import Iterator

from client.mgba_rpc import MGBA


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROM = ROOT / "runtime" / "run-bun" / "Pokemon Run & Bun (v1.07).gba"


def _hashes(paths: tuple[Path, ...]) -> dict[Path, str]:
    return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    os.kill(process.pid, signal.SIGCONT)
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def _stop_pid(pid: int) -> None:
    """Stop the exact listener owner used by a background LaunchServices start."""
    try:
        os.kill(pid, signal.SIGCONT)
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(60):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    os.kill(pid, signal.SIGKILL)


@contextmanager
def disposable_clone(
    state_path: str | Path,
    *,
    rom_path: str | Path = DEFAULT_ROM,
    protected_paths: tuple[str | Path, ...] = (),
    timeout: float = 15.0,
) -> Iterator[MGBA]:
    """Load a state in an isolated process and prove protected files unchanged."""
    state = Path(state_path).resolve()
    rom = Path(rom_path).resolve()
    protected = tuple(Path(path).resolve() for path in protected_paths) + (state,)
    before = _hashes(protected)
    port = _free_port()
    with tempfile.TemporaryDirectory(prefix="runbun-clone-") as directory:
        runtime = Path(directory)
        ready = runtime / "ready.txt"
        ui_ready = runtime / "ui-ready.txt"
        env = os.environ.copy()
        env.update({
            "MGBA_RPC_PORT": str(port),
            "MGBA_RPC_READY_FILE": str(ready),
            "MGBA_UI_READY_FILE": str(ui_ready),
            "MGBA_RUNTIME_DIR": str(runtime),
            "MGBA_START_STATE": str(state),
            "MGBA_START_STATE_SHA256": hashlib.sha256(state.read_bytes()).hexdigest(),
            "MGBA_MUTE": "1",
            "MGBA_UNCAPPED": "1",
            "MGBA_IDLE_STOP": "1",
            "MGBA_FOREGROUND": "0",
        })
        process = None
        gba = None
        emulator_pid = None
        try:
            for attempt in range(2):
                ready.unlink(missing_ok=True)
                ui_ready.unlink(missing_ok=True)
                process = subprocess.Popen(
                    [str(ROOT / "scripts" / "launch_mgba_macos.sh"), str(rom)],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                deadline = time.monotonic() + timeout
                while not ui_ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                if ui_ready.exists():
                    break
                exited = process.poll()
                for listener_pid in MGBA._listener_pids("127.0.0.1", port):
                    _stop_pid(listener_pid)
                _stop_process(process)
                if attempt:
                    if exited is not None:
                        raise RuntimeError(f"clone mGBA exited with {exited}")
                    raise TimeoutError(
                        "clone mGBA failed UI readiness twice "
                        f"(rpc_ready={ready.exists()}, ui_ready={ui_ready.exists()})"
                    )
                time.sleep(0.5)
            assert process is not None
            gba = MGBA(port=port, timeout=timeout)
            emulator_pid = gba._process_pid
            yield gba
        finally:
            if gba is not None:
                gba.close()
            if emulator_pid is not None:
                _stop_pid(emulator_pid)
            if process is not None:
                _stop_process(process)
            after = _hashes(protected)
            if after != before:
                changed = [str(path) for path in protected if before[path] != after[path]]
                raise RuntimeError(f"clone modified protected files: {changed}")
