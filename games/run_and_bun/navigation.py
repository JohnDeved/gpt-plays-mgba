from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Literal

from .visual import inspect_png


@dataclass(frozen=True)
class StepResult:
    kind: Literal['moved', 'map_change', 'battle', 'dialogue', 'menu', 'blocked', 'timeout']
    direction: str
    before_map: tuple[int, int]
    before_pos: tuple[int, int]
    after_map: tuple[int, int]
    after_pos: tuple[int, int]
    pulses: int
    frames: int

    @property
    def moved(self) -> bool:
        return self.kind in {'moved', 'map_change'}


class Navigator:
    """Transition-aware overworld movement for Run & Bun v1.07.

    A coordinate that does not change immediately is not automatically a wall:
    wild-battle transitions, fades, scripts, or menus can temporarily leave
    SaveBlock coordinates unchanged. Require a sustained clean interval before
    classifying a directed edge as blocked.
    """

    def __init__(self, runbun, scratch='/mnt/data/.nav-step.png'):
        self.rb=runbun; self.gba=runbun.gba; self.scratch=scratch

    def visual(self):
        self.gba.screenshot(self.scratch); return inspect_png(self.scratch)

    def step_or_event(self,direction:str,*,pulse_frames:int=4,max_pulses:int=4,sample_frames:int=6,transition_frames:int=120,clean_samples_for_blocked:int=4)->StepResult:
        direction=direction.upper()
        if direction not in {'UP','DOWN','LEFT','RIGHT'}:raise ValueError(direction)
        before=self.rb.player(); elapsed=0; clean=0
        for pulse in range(1,max_pulses+1):
            self.gba.press(direction,frames=pulse_frames); self.gba.wait_frames(sample_frames); elapsed+=pulse_frames+sample_frames
            after=self.rb.player()
            if after.map_id!=before.map_id:return StepResult('map_change',direction,before.map_id,before.position,after.map_id,after.position,pulse,elapsed)
            if after.position!=before.position:return StepResult('moved',direction,before.map_id,before.position,after.map_id,after.position,pulse,elapsed)
            v=self.visual()
            if v.battle_hud or v.battle_textbox or v.battle_intro_textbox:return StepResult('battle',direction,before.map_id,before.position,after.map_id,after.position,pulse,elapsed)
            if v.bottom_textbox:return StepResult('dialogue',direction,before.map_id,before.position,after.map_id,after.position,pulse,elapsed)
            if v.full_screen_menu:clean=0;continue
        while elapsed<transition_frames:
            self.gba.wait_frames(sample_frames);elapsed+=sample_frames;after=self.rb.player()
            if after.map_id!=before.map_id:return StepResult('map_change',direction,before.map_id,before.position,after.map_id,after.position,max_pulses,elapsed)
            if after.position!=before.position:return StepResult('moved',direction,before.map_id,before.position,after.map_id,after.position,max_pulses,elapsed)
            v=self.visual()
            if v.battle_hud or v.battle_textbox or v.battle_intro_textbox:return StepResult('battle',direction,before.map_id,before.position,after.map_id,after.position,max_pulses,elapsed)
            if v.bottom_textbox:return StepResult('dialogue',direction,before.map_id,before.position,after.map_id,after.position,max_pulses,elapsed)
            if v.full_screen_menu:clean=0;continue
            clean+=1
            if clean>=clean_samples_for_blocked:return StepResult('blocked',direction,before.map_id,before.position,after.map_id,after.position,max_pulses,elapsed)
        after=self.rb.player();return StepResult('timeout',direction,before.map_id,before.position,after.map_id,after.position,max_pulses,elapsed)

    @staticmethod
    def asdict(result:StepResult):return asdict(result)


# Verified Run & Bun v1.07 COMMON/IWRAM symbol. The live struct is:
#   s32 width; s32 height; u16 *map
# The backing map is padded by seven tiles around layout coordinates.
G_BACKUP_MAP_LAYOUT=0x03005DD0
MAP_OFFSET=7
MAP_OFFSET_W=15
MAP_OFFSET_H=14


@dataclass(frozen=True)
class GridCell:
    entry:int
    metatile_id:int
    collision:int
    elevation:int


class LiveMapGrid:
    """Read the current hack map grid directly from the running ROM.

    The live grid is a better proposal graph than vanilla map.bin because it
    includes Run & Bun's actual layout and runtime metatile edits. Collision bits
    do not encode object events or all directional behavior, so every planned
    edge is still validated by Navigator.step_or_event.
    """

    def __init__(self,runbun,layout_address:int=G_BACKUP_MAP_LAYOUT):
        self.rb=runbun;self.gba=runbun.gba;self.layout_address=layout_address
        self.width=self.gba.read32(layout_address);self.height=self.gba.read32(layout_address+4);self.map_ptr=self.gba.read32(layout_address+8)
        if not(15<self.width<512 and 14<self.height<512):raise RuntimeError(f'implausible live map dimensions {self.width}x{self.height}')
        if not(0x02000000<=self.map_ptr<0x02040000):raise RuntimeError(f'implausible live map pointer {self.map_ptr:#x}')
        self.layout_width=self.width-MAP_OFFSET_W;self.layout_height=self.height-MAP_OFFSET_H
        self._raw=self.gba.read_range(self.map_ptr,self.width*self.height*2)

    def entry(self,x:int,y:int)->int:
        if not(0<=x<self.layout_width and 0<=y<self.layout_height):raise IndexError((x,y))
        gx,gy=x+MAP_OFFSET,y+MAP_OFFSET;off=2*(gy*self.width+gx)
        return self._raw[off]|(self._raw[off+1]<<8)

    def cell(self,x:int,y:int)->GridCell:
        e=self.entry(x,y);return GridCell(e,e&0x03FF,(e>>10)&0x3,(e>>12)&0xF)

    def collision_passable(self,x:int,y:int)->bool:return self.cell(x,y).collision==0

    def collision_path(self,start:tuple[int,int],targets,*,blocked_edges=None)->list[str]|None:
        """Shortest collision-only proposal path in layout coordinates."""
        from collections import deque
        targets=set(targets);blocked_edges=set(blocked_edges or ())
        if start in targets:return []
        dirs=(('UP',(0,-1)),('DOWN',(0,1)),('LEFT',(-1,0)),('RIGHT',(1,0)))
        q=deque([start]);prev={start:None};via={};found=None
        while q:
            cur=q.popleft()
            for name,(dx,dy) in dirs:
                nxt=(cur[0]+dx,cur[1]+dy)
                if (cur,nxt) in blocked_edges or nxt in prev:continue
                x,y=nxt
                if not(0<=x<self.layout_width and 0<=y<self.layout_height):continue
                if not self.collision_passable(x,y):continue
                prev[nxt]=cur;via[nxt]=name
                if nxt in targets:found=nxt;q.clear();break
                q.append(nxt)
        if found is None:return None
        path=[];cur=found
        while prev[cur] is not None:path.append(via[cur]);cur=prev[cur]
        path.reverse();return path
