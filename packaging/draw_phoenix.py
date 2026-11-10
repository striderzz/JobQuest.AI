"""Draws the JobQuest.AI emblem: a red phoenix built from sharp, connected facets.

The bird is one triangle mesh: every facet shares its edges with its neighbours, so there are no gaps or
overlaps. Each vertex has a height, and each facet is shaded flat from a light at the top left, which gives the
cut-gem look. The left half is defined once and mirrored.

Writes, all from the same mesh:
  packaging/emblem.svg   the app icon: the phoenix on a dark tile (vector master)
  packaging/logo.svg     the full logo: the phoenix over the JOBQUEST title (vector)
  packaging/logo.png     the icon at 1024 px, which make_icon.py turns into the .ico and the in-app icon
  docs/logo.png          the full logo for the README

The title is set in Cinzel (SIL Open Font License, by Natanael Gama), converted to outlines so the SVGs need no font.
PNGs are rendered with headless Microsoft Edge, which ships with Windows.

    python packaging/draw_phoenix.py [path/to/Cinzel.ttf]
"""
import math
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
CINZEL = r"C:\Unity_Projects\RogueLike\Assets\Resources\Fonts\Cinzel.ttf"
TILE = "#141518"
CX = 256                    # drawn on a 512 grid, symmetric about x = 256

# vertex: (x, y, height). Names in CENTRE sit on the centre line; the rest of V is the viewer's left half,
# mirrored for the right. The head is drawn once, in profile, facing left (HEAD / HEAD_TRIS).
V = {
    "chest": (256, 188, 46), "neckc": (256, 162, 34), "belly": (256, 244, 50), "root": (256, 300, 30),
    "tail": (256, 482, 0),
    "neck": (238, 162, 26), "waist": (232, 270, 28), "hip": (238, 304, 18),
    # tail flames
    "tv1": (240, 396, 6), "t1": (206, 462, 0), "tv2": (214, 366, 6), "t2": (162, 426, 0),
}
CENTRE = {"chest", "neckc", "belly", "root", "tail"}
TRIS = [
    ("neck", "chest", "neckc"), ("neck", "a0", "chest"), ("neck", "a1", "a0"),
    ("a0", "belly", "chest"), ("a0", "waist", "belly"), ("waist", "root", "belly"), ("waist", "hip", "root"),
    ("root", "tv1", "tail"), ("hip", "tv1", "root"), ("hip", "t1", "tv1"), ("hip", "tv2", "t1"),
    ("waist", "tv2", "hip"), ("waist", "t2", "tv2"), ("a0", "v0", "waist"),
]


def _wing(n=6):
    """A raised wing: an arm sweeping up and out, with long blade feathers fanned beneath it.
    Feather i is fanned from arm point a_i: a_i, notch v_i, tip f_i, notch v_(i+1), arm point a_(i+1)."""
    p0, p1, p2 = (222, 200), (150, 150), (58, 58)
    def arm(t):
        return tuple((1 - t) ** 2 * a + 2 * (1 - t) * t * b + t * t * c for a, b, c in zip(p0, p1, p2))
    def ray(o, ang, ln):
        r = math.radians(ang)
        return o[0] + math.cos(r) * ln, o[1] + math.sin(r) * ln
    angles = [104 + 86 * (i / (n - 1)) ** 1.1 for i in range(n)]            # hanging at the body, swept out at the tip
    lengths = [104 + 58 * math.sin(math.pi * (i + 0.6) / (n + 0.4)) for i in range(n)]
    for i in range(n + 1):
        x, y = arm(i / n)
        V[f"a{i}"] = (x, y, 38 - 10 * i / n)
    for i in range(n):
        mid = arm((i + 0.5) / n)
        V[f"f{i}"] = (*ray(mid, angles[i], lengths[i]), 0)
    for i in range(n + 1):
        j = min(i, n - 1)
        k = max(i - 1, 0)
        ang = (angles[j] + angles[k]) / 2
        V[f"v{i}"] = (*ray(arm(i / n), ang, 0.4 * (lengths[j] + lengths[k]) / 2), 12)
    V["v0"] = (*ray(p0, angles[0], 50), 12)
    tip_dir = math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0]))
    V["wt"] = (*ray(p2, tip_dir - 8, 54), 0)                                 # the wingtip flame
    for i in range(n):
        TRIS.extend([(f"a{i}", f"v{i}", f"f{i}"), (f"a{i}", f"f{i}", f"v{i + 1}"), (f"a{i}", f"v{i + 1}", f"a{i + 1}")])
    TRIS.append((f"a{n}", f"v{n}", "wt"))


_wing()

