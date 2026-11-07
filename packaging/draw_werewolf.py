"""Draws packaging/wolf.png, the JobQuest.AI logo: a snarling werewolf head in front of a full moon.

Everything is drawn four times too large and scaled down, which gives clean edges. Run: python draw_werewolf.py
"""
import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

K = 4
S = 1024 * K
MOON, GLOW, AMBER = (246, 196, 84), (245, 158, 11), (255, 176, 32)
INK, FUR_DARK, FUR, FUR_MID, FUR_LIGHT = (13, 15, 20), (32, 36, 45), (58, 64, 77), (94, 102, 118), (150, 157, 170)


def pts(p):
    return [(x * K, y * K) for x, y in p]


def mirror(p):
    return [(1024 - x, y) for x, y in p]


def both(d, p, colour):
    d.polygon(pts(p), fill=colour)
    d.polygon(pts(mirror(p)), fill=colour)


def tile():
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).rounded_rectangle([24 * K, 24 * K, 1000 * K, 1000 * K], radius=210 * K, fill=255)
    return m


def fur_edge(p, rng):
    """Turn the ruff of the head into ragged tufts."""
    out = []
    for i, (a, b) in enumerate(zip(p, p[1:])):
        out.append(a)
        if 3 <= i <= len(p) - 4:
            for t in (.33, .66):
                x, y = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
                out.append((x - rng.uniform(16, 42), y + rng.uniform(10, 34)))
                out.append((x + 8, y - 2))
    out.append(p[-1])
    return out


