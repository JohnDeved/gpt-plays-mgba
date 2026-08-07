from __future__ import annotations
import argparse
import os
import sys, shutil
from pathlib import Path
from collections import deque

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from client.mgba_rpc import MGBA
from games.run_and_bun.state import RunBun

DIRS=('UP','RIGHT','DOWN','LEFT')

def parse_map(value: str):
    try:
        group, number = (int(part, 0) for part in value.split(',', 1))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("map must be GROUP,NUMBER") from exc
    return group, number


def main():
    parser = argparse.ArgumentParser(description="Probe a map graph with savestate branches.")
    parser.add_argument("--target-map", type=parse_map, default=(0, 10), help="target map as GROUP,NUMBER")
    parser.add_argument("--start-state", type=Path, help="optional savestate to probe from")
    parser.add_argument("--state-dir", type=Path, help="directory for temporary probe states")
    parser.add_argument("--max-expanded", type=int, default=120)
    args = parser.parse_args()

    runtime_dir = Path(os.environ.get("MGBA_RUNTIME_DIR", REPO_ROOT.parent / "runtime" / "session"))
    start_state = args.start_state or runtime_dir / "nav_probe_start.ss"
    if not start_state.is_absolute():
        start_state = Path.cwd() / start_state
    tmp = args.state_dir or runtime_dir / ".navstates"
    if not tmp.is_absolute():
        tmp = Path.cwd() / tmp
    tmp.mkdir(parents=True, exist_ok=True)
    for p in tmp.glob("*.ss"):
        p.unlink()

    target_map = args.target_map
    max_expanded = args.max_expanded

    with MGBA(timeout=10) as g:
        rb=RunBun(g)
        if start_state.exists():
            g.load_state(start_state)
            g.wait_frames(2)
        else:
            g.save_state(start_state)
        start=rb.player()
        root=tmp/'n0.ss'
        g.save_state(root)
        key=(start.map_id,start.x,start.y)
        q=deque([key])
        nodes={key:(root, [])}
        found=None
        expanded=0
        while q and expanded<max_expanded and found is None:
            k=q.popleft(); state,path=nodes[k]; expanded+=1
            for d in DIRS:
                # Each branch is an atomic bridge experiment: savestate load,
                # input frames, and completion are synchronized in Lua.
                g.experiment(
                    state,
                    [{"keys": [d], "frames": 12}, {"keys": [], "frames": 8}],
                    [],
                    timeout=10,
                )
                after=rb.player()
                if after.map_id==target_map:
                    found=path+[d]
                    print('FOUND',found,'at',after)
                    break
                if after.map_id!=k[0]:
                    continue
                nk=(after.map_id,after.x,after.y)
                if nk==k or nk in nodes:
                    continue
                child=tmp/f'n{len(nodes)}.ss'
                g.save_state(child)
                nodes[nk]=(child,path+[d])
                q.append(nk)
            if expanded%10==0:
                print('expanded',expanded,'queue',len(q),'nodes',len(nodes))
        g.load_state(root); g.wait_frames(4)
        if found:
            print('executing route length',len(found))
            for i,d in enumerate(found):
                g.press(d,frames=10); g.wait_frames(10)
                p=rb.player()
                print(i,d,p.map_id,p.position)
                if p.map_id==target_map: break
            g.wait_frames(30)
            print('FINAL',rb.player())
        else:
            print('NO PATH', 'expanded',expanded,'nodes',len(nodes))


if __name__ == "__main__":
    main()
