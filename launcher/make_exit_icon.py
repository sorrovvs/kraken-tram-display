"""Builds launcher/icon-exit.ico: the launcher icon with a "close" badge (white cross on a dark circle).

Usage:  .venv\\Scripts\\python.exe launcher\\make_exit_icon.py
"""

import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "icon.ico")
DST = os.path.join(HERE, "icon-exit.ico")
SIZES = [16, 24, 32, 48, 64, 128, 256]
SS = 4  # supersampling for smooth edges

BADGE = (32, 32, 36, 255)
RING = (255, 255, 255, 255)
CROSS = (255, 255, 255, 255)


def base_icon():
    ico = Image.open(SRC)
    ico.size = max(ico.info["sizes"])
    ico.load()
    return ico.convert("RGBA")


def badge(size):
    """Badge drawn at `size` px (supersampled), transparent outside the circle."""
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, s - 1, s - 1), fill=RING)
    ring = max(1, round(s * 0.07))
    d.ellipse((ring, ring, s - 1 - ring, s - 1 - ring), fill=BADGE)
    m = s * 0.31  # cross inset
    w = max(SS, round(s * 0.13))
    d.line((m, m, s - m, s - m), fill=CROSS, width=w)
    d.line((m, s - m, s - m, m), fill=CROSS, width=w)
    return img.resize((size, size), Image.LANCZOS)


def frame(base, size):
    img = base.resize((size, size), Image.LANCZOS)
    b = round(size * 0.58)  # big enough to read at 16 px
    img.alpha_composite(badge(b), (size - b, size - b))
    return img


def main():
    base = base_icon()
    frames = [frame(base, s) for s in SIZES]
    frames[-1].save(DST, format="ICO", sizes=[(s, s) for s in SIZES],
                    append_images=frames[:-1])
    print("Wrote", DST)


if __name__ == "__main__":
    main()