def draw():
    rng = random.Random(7)
    img = Image.new("RGBA", (S, S), INK + (255,))
    d = ImageDraw.Draw(img)

    # night sky, then the moon with a soft halo
    halo = Image.new("L", (S, S), 0)
    ImageDraw.Draw(halo).ellipse([150 * K, 40 * K, 874 * K, 764 * K], fill=150)
    halo = halo.filter(ImageFilter.GaussianBlur(70 * K))
    img.paste(Image.new("RGBA", (S, S), GLOW + (255,)), mask=halo)
    d = ImageDraw.Draw(img)
    d.ellipse([222 * K, 96 * K, 802 * K, 676 * K], fill=MOON + (255,))
    for x, y, r, c in ((340, 210, 44, (232, 176, 66)), (640, 170, 30, (232, 176, 66)), (700, 330, 52, (236, 182, 72)), (300, 400, 28, (236, 182, 72))):
        d.ellipse([(x - r) * K, (y - r) * K, (x + r) * K, (y + r) * K], fill=c + (255,))

    # head silhouette: tall ears, heavy ragged cheeks, long jaw
    core = [(512, 330), (432, 318), (392, 262), (292, 40), (246, 300), (214, 372), (170, 450), (118, 560), (112, 660), (168, 742), (250, 790),
            (332, 838), (402, 900), (456, 952), (512, 968)]
    half = fur_edge(core, rng)
    shape = half + mirror(half[1:-1])[::-1]
    d.polygon(pts(shape), fill=INK + (255,))
    grown = Image.new("L", (S, S), 0)
    ImageDraw.Draw(grown).polygon(pts(shape), fill=255)
    outline = grown.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(3))
    img.paste(Image.new("RGBA", (S, S), INK + (255,)), mask=outline)
    head = Image.new("RGBA", (S, S), FUR + (255,))
    h = ImageDraw.Draw(head)

    # ears
    both(h, [(300, 92), (378, 268), (330, 362), (262, 322)], FUR_DARK)
    both(h, [(304, 170), (356, 276), (328, 340), (284, 306)], (92, 44, 40))
    # dark mane down the outer face, lighter brow and cheeks, muzzle planes
    both(h, [(214, 372), (118, 560), (112, 660), (262, 700), (330, 560), (316, 430)], FUR_DARK)
    both(h, [(168, 742), (250, 790), (332, 838), (402, 900), (372, 742), (262, 690), (150, 640)], (44, 49, 60))
    both(h, [(512, 330), (432, 318), (330, 400), (352, 470), (452, 498), (512, 560)], FUR_MID)
    both(h, [(330, 400), (316, 430), (330, 560), (420, 640), (452, 560), (352, 470)], FUR)
    both(h, [(512, 500), (462, 520), (420, 640), (432, 700), (512, 712)], FUR_LIGHT)
    both(h, [(512, 330), (476, 350), (462, 470), (512, 520)], (46, 51, 62))
    both(h, [(512, 560), (478, 566), (470, 660), (512, 690)], (120, 128, 142))

    # fur strokes radiate from the nose
    for _ in range(2600):
        x, y = rng.uniform(110, 914), rng.uniform(60, 960)
        dx, dy = x - 512, y - 620
        n = math.hypot(dx, dy) or 1
        a = rng.uniform(-.28, .28)
        ux, uy = dx / n, dy / n + .3
        vx, vy = ux * math.cos(a) - uy * math.sin(a), ux * math.sin(a) + uy * math.cos(a)
        ln = rng.uniform(22, 54)
        tone = rng.choice((INK, FUR_DARK, FUR_MID, FUR_LIGHT))
        h.line(pts([(x, y), (x + vx * ln, y + vy * ln)]), fill=tone + (rng.randint(50, 110),), width=int(rng.uniform(1.5, 3.2) * K))

    # snarl: open mouth, gums, fangs
    mouth = [(512, 742), (448, 734), (372, 772), (392, 836), (452, 880), (512, 892)]
    h.polygon(pts(mouth + mirror(mouth[1:-1])[::-1]), fill=(26, 8, 10))
    inner = [(512, 772), (452, 772), (420, 796), (462, 852), (512, 862)]
    h.polygon(pts(inner + mirror(inner[1:-1])[::-1]), fill=(120, 24, 32))
    gum = [(512, 736), (444, 728), (372, 768), (384, 790), (450, 762), (512, 768)]
    h.polygon(pts(gum + mirror(gum[1:-1])[::-1]), fill=(150, 44, 56))
    white = (244, 240, 230)
    both(h, [(392, 776), (428, 762), (420, 866)], white)                 # long upper fangs
    both(h, [(436, 760), (462, 756), (454, 806)], white)
    both(h, [(470, 756), (492, 756), (484, 790)], white)
    both(h, [(404, 846), (438, 866), (432, 790)], (226, 220, 208))       # lower fangs
    both(h, [(456, 874), (484, 880), (474, 836)], (226, 220, 208))
    # nose
    nose = [(512, 640), (446, 646), (428, 684), (466, 724), (512, 736)]
    h.polygon(pts(nose + mirror(nose[1:-1])[::-1]), fill=(11, 12, 15))
    h.polygon(pts([(470, 654), (554, 654), (540, 672), (484, 672)]), fill=(84, 90, 102))
    both(h, [(452, 690), (492, 696), (484, 716), (462, 708)], (0, 0, 0))

    # angry brow and burning eyes
    both(h, [(300, 388), (468, 476), (462, 504), (316, 436)], (10, 11, 14))
    eye = [(338, 440), (462, 506), (438, 548), (366, 516)]
    both(h, [(x + (x - 400) * .16, y + (y - 500) * .3) for x, y in eye], (8, 9, 12))
    head.putalpha(grown)
    img.alpha_composite(head)

    glow = Image.new("L", (S, S), 0)
    g = ImageDraw.Draw(glow)
    g.polygon(pts(eye), fill=255)
    g.polygon(pts(mirror(eye)), fill=255)
    glow = glow.filter(ImageFilter.GaussianBlur(26 * K))
    img.paste(Image.new("RGBA", (S, S), AMBER + (255,)), mask=glow.point(lambda v: min(255, int(v * 1.5))))
    d = ImageDraw.Draw(img)
    both(d, eye, AMBER + (255,))
    both(d, [(372, 468), (446, 508), (430, 530), (384, 508)], (255, 226, 128, 255))
    both(d, [(404, 486), (418, 494), (412, 526), (398, 516)], (10, 8, 6, 255))     # slit pupils

    img.putalpha(ImageChops.multiply(img.getchannel("A"), tile()))
    edge = tile().filter(ImageFilter.GaussianBlur(1 * K))
    img.putalpha(ImageChops.darker(img.getchannel("A"), edge))
    return img.resize((1024, 1024), Image.LANCZOS)


if __name__ == "__main__":
    out = Path(__file__).with_name("wolf.png")
    draw().save(out)
    print("wrote", out)
