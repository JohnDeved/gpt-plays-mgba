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

    An unchanged coordinate is not automatically a wall: a wild-battle
    transition, field-poison fade, script, or menu can temporarily leave
    SaveBlock coordinates unchanged. This helper requires a sustained clean
    interval before classifying a directed edge as blocked.
    """

    def __init__(self, runbun, scratch='/mnt/data/.nav-step.png'):
        self.rb = runbun
        self.gba = runbun.gba
        self.scratch = scratch

    def visual(self):
        self.gba.screenshot(self.scratch)
        return inspect_png(self.scratch)

    def step_or_event(self, direction: str, *, pulse_frames: int = 4,
                      max_pulses: int = 4, sample_frames: int = 6,
                      transition_frames: int = 120,
                      clean_samples_for_blocked: int = 4) -> StepResult:
        direction = direction.upper()
        if direction not in {'UP', 'DOWN', 'LEFT', 'RIGHT'}:
            raise ValueError(direction)
        before = self.rb.player()
        elapsed = clean = 0

        for pulse in range(1, max_pulses + 1):
            self.gba.press(direction, frames=pulse_frames)
            self.gba.wait_frames(sample_frames)
            elapsed += pulse_frames + sample_frames
            after = self.rb.player()
            if after.map_id != before.map_id:
                return StepResult('map_change', direction, before.map_id, before.position,
                                  after.map_id, after.position, pulse, elapsed)
            if after.position != before.position:
                return StepResult('moved', direction, before.map_id, before.position,
                                  after.map_id, after.position, pulse, elapsed)
            v = self.visual()
            if v.battle_hud or v.battle_textbox:
                return StepResult('battle', direction, before.map_id, before.position,
                                  after.map_id, after.position, pulse, elapsed)
            if v.bottom_textbox:
                return StepResult('dialogue', direction, before.map_id, before.position,
                                  after.map_id, after.position, pulse, elapsed)
            if v.full_screen_menu:
                # A one-frame visual hit may be a poison/warp fade. Menus should
                # persist, so defer classification to the sustained loop below.
                clean = 0
                continue

        while elapsed < transition_frames:
            self.gba.wait_frames(sample_frames)
            elapsed += sample_frames
            after = self.rb.player()
            if after.map_id != before.map_id:
                return StepResult('map_change', direction, before.map_id, before.position,
                                  after.map_id, after.position, max_pulses, elapsed)
            if after.position != before.position:
                return StepResult('moved', direction, before.map_id, before.position,
                                  after.map_id, after.position, max_pulses, elapsed)
            v = self.visual()
            if v.battle_hud or v.battle_textbox:
                return StepResult('battle', direction, before.map_id, before.position,
                                  after.map_id, after.position, max_pulses, elapsed)
            if v.bottom_textbox:
                return StepResult('dialogue', direction, before.map_id, before.position,
                                  after.map_id, after.position, max_pulses, elapsed)
            if v.full_screen_menu:
                clean = 0
                continue
            clean += 1
            if clean >= clean_samples_for_blocked:
                return StepResult('blocked', direction, before.map_id, before.position,
                                  after.map_id, after.position, max_pulses, elapsed)

        after = self.rb.player()
        return StepResult('timeout', direction, before.map_id, before.position,
                          after.map_id, after.position, max_pulses, elapsed)

    @staticmethod
    def asdict(result: StepResult):
        return asdict(result)
