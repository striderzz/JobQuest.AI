"""Draws packaging/jobquestai.ico (and .png): a white skyscraper on a black rounded square.

One shape list on a 32-unit grid drives both the raster icon and `svg()` (the in-app logo and favicon), so they
always match. Run directly to write the icon; `python make_icon.py --svg` prints the SVG.
"""
import sys
from pathlib import Path

BLACK, WHITE, GREY = (17, 17, 19), (255, 255, 255), (150, 150, 158)

# (kind, colour, coords) - rects are x0, y0, x1, y1; polys are point lists
SHAPES = [
    ("rect", GREY, (21.5, 14, 26.5, 28)),            # shorter tower behind, to the right
    ("rect", GREY, (6, 18, 10.5, 28)),               # low block on the left
    ("rect", WHITE, (11.5, 8, 20.5, 28)),            # main tower
    ("rect", WHITE, (13, 5.5, 19, 8)),               # first setback
    ("rect", WHITE, (14.5, 3.5, 17.5, 5.5)),         # crown
    ("rect", WHITE, (15.6, 0.8, 16.4, 3.5)),         # spire
    ("rect", WHITE, (4.5, 28, 27.5, 29.2)),          # street line
]
WINDOWS = [("rect", BLACK, (x, y, x + 1.4, y + 1.6)) for y in (10, 13, 16, 19, 22, 25) for x in (13, 15.3, 17.6)]
SIDE_WINDOWS = [("rect", BLACK, (x, y, x + 1.1, y + 1.3)) for y in (16, 19, 22, 25) for x in (22.6, 24.6)] + \
               [("rect", BLACK, (x, y, x + 1.1, y + 1.3)) for y in (20, 23) for x in (7.2,)]


def _hex(c):
    return "#%02x%02x%02x" % c


def svg(size=24, background=False):
    parts = [f'<rect width="32" height="32" rx="7" fill="{_hex(BLACK)}"/>'] if background else []
    for kind, col, c in SHAPES + WINDOWS + SIDE_WINDOWS:
        x0, y0, x1, y1 = c
        parts.append(f'<rect x="{x0}" y="{y0}" width="{round(x1 - x0, 2)}" height="{round(y1 - y0, 2)}" fill="{_hex(col)}"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="{size}" height="{size}" aria-hidden="true">'
            + "".join(parts) + "</svg>")


def raster(S=1024):
    from PIL import Image, ImageDraw
    k, o = (S - 2 * 96) / 32, 96
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([24, 24, S - 24, S - 24], radius=210, fill=BLACK + (255,))
    for kind, col, (x0, y0, x1, y1) in SHAPES + WINDOWS + SIDE_WINDOWS:
        d.rectangle([o + x0 * k, o + y0 * k, o + x1 * k, o + y1 * k], fill=col + (255,))
    return img


if __name__ == "__main__":
    if "--svg" in sys.argv:
        print(svg(background="--tile" in sys.argv))
        sys.exit()
    from PIL import Image
    img = raster()
    out = Path(__file__).with_name("jobquestai.ico")
    img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.resize((256, 256), Image.LANCZOS).save(out.with_suffix(".png"))
    print("wrote", out)
