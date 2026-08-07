#!/usr/bin/env python3
from __future__ import annotations

import json
import socket
import time
from pathlib import Path


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

    def call(self, op: str, **params):
        request_id = self._next_id
        self._next_id += 1
        request = {"id": request_id, "op": op, "params": params}
        self.file.write((json.dumps(request, separators=(",", ":")) + "\n").encode())
        response = self._read()
        if response.get("id") != request_id:
            raise MGBAError(f"RPC id mismatch: {response}")
        if not response.get("ok"):
            raise MGBAError(response.get("error", "unknown RPC error"))
        return response["result"]

    def close(self):
        try:
            self.file.close()
        finally:
            self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def ping(self):
        return self.call("ping")

    def info(self):
        return self.call("info")

    def reset(self):
        return self.call("reset")

    def clear_input(self):
        return self.call("input.clear")

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
            if action["state"] == "done":
                return action
            if time.monotonic() >= deadline:
                raise TimeoutError(f"action {action_id} did not finish")
            time.sleep(poll)

    def read(self, address: int, width: int = 8):
        return self.call("memory.read", address=address, width=width)["value"]

    def read8(self, address):
        return self.read(address, 8)

    def read16(self, address):
        return self.read(address, 16)

    def read32(self, address):
        return self.read(address, 32)

    def read_batch(self, reads):
        return self.call("memory.read_batch", reads=list(reads))["reads"]

    def write(self, address: int, value: int, width: int = 8):
        return self.call("memory.write", address=address, value=value, width=width)

    def observe(self, reads=None, screenshot: bool | str = False):
        params = {}
        if reads is not None:
            params["reads"] = list(reads)
        if screenshot:
            params["screenshot"] = str(screenshot) if isinstance(screenshot, (str, Path)) else True
        return self.call("observe", **params)

    def screenshot(self, path):
        return Path(self.call("screenshot", path=str(path))["path"])

    def save_state(self, path, flags=None):
        params = {"path": str(path)}
        if flags is not None:
            params["flags"] = flags
        return self.call("state.save", **params)

    def load_state(self, path, flags=None):
        params = {"path": str(path)}
        if flags is not None:
            params["flags"] = flags
        return self.call("state.load", **params)


if __name__ == "__main__":
    with MGBA() as gba:
        print("hello:", json.dumps(gba.hello, indent=2))
        print("info:", json.dumps(gba.info(), indent=2))
        print(
            "observe:",
            json.dumps(
                gba.observe(reads=[{"name": "ewram0", "address": 0x02000000, "width": 32}]),
                indent=2,
            ),
        )
