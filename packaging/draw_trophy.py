"""Draws packaging/logo.png, the JobQuest.AI logo: a gold quest trophy with silver handles, base and compass star.

Everything is drawn four times too large and scaled down, which gives clean edges. Run: python draw_trophy.py
"""
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

K = 4
S = 1024 * K
INK = (14, 16, 22)
GOLD = [(0, (122, 74, 8)), (.16, (214, 150, 28)), (.34, (255, 228, 130)), (.46, (255, 246, 196)), (.6, (246, 184, 44)), (.82, (168, 104, 12)), (1, (236, 170, 40))]
SILVER = [(0, (92, 100, 114)), (.18, (178, 186, 198)), (.36, (246, 248, 252)), (.5, (214, 220, 230)), (.72, (122, 130, 146)), (1, (196, 204, 216))]


def pts(p):
    return [(x * K, y * K) for x, y in p]


def mirror(p):
    return [(1024 - x, y) for x, y in p]


def mix(stops, t):
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if t <= b:
            f = (t - a) / (b - a)
            return tuple(round(x + (y - x) * f) for x, y in zip(ca, cb))
    return stops[-1][1]


def metal(stops, x0, x1):
    """A sideways metallic sheen between two x positions."""
    img = Image.new("RGBA", (S, S))
    d = ImageDraw.Draw(img)
    a, b = x0 * K, x1 * K
    for x in range(S):
        d.line([(x, 0), (x, S)], fill=mix(stops, min(1, max(0, (x - a) / (b - a)))) + (255,))
    return img


def mask():
    m = Image.new("L", (S, S), 0)
    return m, ImageDraw.Draw(m)


def lay(img, fill, m, shadow=True):
    """Place a shape with a thin dark edge, so it stays crisp at icon size."""
    if shadow:
        edge = m.filter(ImageFilter.MaxFilter(13)).filter(ImageFilter.GaussianBlur(2))
        img.paste(Image.new("RGBA", (S, S), (8, 9, 13, 255)), mask=edge)
    img.paste(fill, mask=m)


def star(cx, cy, r, inner, n=4, turn=0.0):
    out = []
    for i in range(n * 2):
        a = turn + math.pi * i / n - math.pi / 2
        q = r if i % 2 == 0 else inner
        out.append((cx + q * math.cos(a), cy + q * math.sin(a)))
    return out


def draw():
    img = Image.new("RGBA", (S, S), INK + (255,))

    # warm light behind the cup, with faint rays
    rays = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(rays)
    for i in range(16):
        a = i * math.pi / 8
        p = [(512, 400), (512 + 900 * math.cos(a - .07), 400 + 900 * math.sin(a - .07)), (512 + 900 * math.cos(a + .07), 400 + 900 * math.sin(a + .07))]
        d.polygon(pts(p), fill=30)
    img.paste(Image.new("RGBA", (S, S), (245, 158, 11, 255)), mask=rays.filter(ImageFilter.GaussianBlur(10 * K)))
    halo = Image.new("L", (S, S), 0)
    ImageDraw.Draw(halo).ellipse([170 * K, 60 * K, 854 * K, 744 * K], fill=165)
    img.paste(Image.new("RGBA", (S, S), (245, 158, 11, 255)), mask=halo.filter(ImageFilter.GaussianBlur(78 * K)))

    silver = metal(SILVER, 280, 744)
    gold = metal(GOLD, 288, 736)

    # silver handles sit behind the cup
    m, d = mask()
    for box in ((150, 198, 396, 470), (628, 198, 874, 470)):
        d.ellipse([v * K for v in box], outline=255, width=46 * K)
    d.rectangle([330 * K, 150 * K, 694 * K, 520 * K], fill=0)
    lay(img, metal(SILVER, 140, 884), m)

    # base and stem
    m, d = mask()
    d.rounded_rectangle([318 * K, 806 * K, 706 * K, 892 * K], radius=20 * K, fill=255)
    d.polygon(pts([(388, 720), (636, 720), (672, 812), (352, 812)]), fill=255)
    lay(img, silver, m)
    m, d = mask()
    d.rounded_rectangle([392 * K, 830 * K, 632 * K, 870 * K], radius=8 * K, fill=255)
    lay(img, gold, m, shadow=False)
    m, d = mask()
    d.polygon(pts([(472, 596), (552, 596), (540, 660), (594, 730), (430, 730), (484, 660)]), fill=255)
    lay(img, gold, m)
    m, d = mask()
    d.ellipse([440 * K, 636 * K, 584 * K, 684 * K], fill=255)
    lay(img, silver, m)

    # the cup
    side = []
    for i in range(61):
        t = i / 60
        side.append((512 - (42 + 188 * (1 - t ** 2.3)), 190 + 420 * t))
    m, d = mask()
    d.polygon(pts(side + mirror(side)[::-1]), fill=255)
    d.ellipse([282 * K, 152 * K, 742 * K, 228 * K], fill=255)
    lay(img, gold, m)

    # rim and the dark mouth of the cup
    m, d = mask()
    d.ellipse([268 * K, 142 * K, 756 * K, 238 * K], fill=255)
    lay(img, metal(GOLD, 268, 756), m)
    m, d = mask()
    d.ellipse([304 * K, 158 * K, 720 * K, 222 * K], fill=255)
    img.paste(Image.new("RGBA", (S, S), (78, 44, 6, 255)), mask=m)

    # soft shine on the left of the bowl
    shine = Image.new("L", (S, S), 0)
    ImageDraw.Draw(shine).polygon(pts([(346, 250), (388, 250), (428, 540), (402, 540)]), fill=150)
    img.paste(Image.new("RGBA", (S, S), (255, 255, 255, 255)), mask=shine.filter(ImageFilter.GaussianBlur(9 * K)))

    # the quest: a silver compass star on the bowl
    m, d = mask()
    d.polygon(pts(star(512, 392, 136, 34)), fill=255)
    d.polygon(pts(star(512, 392, 76, 22, turn=math.pi / 4)), fill=255)
    lay(img, metal(SILVER, 400, 624), m)
    m, d = mask()
    d.ellipse([486 * K, 366 * K, 538 * K, 418 * K], fill=255)
    lay(img, Image.new("RGBA", (S, S), (245, 158, 11, 255)), m, shadow=False)

    # sparkles
    d = ImageDraw.Draw(img)
    for x, y, r in ((812, 142, 46), (214, 610, 30), (846, 596, 22)):
        d.polygon(pts(star(x, y, r, r * .16)), fill=(255, 244, 200, 255))

    tile = Image.new("L", (S, S), 0)
    ImageDraw.Draw(tile).rounded_rectangle([24 * K, 24 * K, 1000 * K, 1000 * K], radius=210 * K, fill=255)
    img.putalpha(ImageChops.darker(img.getchannel("A"), tile))
    return img.resize((1024, 1024), Image.LANCZOS)


if __name__ == "__main__":
    out = Path(__file__).with_name("logo.png")
    draw().save(out)
    print("wrote", out)
