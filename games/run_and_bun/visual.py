from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import struct
import zlib

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


def _read_png_rgb(path: str | Path) -> tuple[int, int, list[list[tuple[int, int, int]]]]:
    """Read mGBA's 8-bit RGB/RGBA PNG without a third-party dependency."""
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    width = height = bit_depth = color_type = None
    compressed: list[bytes] = []
    offset = 8
    while offset + 8 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _, _, _ = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            compressed.append(payload)
        elif kind == b"IEND":
            break
    if width is None or height is None or bit_depth != 8 or color_type not in (2, 6):
        raise ValueError("unsupported PNG format")
    channels = 3 if color_type == 2 else 4
    stride = width * channels
    raw = zlib.decompress(b"".join(compressed))
    rows: list[list[tuple[int, int, int]]] = []
    cursor = 0
    previous = bytearray(stride)
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        row = bytearray(raw[cursor : cursor + stride])
        cursor += stride
        for index in range(stride):
            left = row[index - channels] if index >= channels else 0
            up = previous[index]
            up_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                row[index] = (row[index] + left) & 0xFF
            elif filter_type == 2:
                row[index] = (row[index] + up) & 0xFF
            elif filter_type == 3:
                row[index] = (row[index] + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                estimate = left + up - up_left
                distances = (abs(estimate - left), abs(estimate - up), abs(estimate - up_left))
                predictor = (left, up, up_left)[distances.index(min(distances))]
                row[index] = (row[index] + predictor) & 0xFF
            elif filter_type != 0:
                raise ValueError(f"unsupported PNG filter {filter_type}")
        rows.append([tuple(row[i : i + channels][:3]) for i in range(0, stride, channels)])
        previous = row
    return width, height, rows


def _inspect_png_without_pillow(path: str | Path) -> VisualState:
    width, height, rows = _read_png_rgb(path)

    def pixel(x: int, y: int) -> tuple[int, int, int]:
        return rows[y][x]

    def crop_counts(x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int]:
        white = teal = total = 0
        for y in range(max(0, y0), min(height, y1)):
            for x in range(max(0, x0), min(width, x1)):
                r, g, b = pixel(x, y)
                white += r > 220 and g > 220 and b > 220
                teal += 80 <= r <= 140 and 130 <= g <= 190 and 130 <= b <= 190
                total += 1
        return white, teal, total

    white, _, total = crop_counts(0, height - 50, width, height)
    prompt_red = sum(
        pixel(x, y)[0] > 150 and pixel(x, y)[1] < 100 and pixel(x, y)[2] < 100
        for y in range(max(0, height - 46), max(0, height - 1))
        for x in range(3, max(3, width - 3))
    )
    _, battle_teal, battle_total = crop_counts(2, height - 44, width - 2, height - 1)

    def white_ratio(box: tuple[int, int, int, int]) -> float:
        w, _, n = crop_counts(*box)
        return w / n if n else 0.0

    enemy = white_ratio((10, 15, min(110, width), min(45, height))) > 0.25
    player = white_ratio((130, 70, min(232, width), min(115, height))) > 0.25
    teal_ratio = battle_teal / battle_total if battle_total else 0.0
    white_ratio_bottom = white / total if total else 0.0
    battle_hud = (enemy and player) or (teal_ratio > 0.25 and (enemy or player))
    battle_textbox = battle_hud and teal_ratio > 0.25
    battle_menu_like = battle_hud and white_ratio_bottom > 0.50 and teal_ratio < 0.10
    _, left_teal, left_total = crop_counts(2, height - 44, min(122, width), height - 1)
    right_white, right_teal, right_total = crop_counts(min(122, width), height - 44, max(123, width - 2), height - 1)
    command = (
        battle_hud
        and left_teal / left_total > 0.55
        and right_white / right_total > 0.55
        and right_teal / right_total < 0.05
    ) if left_total and right_total else False
    return VisualState(
        path=str(path),
        sha1=hashlib.sha1(Path(path).read_bytes()).hexdigest(),
        bottom_white_ratio=white_ratio_bottom,
        bottom_textbox=white_ratio_bottom > 0.45,
        battle_teal_ratio=teal_ratio,
        battle_hud=battle_hud,
        battle_textbox=battle_textbox,
        battle_menu_like=battle_menu_like,
        battle_command_menu=command,
        prompt_red_pixels=prompt_red,
        dialogue_ready=white_ratio_bottom > 0.45 and prompt_red >= 15,
    )


def inspect_png(path: str | Path) -> VisualState:
    if Image is None:
        return _inspect_png_without_pillow(path)
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
