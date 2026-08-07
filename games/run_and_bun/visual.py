from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from PIL import Image, ImageChops, ImageStat
import hashlib


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
    bag_menu: bool
    start_menu: bool
    party_menu: bool
    shop_menu: bool
    full_screen_menu: bool
    prompt_red_pixels: int
    dialogue_ready: bool


def inspect_png(path: str | Path) -> VisualState:
    path = str(path)
    im = Image.open(path).convert("RGB")
    raw = im.tobytes()
    crop = im.crop((0, max(0, im.height - 50), im.width, im.height))
    white = 0
    total = crop.width * crop.height
    for r, g, b in crop.getdata():
        if r > 220 and g > 220 and b > 220:
            white += 1
    ratio = white / total if total else 0.0
    prompt_red = 0
    for y in range(max(0, im.height - 46), max(0, im.height - 1)):
        for x in range(3, max(3, im.width - 3)):
            r, g, b = im.getpixel((x, y))
            if r > 150 and g < 100 and b < 100:
                prompt_red += 1
    textbox = ratio > 0.45

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

    def _white_ratio(crop):
        total = crop.width * crop.height
        white = sum(1 for r, g, b in crop.getdata() if r > 220 and g > 220 and b > 220)
        return white / total if total else 0.0

    enemy_hud_white = _white_ratio(im.crop((10, 15, min(110, im.width), min(45, im.height))))
    player_hud_white = _white_ratio(im.crop((130, 70, min(232, im.width), min(115, im.height))))
    enemy_hud = enemy_hud_white > 0.25
    player_hud = player_hud_white > 0.25
    battle_hud = (enemy_hud and player_hud) or (battle_teal_ratio > 0.25 and (enemy_hud or player_hud))
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

    # The Gen III Bag is a full-screen UI. Coordinates remain populated while it
    # is open, so it must be detected before anything declares free overworld.
    bag_pane = im.crop((108, 25, min(239, im.width), min(155, im.height)))
    bag_total = bag_pane.width * bag_pane.height
    bag_pale = sum(1 for r, g, b in bag_pane.getdata() if r > 245 and g > 245 and 185 <= b <= 220)
    bag_pale_ratio = bag_pale / bag_total if bag_total else 0.0
    bag_menu = (not battle_hud) and bag_pale_ratio > 0.55

    # Start-menu transitions briefly dim the right-hand pane. Recognize both the
    # bright and dimmed variants so navigation never moves while a menu is active.
    start_pane = im.crop((168, 15, min(238, im.width), min(156, im.height)))
    start_total = start_pane.width * start_pane.height
    start_gray_bright = sum(
        1 for r, g, b in start_pane.getdata()
        if max(r, g, b) - min(r, g, b) < 25 and r > 160
    )
    start_gray_dim = sum(
        1 for r, g, b in start_pane.getdata()
        if max(r, g, b) - min(r, g, b) < 25 and 60 <= r <= 140
    )
    start_gray_ratio = start_gray_bright / start_total if start_total else 0.0
    start_dim_ratio = start_gray_dim / start_total if start_total else 0.0
    start_menu = (
        (not battle_hud)
        and (not bag_menu)
        and (start_gray_ratio > 0.55 or start_dim_ratio > 0.55)
    )

    all_pixels = list(im.getdata())
    party_olive_ratio = sum(1 for r,g,b in all_pixels if 170 <= r <= 220 and 170 <= g <= 220 and 70 <= b <= 140) / max(1, len(all_pixels))
    party_menu = (not bag_menu) and party_olive_ratio > 0.30
    shop_peach_ratio = sum(1 for r,g,b in all_pixels if r > 245 and 195 <= g <= 240 and 145 <= b <= 180) / max(1, len(all_pixels))
    shop_menu = (not bag_menu) and (not party_menu) and shop_peach_ratio > 0.25
    full_screen_menu = bag_menu or start_menu or party_menu or shop_menu

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
        bag_menu=bag_menu,
        start_menu=start_menu,
        party_menu=party_menu,
        shop_menu=shop_menu,
        full_screen_menu=full_screen_menu,
        prompt_red_pixels=prompt_red,
        dialogue_ready=textbox and prompt_red >= 15,
    )


def image_difference(a: str | Path, b: str | Path, *, ignore_right: int = 18, textbox_only: bool | None = None) -> float:
    """Mean per-channel absolute difference with dialogue-aware cropping."""
    ia = Image.open(a).convert("RGB")
    ib = Image.open(b).convert("RGB")
    if ia.size != ib.size:
        return float("inf")
    w, h = ia.size
    if textbox_only is None:
        textbox_only = inspect_png(a).bottom_textbox and inspect_png(b).bottom_textbox
    if textbox_only:
        right = max(1, w - max(ignore_right, 22))
        ia = ia.crop((0, max(0, h - 50), right, h))
        ib = ib.crop((0, max(0, h - 50), right, h))
    elif ignore_right:
        ia = ia.crop((0, 0, max(1, w - ignore_right), h))
        ib = ib.crop((0, 0, max(1, w - ignore_right), h))
    diff = ImageChops.difference(ia, ib)
    stat = ImageStat.Stat(diff)
    return sum(stat.mean) / len(stat.mean)
