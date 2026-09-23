"""Draw the application icon: a white microphone on the interface's blue.

A microphone because the question the icon answers, in the tray above all, is
"is something listening to me?". The blue is the interface's own accent, so
the window and the icon read as the same thing.

The master is drawn at 1024 px and every size the bundle needs is derived from
it by the Tauri CLI:

    uv run --with pillow voxvault-app/tools/gerar-icone.py
    cd voxvault-app && npx tauri icon src-tauri/icons/icone-mestre.png

The tray icons are derived from the result by ``gerar-icones-bandeja.py``.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024
TOP = (0x3A, 0x6D, 0xE0)
BOTTOM = (0x1F, 0x45, 0xA8)
WHITE = (255, 255, 255, 255)

OUT = Path(__file__).resolve().parents[1] / "src-tauri" / "icons" / "icone-mestre.png"


def _gradient() -> Image.Image:
    column = Image.new("RGBA", (1, SIZE))
    for y in range(SIZE):
        t = y / (SIZE - 1)
        column.putpixel(
            (0, y),
            tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM, strict=True)) + (255,),
        )
    return column.resize((SIZE, SIZE))


def draw() -> Image.Image:
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (40, 40, SIZE - 40, SIZE - 40), radius=230, fill=255
    )
    icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    icon.paste(_gradient(), (0, 0), mask)

    pen = ImageDraw.Draw(icon)
    cx = SIZE // 2
    # The capsule.
    pen.rounded_rectangle((cx - 120, 190, cx + 120, 590), radius=120, fill=WHITE)
    # The cradle around it: an open arc, thick enough to survive 16 px.
    pen.arc((cx - 215, 330, cx + 215, 720), start=0, end=180, fill=WHITE, width=58)
    # The stem and the foot.
    pen.rounded_rectangle((cx - 29, 700, cx + 29, 820), radius=14, fill=WHITE)
    pen.rounded_rectangle((cx - 150, 800, cx + 150, 856), radius=28, fill=WHITE)
    return icon


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    draw().save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
