from __future__ import annotations
import sys, shutil
from pathlib import Path
from collections import deque
sys.path.insert(0,'/mnt/data')
from mgba_rpc import MGBA
from runbun_adapter import RunBun

TMP=Path('/mnt/data/.navstates')
TMP.mkdir(exist_ok=True)
for p in TMP.glob('*.ss0'): p.unlink()
DIRS=('UP','RIGHT','DOWN','LEFT')

with MGBA(timeout=10) as g:
    rb=RunBun(g)
    start=rb.player()
    start_state=TMP/'n0.ss0'
    g.save_state(start_state)
    key=(start.map_id,start.x,start.y)
    q=deque([key])
    nodes={key:(start_state, [])}
    target_map=(0,10)
    found=None
    expanded=0
    while q and expanded<120 and found is None:
        k=q.popleft(); state,path=nodes[k]; expanded+=1
        for d in DIRS:
            g.load_state(state); g.wait_frames(2)
            before=rb.player()
            g.press(d,frames=10); g.wait_frames(14)
            after=rb.player()
            if after.map_id==target_map:
                found=path+[d]
                print('FOUND',found,'at',after)
                break
            if after.map_id!=before.map_id:
                # Ignore other map exits (e.g. back to Littleroot).
                continue
            nk=(after.map_id,after.x,after.y)
            if nk==k or nk in nodes:
                continue
            child=TMP/f'n{len(nodes)}.ss0'
            g.save_state(child)
            nodes[nk]=(child,path+[d])
            q.append(nk)
        if expanded%10==0:
            print('expanded',expanded,'queue',len(q),'nodes',len(nodes))
    # Restore original checkpoint, then execute only the discovered route.
    g.load_state(start_state); g.wait_frames(4)
    if found:
        print('executing route length',len(found))
        for i,d in enumerate(found):
            g.press(d,frames=10); g.wait_frames(10)
            p=rb.player()
            print(i,d,p.map_id,p.position)
            if p.map_id==target_map: break
        g.wait_frames(60)
        print('FINAL',rb.player())
        rb.observe(screenshot='/mnt/data/nav_route101_done.png')
    else:
        print('NO PATH', 'expanded',expanded,'nodes',len(nodes))
