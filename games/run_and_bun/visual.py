from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib

try:
    from PIL import Image, ImageChops, ImageStat
except ImportError:  # Pillow is optional for the RAM-first adapter.
    Image = ImageChops = ImageStat = None


@dataclass(frozen=True)
class VisualState:
    path: str
    sha1: str
    bottom_white_ratio: float
    bottom_textbox: bool
    battle_teal_ratio: float
    battle_hud: bool
    battle_textbox: bool
    battle_menu_like: bool
    battle_command_menu: bool
    prompt_red_pixels: int
    dialogue_ready: bool


def _require_pillow() -> None:
    if Image is None:
        raise RuntimeError(
            "Pillow is required for framebuffer inspection; "
            "RAM/task/text observations do not require it"
        )


def inspect_png(path: str | Path) -> VisualState:
    _require_pillow()
    path = str(path)
    im = Image.open(path).convert("RGB")
    raw = im.tobytes()
    # Text/dialog boxes in Emerald occupy almost the full bottom ~50 pixels and
    # are overwhelmingly white. This is deliberately only a coarse UI signal.
    crop = im.crop((0, max(0, im.height - 50), im.width, im.height))
    white = 0
    total = crop.width * crop.height
    for r, g, b in crop.getdata():
        if r > 220 and g > 220 and b > 220:
            white += 1
    ratio = white / total if total else 0.0
    # Standard Emerald dialogue completion prompt is a saturated red triangle.
    # Count only inside the textbox interior so overworld sprites do not pollute it.
    prompt_red = 0
    for y in range(max(0, im.height - 46), max(0, im.height - 1)):
        for x in range(3, max(3, im.width - 3)):
            r, g, b = im.getpixel((x, y))
            if r > 150 and g < 100 and b < 100:
                prompt_red += 1
    textbox = ratio > 0.45

    # Run & Bun's battle message window uses a distinctive muted teal fill, while
    # the move selector is predominantly white. These are intentionally coarse
    # signals used together with decoded battle RAM, not OCR.
    battle_crop = im.crop((2, max(0, im.height - 44), max(3, im.width - 2), max(1, im.height - 1)))
    battle_total = battle_crop.width * battle_crop.height
    battle_teal = 0
    battle_white = 0
    for r, g, b in battle_crop.getdata():
        if 80 <= r <= 140 and 130 <= g <= 190 and 130 <= b <= 190:
            battle_teal += 1
        if r > 220 and g > 220 and b > 220:
            battle_white += 1
    battle_teal_ratio = battle_teal / battle_total if battle_total else 0.0
    battle_white_ratio = battle_white / battle_total if battle_total else 0.0
    # Battle HUD: both opponent and player status panels have large white areas
    # in fixed GBA-screen regions. This separates battle menus from ordinary
    # overworld white dialogue boxes.
    def _white_ratio(crop):
        total = crop.width * crop.height
        white = sum(1 for r, g, b in crop.getdata() if r > 220 and g > 220 and b > 220)
        return white / total if total else 0.0
    enemy_hud_white = _white_ratio(im.crop((10, 15, min(110, im.width), min(45, im.height))))
    player_hud_white = _white_ratio(im.crop((130, 70, min(232, im.width), min(115, im.height))))
    enemy_hud = enemy_hud_white > 0.25
    player_hud = player_hud_white > 0.25
    # During battle intro only the opponent HUD may exist; after a KO only the
    # player HUD may remain. A teal battle textbox plus either HUD is still a
    # battle scene, while both HUDs identify the normal command/move phases.
    battle_hud = (enemy_hud and player_hud) or (battle_teal_ratio > 0.25 and (enemy_hud or player_hud))

    # Includes ordinary battle text and level-up/stat overlays. Menu detectors are
    # checked first by the controller, so the split command menu is not advanced.
    battle_textbox = battle_hud and battle_teal_ratio > 0.25
    battle_menu_like = battle_hud and battle_white_ratio > 0.50 and battle_teal_ratio < 0.10

    left = im.crop((2, max(0, im.height - 44), min(122, im.width), max(1, im.height - 1)))
    right = im.crop((min(122, im.width), max(0, im.height - 44), max(123, im.width - 2), max(1, im.height - 1)))
    def _ratios(crop):
        total = crop.width * crop.height
        teal = white = 0
        for r, g, b in crop.getdata():
            if 80 <= r <= 140 and 130 <= g <= 190 and 130 <= b <= 190:
                teal += 1
            if r > 220 and g > 220 and b > 220:
                white += 1
        return (teal / total if total else 0.0, white / total if total else 0.0)
    left_teal, left_white = _ratios(left)
    right_teal, right_white = _ratios(right)
    battle_command_menu = battle_hud and left_teal > 0.55 and right_white > 0.55 and right_teal < 0.05

    return VisualState(
        path=path,
        sha1=hashlib.sha1(raw).hexdigest(),
        bottom_white_ratio=ratio,
        bottom_textbox=textbox,
        battle_teal_ratio=battle_teal_ratio,
        battle_hud=battle_hud,
        battle_textbox=battle_textbox,
        battle_menu_like=battle_menu_like,
        battle_command_menu=battle_command_menu,
        prompt_red_pixels=prompt_red,
        dialogue_ready=textbox and prompt_red >= 15,
    )


def image_difference(a: str | Path, b: str | Path, *, ignore_right: int = 18, textbox_only: bool | None = None) -> float:
    """Mean per-channel absolute difference.

    If a bottom textbox is present, compare only that UI region so normal overworld
    sprite animations do not prevent a dialogue page from being considered stable.
    """
    _require_pillow()
    ia = Image.open(a).convert("RGB")
    ib = Image.open(b).convert("RGB")
    if ia.size != ib.size:
        return float("inf")
    w, h = ia.size
    if textbox_only is None:
        textbox_only = inspect_png(a).bottom_textbox and inspect_png(b).bottom_textbox
    if textbox_only:
        # The Emerald message box occupies roughly the bottom 50 pixels. Ignore
        # the far-right prompt-arrow area, which can blink independently.
        right = max(1, w - max(ignore_right, 22))
        ia = ia.crop((0, max(0, h - 50), right, h))
        ib = ib.crop((0, max(0, h - 50), right, h))
    elif ignore_right:
        ia = ia.crop((0, 0, max(1, w - ignore_right), h))
        ib = ib.crop((0, 0, max(1, w - ignore_right), h))
    diff = ImageChops.difference(ia, ib)
    stat = ImageStat.Stat(diff)
    return sum(stat.mean) / len(stat.mean)
