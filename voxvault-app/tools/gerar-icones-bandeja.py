"""Derive the four tray icons from the application icon.

Each state differs by **shape**, not only by colour, so the icon answers "is
VoxVault recording my microphone?" for someone who does not tell red from
amber, and in a grayscale screenshot:

========  =================================================================
ocioso    the application icon as it is
gravando  a filled red circle, white-ringed, in the lower right corner
pausado   two amber bars, dark-ringed, in the lower right corner
falha     the icon in grays, with an exclamation triangle in the corner
========  =================================================================

Drawn at 4x and reduced, so the badges keep smooth edges at 32 px. The PNGs
are committed; building the app never needs Pillow.

    uv run --no-project --with pillow python voxvault-app/tools/gerar-icones-bandeja.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

FINAL = 32
SCALE = 4
WORK = FINAL * SCALE

RED = (0xE5, 0x48, 0x4D, 255)
AMBER = (0xF5, 0xA5, 0x24, 255)
WHITE = (255, 255, 255, 255)
DARK = (0x1A, 0x1C, 0x21, 255)

ICONS = Path(__file__).resolve().parents[1] / "src-tauri" / "icons"
OUT = ICONS / "bandeja"


def _base() -> Image.Image:
    return Image.open(ICONS / "icon.png").convert("RGBA").resize((WORK, WORK), Image.LANCZOS)


def _badge_box() -> tuple[int, int, int, int]:
    """The lower right corner, where every state marks itself."""
    size = int(WORK * 0.56)
    return (WORK - size, WORK - size, WORK, WORK)


def recording() -> Image.Image:
    icon = _base()
    pen = ImageDraw.Draw(icon)
    x0, y0, x1, y1 = _badge_box()
    pen.ellipse((x0, y0, x1 - 1, y1 - 1), fill=WHITE)
    ring = int(WORK * 0.06)
    pen.ellipse((x0 + ring, y0 + ring, x1 - 1 - ring, y1 - 1 - ring), fill=RED)
    return icon


def paused() -> Image.Image:
    icon = _base()
    pen = ImageDraw.Draw(icon)
    x0, y0, x1, y1 = _badge_box()
    pen.rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=int(WORK * 0.12), fill=DARK)
    inner = int(WORK * 0.12)
    bar = int((x1 - x0 - 2 * inner) * 0.36)
    top, bottom = y0 + inner, y1 - inner
    left = x0 + inner
    right = x1 - inner - bar
    pen.rounded_rectangle((left, top, left + bar, bottom), radius=int(WORK * 0.03), fill=AMBER)
    pen.rounded_rectangle((right, top, right + bar, bottom), radius=int(WORK * 0.03), fill=AMBER)
    return icon


def failure() -> Image.Image:
    gray = ImageOps.grayscale(_base()).convert("RGBA")
    gray.putalpha(_base().getchannel("A"))
    pen = ImageDraw.Draw(gray)
    x0, y0, x1, y1 = _badge_box()
    pen.polygon([((x0 + x1) // 2, y0), (x1 - 1, y1 - 1), (x0, y1 - 1)], fill=DARK)
    pad = int(WORK * 0.07)
    pen.polygon(
        [((x0 + x1) // 2, y0 + int(pad * 1.6)), (x1 - 1 - pad, y1 - 1 - pad // 2),
         (x0 + pad, y1 - 1 - pad // 2)],
        fill=AMBER,
    )
    cx = (x0 + x1) // 2
    stroke = max(4, int(WORK * 0.05))
    pen.rounded_rectangle(
        (cx - stroke // 2, y0 + int((y1 - y0) * 0.36), cx + stroke // 2, y0 + int((y1 - y0) * 0.70)),
        radius=stroke // 2, fill=DARK,
    )
    dot = int(stroke * 0.65)
    dot_y = y0 + int((y1 - y0) * 0.80)
    pen.ellipse((cx - dot, dot_y - dot, cx + dot, dot_y + dot), fill=DARK)
    return gray


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, image in (
        ("ocioso", _base()),
        ("gravando", recording()),
        ("pausado", paused()),
        ("falha", failure()),
    ):
        target = OUT / f"{name}.png"
        image.resize((FINAL, FINAL), Image.LANCZOS).save(target, optimize=True)
        print(target)


if __name__ == "__main__":
    main()
