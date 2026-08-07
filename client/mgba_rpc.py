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

    def read_range(self, address: int, length: int, name: str | None = None) -> bytes:
        params = {"address": address, "length": length}
        if name is not None:
            params["name"] = name
        result = self.call("memory.read_range", **params)
        return bytes.fromhex(result["data"])

    def read_range_batch(self, ranges):
        """Read named byte ranges and decode the bridge's compact hex payloads."""
        result = self.call("memory.read_range_batch", ranges=list(ranges))
        return [
            {**item, "data": bytes.fromhex(item["data"])}
            for item in result["ranges"]
        ]

    def inspect_text(
        self,
        address: int = 0x02021FC4,
        length: int = 0x3E8,
        *,
        printers_address: int = 0x0202018C,
        printer_stride: int = 0x24,
        printer_slots: int = 16,
    ):
        """Read a Gen III text buffer and its text-printer runtime state."""
        result = self.call(
            "text.inspect",
            address=address,
            length=length,
            printers_address=printers_address,
            printer_stride=printer_stride,
            printer_slots=printer_slots,
        )
        result["buffer"] = {
            **result["buffer"],
            "data": bytes.fromhex(result["buffer"]["data"]),
        }
        return result

    def inspect_tasks(
        self,
        address: int = 0x03005E10,
        stride: int = 0x28,
        slots: int = 16,
    ):
        """Read the Gen III task scheduler entries used by UI and field code."""
        return self.call(
            "tasks.inspect",
            address=address,
            stride=stride,
            slots=slots,
        )

    def write(self, address: int, value: int, width: int = 8):
        return self.call("memory.write", address=address, value=value, width=width)

    def snapshot(self, name: str, ranges, include_data: bool = False):
        snapshot = self.call(
            "memory.snapshot",
            name=name,
            ranges=list(ranges),
            include_data=include_data,
        )["snapshot"]
        if include_data:
            snapshot["ranges"] = [
                {**item, "data": bytes.fromhex(item["data"])}
                for item in snapshot["ranges"]
            ]
        return snapshot

    def diff(self, name: str):
        """Compare the current emulator memory with a named bridge snapshot."""
        return self.call("memory.diff", name=name)["diff"]

    def add_watch(
        self,
        name: str,
        address: int,
        width: int = 8,
        *,
        length: int | None = None,
    ):
        params = {"name": name, "address": address}
        if length is None:
            params["width"] = width
        else:
            params["length"] = length
        return self.call("watch.add", **params)["watch"]

    def remove_watch(self, name: str):
        return self.call("watch.remove", name=name)

    def list_watches(self):
        return self.call("watch.list")["watches"]

    def read_watches(self, names=None):
        params = {}
        if names is not None:
            params["names"] = list(names)
        return self.call("watch.read", **params)["watches"]

    def poll_events(self, after: int = 0, limit: int = 256):
        return self.call("events.poll", after=after, limit=limit)

    def wait_status(self, wait_id: int):
        return self.call("wait.status", id=wait_id)["wait"]

    def cancel_wait(self, wait_id: int):
        return self.call("wait.cancel", id=wait_id)["wait"]

    def wait_until(
        self,
        condition,
        *,
        timeout_frames: int = 300,
        timeout: float = 10.0,
        poll: float = 0.01,
    ):
        """Wait for a bridge-side condition using emulator-frame timeouts."""
        wait = self.call(
            "wait.until",
            condition=dict(condition),
            timeout_frames=timeout_frames,
        )["wait"]
        deadline = time.monotonic() + timeout
        while wait["state"] == "waiting":
            if time.monotonic() >= deadline:
                self.cancel_wait(wait["id"])
                raise TimeoutError(
                    f"wait {wait['id']} exceeded host timeout "
                    f"while waiting for {condition!r}"
                )
            time.sleep(poll)
            wait = self.wait_status(wait["id"])
        if wait["state"] == "timed_out":
            raise TimeoutError(
                f"wait {wait['id']} timed out after {timeout_frames} emulator frames"
            )
        if wait["state"] == "error":
            raise MGBAError(wait.get("error", f"wait {wait['id']} failed"))
        return wait

    def observe(
        self,
        reads=None,
        screenshot: bool | str = False,
        ranges=None,
        watches: bool = False,
        events_after: int | None = None,
        text: bool | dict = False,
        tasks: bool | dict = False,
    ):
        params = {}
        if reads is not None:
            params["reads"] = list(reads)
        if ranges is not None:
            params["ranges"] = list(ranges)
        if watches:
            params["watches"] = True
        if events_after is not None:
            params["events"] = True
            params["after_event"] = events_after
        if text:
            params["text"] = text if isinstance(text, dict) else True
        if tasks:
            params["tasks"] = tasks if isinstance(tasks, dict) else True
        if screenshot:
            params["screenshot"] = str(screenshot) if isinstance(screenshot, (str, Path)) else True
        result = self.call("observe", **params)
        if result.get("text", {}).get("buffer", {}).get("data") is not None:
            buffer = result["text"]["buffer"]
            result["text"]["buffer"] = {
                **buffer,
                "data": bytes.fromhex(buffer["data"]),
            }
        return result

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
