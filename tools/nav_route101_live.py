import sys
from collections import deque
sys.path.insert(0,'/mnt/data')
from mgba_rpc import MGBA
from runbun_adapter import RunBun

COLL_STR=[
'11111111000011111111','11111111000011111111','11000000000000001111','11000000000000001111',
'00000000000000000011','00000000000000000011','11000011111110000000','11111110000111100000',
'11000000000111100000','11110100000110000000','11110000000000000000','11110000000000000000',
'11110000000000000000','11000000111100000000','00000000000011000011','00000000000011000011',
'11000000000011111111','11000000000011111111','11111111110011111111','11111111110011111111']
ELEV_STR=[
'00000000333300000000','00000000333300000000','00333333333333330000','03333333333333330000',
'33333333333333333300','33333333333333333300','00333300000003333333','00000003333000033333',
'00333333333000033333','00003033333003333333','00003333333333333333','00003333333333333333',
'00003333333333333333','00333333000033333333','33333333333300333300','33333333333300333300',
'00333333333300000000','00333333333300000000','00000000003300000000','00000000003300000000']
coll=[[int(c) for c in r] for r in COLL_STR]
elev=[[int(c,16) for c in r] for r in ELEV_STR]
DIRS=[(0,-1,'UP'),(1,0,'RIGHT'),(0,1,'DOWN'),(-1,0,'LEFT')]

def plan(start, blocked_edges):
    goals={(x,0) for x in range(20) if coll[0][x]==0 and elev[0][x]==3}
    q=deque([start]); prev={start:None}; pd={}
    goal=None
    while q:
        cur=q.popleft()
        if cur in goals:
            goal=cur;break
        x,y=cur
        for dx,dy,d in DIRS:
            n=(x+dx,y+dy)
            if (cur,d) in blocked_edges: continue
            nx,ny=n
            if not (0<=nx<20 and 0<=ny<20): continue
            if coll[ny][nx]!=0 or elev[ny][nx]!=3: continue
            if n in prev: continue
            prev[n]=cur;pd[n]=d;q.append(n)
    if not goal: return None
    path=[];cur=goal
    while prev[cur] is not None:
        path.append(pd[cur]);cur=prev[cur]
    return path[::-1]

def step(g, rb, d, max_pulses=7):
    before=rb.player()
    for n in range(max_pulses):
        g.press(d,frames=4);g.wait_frames(6)
        after=rb.player()
        if after.map_id!=before.map_id or after.position!=before.position:
            g.wait_frames(12)
            return True,before,rb.player(),n+1
    return False,before,rb.player(),max_pulses

def resolve_battle(rb):
    for turn in range(16):
        obs=rb.observe(screenshot=f'/mnt/data/nav_battle_{turn}.png')
        if 'battle' not in obs:
            return
        b=obs['battle']
        print(' BATTLE',b['opponent']['nickname'],b['opponent']['level'],b['opponent']['hp'],'/',b['opponent']['max_hp'],'player',b['player']['hp'])
        st=rb.advance_battle_until_menu(prefix='/mnt/data/nav_battle_adv')
        s=st['state'] if isinstance(st,dict) else st
        if s=='battle_end':
            continue
        if s=='command_menu':
            rb.open_fight_menu(); rb.choose_move(0)
        elif s=='move_menu':
            rb.choose_move(0)
    raise RuntimeError('battle did not end')

with MGBA(timeout=5) as g:
    rb=RunBun(g)
    g.load_state('/mnt/data/route101_post_bunnelby.ss0');g.wait_frames(24)
    blocked=set();steps=0
    while rb.player().map_id==(0,16) and steps<120:
        obs=rb.observe(screenshot='/mnt/data/nav_current.png')
        if 'battle' in obs:
            resolve_battle(rb); g.save_state('/mnt/data/route101_live_progress.ss0'); continue
        p=rb.player(); path=plan(p.position,blocked)
        if path is None:
            raise RuntimeError(f'no path from {p.position}, blocked={blocked}')
        if p.y==0:
            print('AT NORTH EDGE',p)
            ok,b,a,np=step(g,rb,'UP')
            print(' EXIT UP',ok,b.position,b.map_id,'->',a.position,a.map_id)
            if a.map_id!=b.map_id: break
            blocked.add((p.position,'UP'));continue
        d=path[0]
        ok,b,a,np=step(g,rb,d);steps+=1
        print(f'{steps:02d}',b.position,d,'->',a.position,a.map_id,'pulses',np)
        obs=rb.observe(screenshot='/mnt/data/nav_after.png')
        if 'battle' in obs:
            resolve_battle(rb);g.save_state('/mnt/data/route101_live_progress.ss0');continue
        if not ok:
            blocked.add((b.position,d));print(' BLOCKED EDGE',b.position,d);continue
        g.save_state('/mnt/data/route101_live_progress.ss0')
    print('FINAL',rb.player())
    print('PARTY',rb.party())
    print('SAVE',g.save_state('/mnt/data/oldale_arrival.ss0'))
    rb.observe(screenshot='/mnt/data/oldale_arrival.png')
