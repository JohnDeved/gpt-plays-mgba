"""Run the verified Route 101 path with one batched bridge action per leg."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from client.mgba_rpc import MGBA
from games.run_and_bun.routes import route101_path
from games.run_and_bun.state import RunBun
from games.runbun import RunBunAdapter


def resolve_battles(gba: MGBA, adapter: RunBunAdapter, state: RunBun) -> None:
    for turn in range(16):
        status = adapter.advance_battle_until_menu(sample_frames=24, max_frames=1200)
        print("BATTLE", turn, status)
        if status["state"] == "battle_end":
            print("BATTLE_DRAIN", adapter.finish_battle_after_ko())
            return
        if status["state"] != "command_menu":
            raise RuntimeError(f"battle controller stopped in {status['state']}")
        battle = state.battle()
        if not battle.player.move_ids[0]:
            raise RuntimeError("first battle move slot is empty")
        state.open_fight_menu()
        state.choose_move(0)
        gba.wait_frames(30)
    raise RuntimeError("battle did not end after 16 turns")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-state", type=Path)
    parser.add_argument("--save-state", type=Path)
    args = parser.parse_args()

    runtime_dir = Path(os.environ.get("MGBA_RUNTIME_DIR", REPO_ROOT.parent / "runtime" / "session"))
    start_state = args.start_state
    save_state = args.save_state or runtime_dir / "route101-progress.ss"

    with MGBA(timeout=15) as gba:
        state = RunBun(gba)
        adapter = RunBunAdapter(gba)
        if start_state:
            gba.load_state(start_state)
            gba.wait_frames(4)

        while state.player().map_id == (0, 16):
            player = state.player()
            path = route101_path(player.position)
            print("ROUTE101", player.position, "steps", len(path))
            result = adapter.follow_route(path, expected_map=(0, 16), transition_frames=120)
            if result["state"]["battle"]["active"]:
                resolve_battles(gba, adapter, state)
                continue
            if result["position"][1] != 0:
                raise RuntimeError(f"static route stopped unexpectedly at {result['position']}")
            exit_result = adapter.follow_route(
                ["UP", "UP"],
                transition_frames=150,
            )
            if exit_result["map"] != (0, 16):
                break

        final = adapter.observe()
        print("FINAL", final["save"]["block1"], final["mode"])
        gba.save_state(save_state)


if __name__ == "__main__":
    main()
