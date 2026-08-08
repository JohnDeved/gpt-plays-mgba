import unittest

from client.mgba_rpc import MGBA


class FakeMGBA(MGBA):
    def __init__(self):
        self.calls = []
        self.wait_states = []

    def call(self, op: str, **params):
        self.calls.append((op, params))
        if op == "info":
            return {"frame": 100}
        if op == "memory.read_range":
            return {
                "address": params["address"],
                "length": params["length"],
                "encoding": "hex",
                "data": "0001ff",
            }
        if op == "memory.read_range_batch":
            return {
                "ranges": [
                    {
                        "name": "a",
                        "address": 1,
                        "length": 2,
                        "encoding": "hex",
                        "data": "1020",
                    }
                ]
            }
        if op == "text.inspect":
            return {
                "buffer": {"address": 1, "length": 2, "encoding": "hex", "data": "bbff"},
                "printers": [],
            }
        if op == "tasks.inspect":
            return {"address": 0x03005E10, "stride": 0x28, "slots": 16, "tasks": []}
        if op == "memory.snapshot":
            return {"snapshot": {"name": params["name"], "ranges": params["ranges"]}}
        if op == "memory.diff":
            return {"diff": {"name": params["name"], "changed_bytes": 1}}
        if op == "watch.add":
            return {"watch": {"name": params["name"], "address": params["address"]}}
        if op == "watch.remove":
            return {"name": params["name"], "removed": True}
        if op in {"watch.list", "watch.read"}:
            return {"watches": []}
        if op == "events.poll":
            return {"events": [], "cursor": params["after"]}
        if op == "wait.until":
            return {
                "wait": {
                    "id": 4,
                    "state": "waiting",
                    "condition": params["condition"],
                }
            }
        if op == "wait.status":
            return {"wait": self.wait_states.pop(0)}
        if op == "wait.cancel":
            return {"wait": {"id": params["id"], "state": "cancelled"}}
        if op == "experiment.run":
            return {"experiment": {"id": 9, "state": "queued"}}
        if op == "experiment.status":
            return {
                "experiment": {
                    "id": params["id"],
                    "state": "done",
                    "captures": [],
                }
            }
        if op == "observe":
            return {"frame": 12, "ranges": params.get("ranges", [])}
        raise AssertionError(f"unexpected operation: {op}")


class MGBAClientTests(unittest.TestCase):
    def test_range_reads_decode_hex_payloads(self):
        gba = FakeMGBA()

        self.assertEqual(gba.read_range(0x02000000, 3), b"\x00\x01\xff")
        self.assertEqual(gba.read_range_batch([{"name": "a", "address": 1, "length": 2}])[0]["data"], b"\x10\x20")
        self.assertEqual(gba.calls[0], ("memory.read_range", {"address": 0x02000000, "length": 3}))

    def test_snapshot_diff_and_watches_map_to_rpc_operations(self):
        gba = FakeMGBA()

        gba.snapshot("before", [{"name": "ewram", "address": 1, "length": 4}])
        self.assertEqual(gba.diff("before")["changed_bytes"], 1)
        gba.add_watch("player_x", 2, width=16)
        gba.add_watch("tile_bytes", 3, length=8)
        gba.remove_watch("player_x")

        self.assertEqual(gba.calls[0][0], "memory.snapshot")
        self.assertEqual(gba.calls[3], ("watch.add", {"name": "tile_bytes", "address": 3, "length": 8}))
        self.assertEqual(gba.calls[-1], ("watch.remove", {"name": "player_x"}))

    def test_wait_until_polls_until_bridge_reports_done(self):
        gba = FakeMGBA()
        gba.wait_states = [
            {"id": 4, "state": "waiting"},
            {"id": 4, "state": "done", "finished_frame": 81},
        ]

        result = gba.wait_until({"type": "memory_changed", "address": 1}, poll=0)

        self.assertEqual(result["state"], "done")
        self.assertEqual([op for op, _ in gba.calls], ["wait.until", "wait.status", "wait.status"])

    def test_wait_until_cancels_when_host_timeout_expires(self):
        gba = FakeMGBA()
        gba.wait_states = [{"id": 4, "state": "waiting"}] * 3

        with self.assertRaises(TimeoutError):
            gba.wait_until({"type": "frame", "at_frame": 999}, timeout=0, poll=0)

        self.assertEqual(gba.calls[-1], ("wait.cancel", {"id": 4}))

    def test_observe_forwards_ranges_watches_and_event_cursor(self):
        gba = FakeMGBA()

        gba.observe(
            ranges=[{"name": "ewram", "address": 1, "length": 2}],
            watches=True,
            events_after=7,
        )

        self.assertEqual(
            gba.calls[-1],
            (
                "observe",
                {
                    "ranges": [{"name": "ewram", "address": 1, "length": 2}],
                    "watches": True,
                    "events": True,
                    "after_event": 7,
                },
            ),
        )

    def test_text_inspection_decodes_the_buffer_payload(self):
        gba = FakeMGBA()
        result = gba.inspect_text()
        self.assertEqual(result["buffer"]["data"], b"\xbb\xff")
        self.assertEqual(gba.calls[-1][0], "text.inspect")

    def test_task_inspection_maps_to_rpc_operation(self):
        gba = FakeMGBA()

        result = gba.inspect_tasks()

        self.assertEqual(result["stride"], 0x28)
        self.assertEqual(gba.calls[-1][0], "tasks.inspect")

    def test_wait_frames_uses_bridge_frame_condition(self):
        gba = FakeMGBA()

        result = gba.wait_frames(12, wait=False)

        self.assertEqual(result["state"], "waiting")
        self.assertEqual(
            gba.calls[-1],
            (
                "wait.until",
                {
                    "condition": {"type": "frame", "at_frame": 112},
                    "timeout_frames": 72,
                },
            ),
        )

    def test_atomic_experiment_round_trip(self):
        gba = FakeMGBA()

        result = gba.experiment(
            "/tmp/checkpoint.ss",
            [{"keys": ["RIGHT"], "frames": 4}],
            [{"name": "coords", "address": 1, "length": 8}],
        )

        self.assertEqual(result["state"], "done")
        self.assertEqual(
            gba.calls[0],
            (
                "experiment.run",
                {
                    "state_path": "/tmp/checkpoint.ss",
                    "steps": [{"keys": ["RIGHT"], "frames": 4}],
                    "captures": [{"name": "coords", "address": 1, "length": 8}],
                },
            ),
        )

if __name__ == "__main__":
    unittest.main()
