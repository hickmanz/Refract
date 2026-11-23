"""Draws the Refract app icon (a bent ray entering a surface on a graphite tile) into packaging/icon.ico.

    .venv\\Scripts\\python.exe scripts\\make_icon.py

Same geometry as frontend/public/favicon.svg and frontend/src/components/RefractMark.vue (a 64 x 64 grid).
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SIZES = [16, 24, 32, 48, 64, 128, 256]
S = 1024  # supersampled canvas
K = S / 64

TILE = (24, 25, 28, 255)
EDGE = (255, 255, 255, 24)
SURFACE = (200, 202, 208, 255)
RAY = (236, 72, 153, 255)  # the default accent (pink)


def _line(d: ImageDraw.ImageDraw, pts, width, fill) -> None:
    """A polyline with round caps and joins."""
    p = [(x * K, y * K) for x, y in pts]
    w = width * K
    d.line(p, fill=fill, width=round(w), joint="curve")
    r = w / 2
    for x, y in p:
        d.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def render() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, S - 1, S - 1), radius=15 * K, fill=TILE)
    d.rounded_rectangle((K * 0.25, K * 0.25, S - 1 - K * 0.25, S - 1 - K * 0.25), radius=14.75 * K, outline=EDGE, width=round(K * 0.5))
    _line(d, [(12, 34), (52, 34)], 3.2, SURFACE)
    _line(d, [(17.5, 12), (36, 34), (46.6, 56)], 5.0, RAY)
    return img


if __name__ == "__main__":
    big = render()
    out = ROOT / "packaging" / "icon.ico"
    big.save(out, format="ICO", sizes=[(s, s) for s in SIZES])
    big.resize((256, 256), Image.LANCZOS).save(ROOT / "scratch" / "icon-preview.png")
    print("wrote", out, [s for s in SIZES])
