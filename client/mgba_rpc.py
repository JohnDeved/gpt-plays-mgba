#!/usr/bin/env python3
from __future__ import annotations
import json
import socket
import time
from pathlib import Path
from typing import Iterable, Optional

class MGBAError(RuntimeError):
    pass

class MGBA:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765, timeout: float = 3.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.file = self.sock.makefile("rwb", buffering=0)
        self._next_id = 1
        self.hello = self._read()
        if self.hello.get("type") != "hello":
            raise MGBAError(f"unexpected handshake: {self.hello}")

    def _read(self):
        line = self.file.readline()
        if not line:
            raise MGBAError("connection closed")
        return json.loads(line)

    def call(self, operation: str, **params):
        request_id = self._next_id
        self._next_id += 1
        request = {"id": request_id, "op": operation, "params": params}
        self.file.write((json.dumps(request, separators=(",", ":")) + "\n").encode())
        response = self._read()
        if response.get("id") != request_id:
            raise MGBAError(f"RPC id mismatch: {response}")
        if not response.get("ok"):
            raise MGBAError(response.get("error", "unknown RPC error"))
        return response["result"]

    def close(self):
        try: self.file.close()
        finally: self.sock.close()
    def __enter__(self): return self
    def __exit__(self, *_): self.close()

    def ping(self): return self.call("ping")
    def info(self): return self.call("info")
    def reset(self): return self.call("reset")
    def clear_input(self): return self.call("input.clear")

    def press(self, key: str, frames: int = 2, wait: bool = True):
        action = self.call("input.press", key=key, frames=frames)["action"]
        return self.wait_action(action["id"]) if wait else action

    def sequence(self, steps, wait: bool = True):
        action = self.call("input.sequence", steps=steps)["action"]
        return self.wait_action(action["id"]) if wait else action

    def action_status(self, action_id: int):
        return self.call("action.status", id=action_id)["action"]

    def wait_action(self, action_id: int, timeout: float = 5.0, poll: float = 0.01):
        deadline = time.monotonic() + timeout
        while True:
            action = self.action_status(action_id)
            if action["state"] == "done": return action
            if time.monotonic() >= deadline: raise TimeoutError(f"action {action_id} did not finish")
            time.sleep(poll)

    def read(self, address: int, width: int = 8):
        return self.call("memory.read", address=address, width=width)["value"]
    def read8(self, address): return self.read(address, 8)
    def read16(self, address): return self.read(address, 16)
    def read32(self, address): return self.read(address, 32)

    def read_batch(self, reads):
        return self.call("memory.read_batch", reads=list(reads))["reads"]

    def read_range(self, address: int, length: int) -> bytes:
        result = self.call("memory.read_range", address=address, length=length)
        return bytes.fromhex(result["hex"])

    def snapshot(self, address: int, length: int, name: str | None = None):
        params = {"address": address, "length": length}
        if name is not None: params["name"] = name
        return self.call("memory.snapshot", **params)["snapshot"]

    def diff(self, snapshot_id: int, max_changes: int = 256):
        return self.call("memory.diff", id=snapshot_id, max_changes=max_changes)

    def drop_snapshot(self, snapshot_id: int):
        return self.call("memory.snapshot_drop", id=snapshot_id)

    def watch(self, name: str, address: int, width: int = 8):
        return self.call("watch.set", name=name, address=address, width=width)["watch"]

    def unwatch(self, name: str):
        return self.call("watch.remove", name=name)

    def watches(self):
        return self.call("watch.list")["watches"]

    def create_wait(self, **condition):
        return self.call("wait.create", **condition)["wait"]

    def wait_status(self, wait_id: int):
        return self.call("wait.status", id=wait_id)["wait"]

    def wait_for(self, wait_id: int, timeout: float = 10.0, poll: float = 0.01):
        deadline = time.monotonic() + timeout
        while True:
            state = self.wait_status(wait_id)
            if state["state"] == "done": return state
            if state["state"] == "timeout": raise TimeoutError(f"emulator wait {wait_id} timed out at frame {state.get('finished_frame')}")
            if time.monotonic() >= deadline: raise TimeoutError(f"client wait {wait_id} timed out")
            time.sleep(poll)

    def wait_memory(self, address: int, *, width: int = 8, op: str = "changed", value: int | None = None, mask: int | None = None, timeout_frames: int = 600, wait: bool = True):
        params = {"kind": "memory", "address": address, "width": width, "op": op, "timeout_frames": timeout_frames}
        if value is not None: params["value"] = value
        if mask is not None: params["mask"] = mask
        obj = self.create_wait(**params)
        return self.wait_for(obj["id"], timeout=max(3.0, timeout_frames / 30.0)) if wait else obj

    def wait_frames(self, frames: int, *, timeout_frames: int | None = None, wait: bool = True):
        obj = self.create_wait(kind="frame", frames=frames, timeout_frames=timeout_frames or frames + 60)
        return self.wait_for(obj["id"], timeout=max(3.0, frames / 30.0)) if wait else obj

    def experiment(self, state_path, steps, captures, *, wait: bool = True, timeout: float = 10.0):
        exp = self.call(
            "experiment.run",
            state_path=str(state_path),
            steps=list(steps),
            captures=list(captures),
        )["experiment"]
        return self.wait_experiment(exp["id"], timeout=timeout) if wait else exp

    def experiment_status(self, experiment_id: int):
        return self.call("experiment.status", id=experiment_id)["experiment"]

    def wait_experiment(self, experiment_id: int, timeout: float = 10.0, poll: float = 0.01):
        deadline = time.monotonic() + timeout
        while True:
            exp = self.experiment_status(experiment_id)
            if exp["state"] == "done": return exp
            if exp["state"] == "error": raise MGBAError(exp.get("error", "experiment failed"))
            if time.monotonic() >= deadline: raise TimeoutError(f"experiment {experiment_id} did not finish")
            time.sleep(poll)

    def write(self, address: int, value: int, width: int = 8):
        return self.call("memory.write", address=address, value=value, width=width)

    def observe(self, reads=None, screenshot: bool | str = False, watches: bool = False):
        params = {}
        if reads is not None: params["reads"] = list(reads)
        if watches: params["watches"] = True
        if screenshot: params["screenshot"] = str(screenshot) if isinstance(screenshot, (str, Path)) else True
        return self.call("observe", **params)

    def screenshot(self, path):
        return Path(self.call("screenshot", path=str(path))["path"])

    def save_state(self, path, flags=None):
        params = {"path": str(path)}
        if flags is not None: params["flags"] = flags
        return self.call("state.save", **params)
    def load_state(self, path, flags=None):
        params = {"path": str(path)}
        if flags is not None: params["flags"] = flags
        return self.call("state.load", **params)

if __name__ == "__main__":
    with MGBA() as gba:
        print("hello:", json.dumps(gba.hello, indent=2))
        print("info:", json.dumps(gba.info(), indent=2))
        print("observe:", json.dumps(gba.observe(reads=[{"name":"ewram0","address":0x02000000,"width":32}]), indent=2))
