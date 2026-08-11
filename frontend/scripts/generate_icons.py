"""One-off generator for PWA app icons. Not part of the build — run manually if the
brand mark ever needs regenerating: `python scripts/generate_icons.py`."""
from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "public")

# Brand gradient: matches the indigo -> purple gradient already used for the
# primary "Connect" CTA across the app (PlatformConnectionCard.tsx).
GRADIENT_START = (79, 70, 229)   # indigo-600
GRADIENT_END = (147, 51, 234)    # purple-600


def _lerp(a: int, b: int, t: float) -> int:
    return round(a + (b - a) * t)


def _rounded_mask(size: int, radius: float) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([(0, 0), (size - 1, size - 1)], radius=radius, fill=255)
    return mask


def _spark_polygon(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    """A simple 4-point spark / forge-bolt glyph, centered at (cx, cy)."""
    pts = []
    for i in range(4):
        ang = math.pi / 2 * i
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
        mid_ang = ang + math.pi / 4
        pts.append((cx + r * 0.38 * math.cos(mid_ang), cy + r * 0.38 * math.sin(mid_ang)))
    return pts


def make_icon(size: int, *, maskable: bool = False) -> Image.Image:
    img = Image.new("RGB", (size, size), GRADIENT_START)
    px = img.load()
    for y in range(size):
        t = y / (size - 1)
        row_color = tuple(_lerp(GRADIENT_START[i], GRADIENT_END[i], t) for i in range(3))
        for x in range(size):
            px[x, y] = row_color

    draw = ImageDraw.Draw(img)
    cx = cy = size / 2
    # Maskable icons need extra safe-zone padding (~20%) so Android's mask doesn't clip the glyph.
    r = size * (0.30 if maskable else 0.34)
    draw.polygon(_spark_polygon(cx, cy, r), fill=(255, 255, 255))

    radius = 0 if maskable else size * 0.22
    if radius:
        mask = _rounded_mask(size, radius)
        rounded = Image.new("RGBA", (size, size))
        rounded.paste(img, (0, 0), mask)
        return rounded
    return img.convert("RGBA")


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    make_icon(192).save(os.path.join(OUT_DIR, "icon-192.png"))
    make_icon(512).save(os.path.join(OUT_DIR, "icon-512.png"))
    make_icon(512, maskable=True).save(os.path.join(OUT_DIR, "icon-512-maskable.png"))
    make_icon(180).convert("RGB").save(os.path.join(OUT_DIR, "apple-touch-icon.png"))
    make_icon(32).save(os.path.join(OUT_DIR, "favicon-32.png"))
    make_icon(32).convert("RGB").save(os.path.join(OUT_DIR, "favicon.ico"), format="ICO", sizes=[(32, 32), (16, 16)])
    print("Icons written to", os.path.abspath(OUT_DIR))


if __name__ == "__main__":
    main()