# the head in profile, facing the viewer's left: hooked beak, high cheek, crest sweeping back
HEAD = {
    "crown": (244, 90, 26), "beaktop": (214, 108, 18), "beaktip": (182, 136, 0), "jaw": (222, 128, 18),
    "lowtip": (198, 142, 0), "throat": (230, 144, 18), "cheek": (250, 120, 40), "occiput": (276, 100, 24),
    "occ2": (284, 118, 20), "nape": (282, 140, 20),
    "p1": (296, 26, 0), "p2": (334, 56, 0), "p3": (340, 104, 0),
}
HEAD_TRIS = [
    ("crown", "beaktop", "cheek"), ("beaktop", "jaw", "cheek"), ("beaktop", "beaktip", "jaw"),
    ("jaw", "lowtip", "throat"), ("cheek", "jaw", "throat"), ("cheek", "throat", "neck/L"),
    ("cheek", "neck/L", "neckc"), ("cheek", "neckc", "neck/R"), ("cheek", "neck/R", "nape"),
    ("cheek", "nape", "occ2"), ("cheek", "occ2", "occiput"), ("cheek", "occiput", "crown"),
    ("crown", "p1", "occiput"), ("occiput", "p2", "occ2"), ("occ2", "p3", "nape"),
]
EYE = [(234, 112), (245, 109), (242, 118)]
HEAD_SCALE = 1.3

# the red ramp, darkest to brightest: facets turned away from the light are deep crimson, lit ones bright scarlet
RAMP = [(52, 6, 12), (110, 12, 22), (168, 22, 30), (214, 38, 38), (244, 74, 58), (255, 128, 104)]
LIGHT = (-0.55, -0.62, 0.56)             # from the top left, in front


def f(v):
    return f"{v:.1f}".rstrip("0").rstrip(".")


def point(name, side):
    if name in HEAD:
        x, y, z = HEAD[name]
        x, y = CX + (x - CX) * HEAD_SCALE, 162 + (y - 162) * HEAD_SCALE      # grown about the neck
    else:
        if "/" in name:
            name, side = name.split("/")
        x, y, z = V[name]
        if side == "R" and name not in CENTRE:
            x = 2 * CX - x
    return x, y, z * 1.6


def shade(a, b, c):
    """Flat shading: the facet's normal against the light, mapped onto the red ramp."""
    u = [b[i] - a[i] for i in range(3)]
    v = [c[i] - a[i] for i in range(3)]
    n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
    if n[2] < 0:
        n = [-k for k in n]
    length = math.sqrt(sum(k * k for k in n)) or 1
    lx, ly, lz = LIGHT
    ll = math.sqrt(lx * lx + ly * ly + lz * lz)
    lit = max(0.0, (n[0] * lx + n[1] * ly + n[2] * lz) / (length * ll))
    t = min(1.0, 0.12 + 0.95 * lit ** 1.4)
    pos = t * (len(RAMP) - 1)
    i = min(int(pos), len(RAMP) - 2)
    k = pos - i
    col = tuple(round(RAMP[i][j] + (RAMP[i + 1][j] - RAMP[i][j]) * k) for j in range(3))
    return "#%02x%02x%02x" % col


def facets():
    out = []
    for side in ("L", "R"):
        for tri in TRIS:
            pts = [point(n, side) for n in tri]
            out.append((pts, shade(*pts)))
    for tri in HEAD_TRIS:
        pts = [point(n, "L") for n in tri]
        out.append((pts, shade(*pts)))
    return out


def bbox():
    xs = [p[0] for pts, _ in facets() for p in pts]
    ys = [p[1] for pts, _ in facets() for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def fit(box):
    """A transform that centres the phoenix in box = (x0, y0, x1, y1), keeping its proportions."""
    x0, y0, x1, y1 = bbox()
    bx0, by0, bx1, by1 = box
    s = min((bx1 - bx0) / (x1 - x0), (by1 - by0) / (y1 - y0))
    return f"translate({f((bx0 + bx1) / 2 - s * (x0 + x1) / 2)} {f((by0 + by1) / 2 - s * (y0 + y1) / 2)}) scale({s:.4f})"


def phoenix():
    """The mesh as SVG. Each facet is stroked in its own colour so neighbours meet without hairline cracks."""
    parts = []
    for pts, col in facets():
        d = "M" + " L".join(f"{f(x)},{f(y)}" for x, y, _ in pts) + " Z"
        parts.append(f'<path d="{d}" fill="{col}" stroke="{col}"/>')
    eye = "M" + " L".join(f"{f(CX + (x - CX) * HEAD_SCALE)},{f(162 + (y - 162) * HEAD_SCALE)}" for x, y in EYE) + " Z"
    parts.append(f'<path d="{eye}" fill="#1c0507" stroke="#1c0507"/>')
    return f'<g stroke-width=".8" stroke-linejoin="round">{"".join(parts)}</g>'


SHADOW = """<defs><filter id="lift" x="-10%" y="-10%" width="120%" height="125%">
  <feDropShadow dx="0" dy="8" stdDeviation="10" flood-color="#000000" flood-opacity=".6"/>
</filter>
<linearGradient id="silverText" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="#ffffff"/><stop offset=".5" stop-color="#c3c6cc"/><stop offset=".56" stop-color="#e9eaed"/>
  <stop offset="1" stop-color="#8d919a"/>
</linearGradient></defs>"""


def emblem_svg():
    """The app icon: the phoenix on a dark rounded tile."""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">{SHADOW}'
            f'<rect x="8" y="8" width="496" height="496" rx="112" fill="{TILE}"/>'
            f'<rect x="9.5" y="9.5" width="493" height="493" rx="110.5" fill="none" stroke="#ffffff" stroke-opacity=".08" stroke-width="3"/>'
            f'<g filter="url(#lift)"><g transform="{fit((34, 34, 478, 478))}">{phoenix()}</g></g></svg>')


def title_paths(text, font, size, tracking, weight=700):
    """Text as outlines: returns (svg path elements, total width)."""
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont
    tt = TTFont(font)
    if "fvar" in tt:
        tt = instantiateVariableFont(tt, {"wght": weight})
    gs, cmap = tt.getGlyphSet(), tt.getBestCmap()
    k = size / tt["head"].unitsPerEm
    x, out = 0.0, []
    for ch in text:
        g = gs[cmap[ord(ch)]]
        pen = SVGPathPen(gs)
        g.draw(TransformPen(pen, (k, 0, 0, -k, x, 0)))
        out.append(f'<path d="{pen.getCommands()}"/>')
        x += g.width * k + tracking
    return "".join(out), x - tracking


def logo_svg(font):
    """The full logo on a dark field: phoenix above the JOBQUEST title, with AI set small between two rules."""
    W, H = 1000, 720
    words, ww = title_paths("JOBQUEST", font, 132, 14)
    ai, aw = title_paths("AI", font, 40, 22, weight=600)
    bird = f'<g filter="url(#lift)"><g transform="{fit((W / 2 - 240, 36, W / 2 + 240, 420))}">{phoenix()}</g></g>'
    title = f'<g fill="url(#silverText)" filter="url(#lift)" transform="translate({f((W - ww) / 2)} 588)">{words}</g>'
    rule_y, gap = 652, 34
    sub = (f'<g fill="#c9ccd2" transform="translate({f((W - aw) / 2)} {rule_y + 14})">{ai}</g>'
           f'<path d="M{f(W / 2 - aw / 2 - gap - 150)},{rule_y} H{f(W / 2 - aw / 2 - gap)} M{f(W / 2 + aw / 2 + gap)},{rule_y} '
           f'H{f(W / 2 + aw / 2 + gap + 150)}" stroke="#9a9ea6" stroke-width="2"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">{SHADOW}'
            f'<rect width="{W}" height="{H}" fill="{TILE}"/>{bird}{title}{sub}</svg>')


def render(svg_text, out, w, h):
    """Rasterise an SVG with headless Edge at w x h pixels, transparent outside the drawing.
    Run this from PowerShell or cmd: Edge started from some sandboxed shells exits without writing the file."""
    with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:         # not %TEMP%, which some apps sandbox
        page = Path(tmp) / "page.html"
        out = Path(out).resolve()
        out.unlink(missing_ok=True)
        page.write_text('<!doctype html><html><body style="margin:0;background:transparent">'
                        f'<div style="width:{w}px;height:{h}px">'
                        + svg_text.replace("<svg ", '<svg style="width:100%;height:100%;display:block" ', 1)
                        + "</div></body></html>", encoding="utf-8")
        subprocess.run([EDGE, "--headless", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={tmp}\\edge",
                        "--default-background-color=00000000", f"--window-size={w},{h}", f"--screenshot={out}",
                        page.as_uri()], check=True, capture_output=True, timeout=120)
    if not out.exists():
        sys.exit(f"Edge didn't write {out}")
    print("wrote", out)


if __name__ == "__main__":
    font = sys.argv[1] if len(sys.argv) > 1 else CINZEL
    emblem, logo = emblem_svg(), logo_svg(font)
    (HERE / "emblem.svg").write_text(emblem, encoding="utf-8")
    (HERE / "logo.svg").write_text(logo, encoding="utf-8")
    render(emblem, HERE / "logo.png", 1024, 1024)
    render(logo, ROOT / "docs" / "logo.png", 1000, 720)
