#!/usr/bin/env python3
"""
gtsend.py - SVG-Dateien direkt an einen Graphtec-Schneideplotter (z. B. CE8600)
per Netzwerk senden. Ohne Admin-Rechte, ohne Graphtec Studio.

Ablauf:  SVG  ->  Linienzüge (mm)  ->  HP-GL  ->  TCP-Port 9100 des Plotters

Nur Python-Standardbibliothek (Python 3.9+).

Beispiele:
  python gtsend.py --test                      # Testquadrat 20 mm schneiden
  python gtsend.py motiv.svg                   # Datei schneiden
  python gtsend.py motiv.svg --dry-run         # nur anzeigen, nichts senden
  python gtsend.py motiv.svg --out motiv.plt   # HP-GL als Datei speichern
  python gtsend.py motiv.svg --mirror          # gespiegelt (z. B. Flexfolie)
"""

import argparse
import math
import re
import socket
import sys
import xml.etree.ElementTree as ET

DEFAULT_HOST = "192.0.2.10"
DEFAULT_PORT = 9100
UNITS_PER_MM = 40.0          # HP-GL: 1 Plottereinheit = 0,025 mm
FLATTEN_TOL_MM = 0.05        # max. Abweichung beim Zerlegen von Kurven

# --------------------------------------------------------------------------
# 2D-Matrizen (a b c d e f) wie in SVG: x' = a*x + c*y + e, y' = b*x + d*y + f
# --------------------------------------------------------------------------
IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mmul(m, n):
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + c * B, b * A + d * B,
            a * C + c * D, b * C + d * D,
            a * E + c * F + e, b * E + d * F + f)


def mapply(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def mscale_factor(m):
    a, b, c, d, _, _ = m
    return math.sqrt(abs(a * d - b * c)) or 1.0


NUM_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def nums(s):
    return [float(v) for v in NUM_RE.findall(s or "")]


def parse_transform(s):
    m = IDENT
    if not s:
        return m
    for name, args in re.findall(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)", s):
        v = nums(args)
        if name == "matrix" and len(v) == 6:
            t = tuple(v)
        elif name == "translate":
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0)
        elif name == "scale":
            sx = v[0]
            sy = v[1] if len(v) > 1 else sx
            t = (sx, 0, 0, sy, 0, 0)
        elif name == "rotate":
            r = math.radians(v[0])
            cs, sn = math.cos(r), math.sin(r)
            t = (cs, sn, -sn, cs, 0, 0)
            if len(v) == 3:
                cx, cy = v[1], v[2]
                t = mmul(mmul((1, 0, 0, 1, cx, cy), t), (1, 0, 0, 1, -cx, -cy))
        elif name == "skewX":
            t = (1, 0, math.tan(math.radians(v[0])), 1, 0, 0)
        elif name == "skewY":
            t = (1, math.tan(math.radians(v[0])), 0, 1, 0, 0)
        else:
            continue
        m = mmul(m, t)
    return m


# --------------------------------------------------------------------------
# Einheiten
# --------------------------------------------------------------------------
UNIT_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4, "pt": 25.4 / 72, "pc": 25.4 / 6,
           "px": 25.4 / 96, "": 25.4 / 96}


def length_mm(s, px_mm=25.4 / 96):
    if s is None:
        return None
    m = re.match(r"\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([a-z%]*)", s)
    if not m or m.group(2) == "%":
        return None
    unit = m.group(2)
    return float(m.group(1)) * (px_mm if unit in ("", "px") else UNIT_MM.get(unit, px_mm))


def svg_px_mm(path):
    """Wie viele mm ein SVG-'px' in dieser Datei bedeutet.
    Adobe Illustrator schreibt px im 72-dpi-Raster (1 px = 1 pt), alle anderen 96 dpi."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096).decode("utf-8", "ignore")
    except OSError:
        return 25.4 / 96
    if "Adobe Illustrator" in head or "ns.adobe.com/AdobeIllustrator" in head:
        return 25.4 / 72
    return 25.4 / 96


# --------------------------------------------------------------------------
# Kurven zerlegen
# --------------------------------------------------------------------------
def _steps(length_est, scale):
    # Anzahl Segmente so, dass die Sehnenabweichung klein bleibt
    L = length_est * scale
    return max(2, min(400, int(math.sqrt(L / FLATTEN_TOL_MM) * 2) + 2))


def cubic(p0, p1, p2, p3, scale):
    est = (math.dist(p0, p1) + math.dist(p1, p2) + math.dist(p2, p3))
    n = _steps(est, scale)
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        x = u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0]
        y = u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]
        out.append((x, y))
    return out


def quad(p0, p1, p2, scale):
    est = math.dist(p0, p1) + math.dist(p1, p2)
    n = _steps(est, scale)
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                    u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
    return out


def arc(p0, rx, ry, phi_deg, large, sweep, p1, scale):
    # SVG-Endpunkt-Bogen -> Mittelpunkt-Darstellung (SVG 1.1, Anhang F.6)
    x1, y1 = p0
    x2, y2 = p1
    if (x1, y1) == (x2, y2):
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return [p1]
    phi = math.radians(phi_deg % 360)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p = cp * dx + sp * dy
    y1p = -sp * dx + cp * dy
    lam = (x1p / rx) ** 2 + (y1p / ry) ** 2
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    co = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        co = -co
    cxp = co * rx * y1p / ry
    cyp = -co * ry * x1p / rx
    cx = cp * cxp - sp * cyp + (x1 + x2) / 2
    cy = sp * cxp + cp * cyp + (y1 + y2) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    th1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dth = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dth > 0:
        dth -= 2 * math.pi
    elif sweep and dth < 0:
        dth += 2 * math.pi
    est = abs(dth) * max(rx, ry)
    n = _steps(est, scale)
    out = []
    for i in range(1, n + 1):
        t = th1 + dth * i / n
        ex, ey = rx * math.cos(t), ry * math.sin(t)
        out.append((cp * ex - sp * ey + cx, sp * ex + cp * ey + cy))
    out[-1] = p1
    return out


# --------------------------------------------------------------------------
# SVG-Pfad -> Liste von Linienzügen (in lokalen Koordinaten)
# --------------------------------------------------------------------------
PATH_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def parse_path(d, scale):
    toks = PATH_TOKEN.findall(d or "")
    polys = []
    cur = []
    x = y = 0.0
    sx = sy = 0.0
    last_c = None   # letzter kubischer Kontrollpunkt (für S)
    last_q = None   # letzter quadratischer Kontrollpunkt (für T)
    cmd = None
    i = 0

    def take(n):
        nonlocal i
        vals = []
        for _ in range(n):
            if i >= len(toks) or toks[i].isalpha():
                raise ValueError("Pfad unvollständig")
            vals.append(float(toks[i]))
            i += 1
        return vals

    def take_flag():
        nonlocal i
        t = toks[i]
        if t in ("0", "1"):
            i += 1
            return int(t)
        # z. B. "110" -> Flag "1", Rest "10" zurück in den Strom
        if t[0] in "01":
            toks[i] = t[1:]
            return int(t[0])
        i += 1
        return 1 if float(t) else 0

    while i < len(toks):
        if toks[i].isalpha():
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                if cur:
                    if cur[-1] != (sx, sy):
                        cur.append((sx, sy))
                    polys.append(cur)
                cur = []
                x, y = sx, sy
                last_c = last_q = None
                continue
        if cmd is None:
            break
        rel = cmd.islower()
        C = cmd.upper()
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if C == "M":
            px, py = take(2)
            x, y = ox + px, oy + py
            if cur and len(cur) > 1:
                polys.append(cur)
            cur = [(x, y)]
            sx, sy = x, y
            cmd = "l" if rel else "L"   # weitere Koordinatenpaare = Lineto
            last_c = last_q = None
        elif C == "L":
            px, py = take(2)
            x, y = ox + px, oy + py
            cur.append((x, y))
            last_c = last_q = None
        elif C == "H":
            (px,) = take(1)
            x = ox + px if rel else px
            cur.append((x, y))
            last_c = last_q = None
        elif C == "V":
            (py,) = take(1)
            y = oy + py if rel else py
            cur.append((x, y))
            last_c = last_q = None
        elif C == "C":
            a = take(6)
            p1 = (ox + a[0], oy + a[1])
            p2 = (ox + a[2], oy + a[3])
            p3 = (ox + a[4], oy + a[5])
            cur += cubic((x, y), p1, p2, p3, scale)
            last_c = p2
            last_q = None
            x, y = p3
        elif C == "S":
            a = take(4)
            p1 = (2 * x - last_c[0], 2 * y - last_c[1]) if last_c else (x, y)
            p2 = (ox + a[0], oy + a[1])
            p3 = (ox + a[2], oy + a[3])
            cur += cubic((x, y), p1, p2, p3, scale)
            last_c = p2
            last_q = None
            x, y = p3
        elif C == "Q":
            a = take(4)
            p1 = (ox + a[0], oy + a[1])
            p2 = (ox + a[2], oy + a[3])
            cur += quad((x, y), p1, p2, scale)
            last_q = p1
            last_c = None
            x, y = p2
        elif C == "T":
            a = take(2)
            p1 = (2 * x - last_q[0], 2 * y - last_q[1]) if last_q else (x, y)
            p2 = (ox + a[0], oy + a[1])
            cur += quad((x, y), p1, p2, scale)
            last_q = p1
            last_c = None
            x, y = p2
        elif C == "A":
            rx, ry, rot = take(3)
            large = take_flag()
            sweep = take_flag()
            px, py = take(2)
            p1 = (ox + px, oy + py)
            if not cur:
                cur = [(x, y)]
            cur += arc((x, y), rx, ry, rot, large, sweep, p1, scale)
            x, y = p1
            last_c = last_q = None
        else:
            raise ValueError(f"Unbekannter Pfadbefehl {cmd!r}")
        if not cur:
            cur = [(x, y)]
    if cur and len(cur) > 1:
        polys.append(cur)
    return polys


def ellipse_poly(cx, cy, rx, ry, scale):
    n = _steps(2 * math.pi * max(rx, ry), scale)
    n = max(n, 24)
    pts = [(cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n))
           for i in range(n)]
    pts.append(pts[0])
    return [pts]


def rect_poly(x, y, w, h, rx, ry, scale):
    if w <= 0 or h <= 0:
        return []
    if rx <= 0 and ry <= 0:
        return [[(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]]
    rx = min(rx or ry, w / 2)
    ry = min(ry or rx, h / 2)
    d = (f"M{x + rx},{y} H{x + w - rx} A{rx},{ry} 0 0 1 {x + w},{y + ry} "
         f"V{y + h - ry} A{rx},{ry} 0 0 1 {x + w - rx},{y + h} H{x + rx} "
         f"A{rx},{ry} 0 0 1 {x},{y + h - ry} V{y + ry} A{rx},{ry} 0 0 1 {x + rx},{y} Z")
    return parse_path(d, scale)


# --------------------------------------------------------------------------
# SVG-Dokument -> Linienzüge in mm (SVG-Koordinatensystem, y nach unten)
# --------------------------------------------------------------------------
def local(tag):
    return tag.rsplit("}", 1)[-1]


SKIP = {"defs", "clipPath", "mask", "pattern", "marker", "symbol", "metadata",
        "title", "desc", "style", "script", "linearGradient", "radialGradient", "filter"}


def load_svg(path):
    px = svg_px_mm(path)
    tree = ET.parse(path)
    root = tree.getroot()
    vb = nums(root.get("viewBox"))
    w_mm = length_mm(root.get("width"), px)
    h_mm = length_mm(root.get("height"), px)
    if len(vb) == 4 and vb[2] > 0 and vb[3] > 0:
        vx, vy, vw, vh = vb
        sx = (w_mm / vw) if w_mm else px
        sy = (h_mm / vh) if h_mm else sx
        if not w_mm and h_mm:
            sx = sy
        base = (sx, 0, 0, sy, -vx * sx, -vy * sy)
    else:
        base = (px, 0, 0, px, 0, 0)

    ids = {el.get("id"): el for el in root.iter() if el.get("id")}
    out = []

    def visit(el, m, depth=0):
        if depth > 64:
            return
        tag = local(el.tag)
        if tag in SKIP:
            return
        style = el.get("style") or ""
        if re.search(r"display\s*:\s*none", style) or el.get("display") == "none":
            return
        if el.get("visibility") == "hidden":
            return
        m = mmul(m, parse_transform(el.get("transform")))
        scale = mscale_factor(m)
        g = lambda k, d=0.0: float(nums(el.get(k))[0]) if nums(el.get(k)) else d
        polys = []
        if tag == "path":
            polys = parse_path(el.get("d"), scale)
        elif tag == "rect":
            polys = rect_poly(g("x"), g("y"), g("width"), g("height"), g("rx"), g("ry"), scale)
        elif tag == "circle":
            r = g("r")
            if r > 0:
                polys = ellipse_poly(g("cx"), g("cy"), r, r, scale)
        elif tag == "ellipse":
            if g("rx") > 0 and g("ry") > 0:
                polys = ellipse_poly(g("cx"), g("cy"), g("rx"), g("ry"), scale)
        elif tag == "line":
            polys = [[(g("x1"), g("y1")), (g("x2"), g("y2"))]]
        elif tag in ("polyline", "polygon"):
            v = nums(el.get("points"))
            pts = list(zip(v[0::2], v[1::2]))
            if tag == "polygon" and pts:
                pts.append(pts[0])
            if len(pts) > 1:
                polys = [pts]
        elif tag == "use":
            href = el.get("{http://www.w3.org/1999/xlink}href") or el.get("href") or ""
            ref = ids.get(href.lstrip("#"))
            if ref is not None:
                mm_ = mmul(m, (1, 0, 0, 1, g("x"), g("y")))
                if local(ref.tag) == "symbol":
                    for ch in ref:
                        visit(ch, mm_, depth + 1)
                else:
                    visit(ref, mm_, depth + 1)
            return
        for p in polys:
            out.append([mapply(m, px, py) for px, py in p])
        for ch in el:
            visit(ch, m, depth + 1)

    visit(root, base)
    return out


# --------------------------------------------------------------------------
# Testmuster
# --------------------------------------------------------------------------
def test_pattern(size=20.0):
    s = size
    sq = [(0, 0), (s, 0), (s, s), (0, s), (0, 0)]
    # kleines Dreieck in der Ecke links unten (im SVG-System: unten = großes y)
    tri = [(2, s - 2), (6, s - 2), (2, s - 6), (2, s - 2)]
    return [sq, tri]


# --------------------------------------------------------------------------
# Lage auf dem Material, HP-GL erzeugen
# --------------------------------------------------------------------------
def bbox(polys):
    xs = [x for p in polys for x, _ in p]
    ys = [y for p in polys for _, y in p]
    return min(xs), min(ys), max(xs), max(ys)


def place(polys, rotate=0, mirror=False, scale=1.0, origin=(0.0, 0.0)):
    """SVG (y nach unten) -> Plotter (y nach oben), auf Nullpunkt verschieben."""
    res = []
    for p in polys:
        q = []
        for x, y in p:
            x, y = x * scale, -y * scale          # y umdrehen
            if mirror:
                x = -x
            for _ in range((rotate // 90) % 4):   # gegen den Uhrzeigersinn
                x, y = -y, x
            q.append((x, y))
        res.append(q)
    x0, y0, _, _ = bbox(res)
    ox, oy = origin
    return [[(x - x0 + ox, y - y0 + oy) for x, y in p] for p in res]


def simplify(p, tol_units=1):
    """Doppelte/zu nahe Punkte entfernen (in Plottereinheiten)."""
    out = [p[0]]
    for pt in p[1:]:
        if abs(pt[0] - out[-1][0]) >= tol_units or abs(pt[1] - out[-1][1]) >= tol_units:
            out.append(pt)
    if len(out) == 1 and len(p) > 1:
        out.append(p[-1])
    return out


def order_paths(polys, start=(0.0, 0.0)):
    """Einfache Nächster-Nachbar-Sortierung, damit der Kopf nicht kreuz und quer fährt.
    Offene Linien (z. B. Entgitterlinien) dürfen auch rückwärts geschnitten werden."""
    rest = list(polys)
    done = []
    cx, cy = start

    def d2(pt):
        return (pt[0] - cx) ** 2 + (pt[1] - cy) ** 2

    while rest:
        best, rev, bd = 0, False, None
        for k, p in enumerate(rest):
            closed = p[0] == p[-1]
            for r in ((False, True) if not closed and len(p) == 2 else (False,)):
                d = d2(p[-1] if r else p[0])
                if bd is None or d < bd:
                    best, rev, bd = k, r, d
        p = rest.pop(best)
        if rev:
            p = p[::-1]
        done.append(p)
        cx, cy = p[-1]
    return done


# --------------------------------------------------------------------------
# Entgittern: Rahmen und Hilfslinien, die das Motiv nicht durchschneiden
# --------------------------------------------------------------------------
def _closed(p, tol=0.05):
    return len(p) > 3 and math.dist(p[0], p[-1]) <= tol


def _inside(closed_polys, x, y):
    """Gerade-Ungerade-Regel: liegt (x, y) in der Fläche des Motivs?"""
    inside = False
    for p in closed_polys:
        for (ax, ay), (bx, by) in zip(p, p[1:]):
            if (ay > y) != (by > y):
                xi = ax + (y - ay) * (bx - ax) / (by - ay)
                if xi > x:
                    inside = not inside
    return inside


def _crossings(polys, horizontal, c, lo, hi):
    """Parameter (x bzw. y), an denen eine waagerechte/senkrechte Linie das Motiv schneidet."""
    ts = []
    for p in polys:
        for (ax, ay), (bx, by) in zip(p, p[1:]):
            if horizontal:
                if (ay - c) * (by - c) <= 0 and ay != by:
                    t = ax + (c - ay) * (bx - ax) / (by - ay)
                    if lo <= t <= hi:
                        ts.append(t)
                elif ay == by == c:          # Segment liegt genau auf der Linie
                    ts.extend([ax, bx])
            else:
                if (ax - c) * (bx - c) <= 0 and ax != bx:
                    t = ay + (c - ax) * (by - ay) / (bx - ax)
                    if lo <= t <= hi:
                        ts.append(t)
                elif ax == bx == c:
                    ts.extend([ay, by])
    return sorted(ts)


def weed_line(polys, closed_polys, horizontal, c, lo, hi, gap=1.0, min_len=1.0):
    """Eine Entgitterlinie von lo bis hi; Stücke im Motiv entfallen,
    an jedem Motivkontakt bleibt `gap` mm Abstand."""
    cuts = [t for t in _crossings(polys, horizontal, c, lo, hi)]
    stops = [lo] + cuts + [hi]
    out = []
    for a, b in zip(stops, stops[1:]):
        if b - a < 1e-6:
            continue
        m = (a + b) / 2
        mx, my = (m, c) if horizontal else (c, m)
        if _inside(closed_polys, mx, my):
            continue
        a2 = a + gap if a > lo + 1e-9 else a
        b2 = b - gap if b < hi - 1e-9 else b
        if b2 - a2 < min_len:
            continue
        out.append([(a2, c), (b2, c)] if horizontal else [(c, a2), (c, b2)])
    return out


def weeding(placed, box_margin=3.0, box=True, h=(), v=(), gap=1.0, shift=True):
    """Entgitter-Geometrie für ein platziertes Motiv (mm, Plotterlage).

    h, v:  Positionen der waagerechten / senkrechten Linien als Anteil 0..1
           der Entgitterfläche (Motiv + Rahmenabstand).
    shift: alles so verschieben, dass der Rahmen am bisherigen Motiv-Nullpunkt
           beginnt (sonst lägen Rahmenteile bei negativen Koordinaten).

    Rückgabe: (motiv, linien, rahmen) – jeweils Listen von Linienzügen.
    """
    x0, y0, x1, y1 = bbox(placed)
    bm = max(0.0, box_margin)
    dx = dy = bm if shift else 0.0
    motif = [[(x + dx, y + dy) for x, y in p] for p in placed] if (dx or dy) else placed
    X0, Y0, X1, Y1 = x0 + dx - bm, y0 + dy - bm, x1 + dx + bm, y1 + dy + bm
    closed = [p for p in motif if _closed(p)]
    lines = []
    for f in h:
        y = Y0 + max(0.0, min(1.0, f)) * (Y1 - Y0)
        if Y0 < y < Y1:
            lines += weed_line(motif, closed, True, y, X0, X1, gap)
    for f in v:
        x = X0 + max(0.0, min(1.0, f)) * (X1 - X0)
        if X0 < x < X1:
            lines += weed_line(motif, closed, False, x, Y0, Y1, gap)
    frame = [[(X0, Y0), (X1, Y0), (X1, Y1), (X0, Y1), (X0, Y0)]] if box else []
    return motif, lines, frame


def grid_positions(extent_mm, spacing_mm):
    """Gleichmäßig verteilte Linienpositionen (Anteile 0..1) für einen Abstand in mm."""
    if spacing_mm <= 0 or extent_mm <= spacing_mm:
        return []
    n = int(extent_mm // spacing_mm)
    if extent_mm - n * spacing_mm < spacing_mm * 0.25:
        n -= 1
    return [(i + 1) / (n + 1) for i in range(max(0, n))]


def to_hpgl(polys_mm, speed=None, force=None, pen=1, sort=True, after=()):
    """polys_mm wird zuerst geschnitten; `after` sind weitere Gruppen
    (z. B. Entgitterlinien, dann Rahmen), die danach in dieser Reihenfolge folgen."""
    def units(group):
        res = []
        for p in group:
            q = simplify([(round(x * UNITS_PER_MM), round(y * UNITS_PER_MM)) for x, y in p])
            if len(q) > 1:
                res.append(q)
        return res

    polys = []
    pos = (0, 0)
    for group in [polys_mm, *after]:
        g = units(group)
        if sort and g:
            g = order_paths(g, pos)
        if g:
            pos = g[-1][-1]
        polys += g
    cmds = ["IN", f"SP{pen}"]
    if speed:
        cmds.append(f"VS{int(speed)}")
    if force:
        cmds.append(f"FS{int(force)}")
    for p in polys:
        x, y = p[0]
        cmds.append(f"PU{x},{y}")
        # lange Linienzüge in Blöcke aufteilen
        rest = p[1:]
        while rest:
            chunk, rest = rest[:200], rest[200:]
            cmds.append("PD" + ",".join(f"{a},{b}" for a, b in chunk))
    cmds.append("PU0,0")
    cmds.append("SP0")
    return ";".join(cmds) + ";\n", polys


# --------------------------------------------------------------------------
# Senden
# --------------------------------------------------------------------------
def send(host, port, data, timeout=10.0):
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(data.encode("ascii"))
        try:
            s.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def query(host, port, cmd, wait=2.0):
    """Einen Abfragebefehl senden und die Antwortzeile (bis CR) zurückgeben."""
    with socket.create_connection((host, port), timeout=5) as s:
        s.settimeout(wait)
        s.sendall(cmd.encode("latin-1"))
        buf = b""
        while not buf.endswith((b"\r", b"\n")):
            try:
                b = s.recv(256)
            except socket.timeout:
                break
            if not b:
                break
            buf += b
    return buf.decode("latin-1").strip()


def query_media(host, port):
    """Schneidebereich der eingelegten Folie per HP-GL 'OW' abfragen.
    Rückgabe: (Länge_mm, Breite_mm) – Länge = Vorschub (X), Breite = quer (Y)."""
    r = query(host, port, "OW;")
    v = [int(t) for t in re.findall(r"-?\d+", r)]
    if len(v) != 4:
        raise OSError(f"unerwartete Antwort auf OW: {r!r}")
    x0, y0, x1, y1 = v
    return (x1 - x0) / UNITS_PER_MM, (y1 - y0) / UNITS_PER_MM


def fit(polys, media_len, media_w, margin=5.0, rotate=0, mirror=False, allow_rotate=False,
        upscale=True):
    """Größte Skalierung (und ggf. Drehung), mit der das Motiv auf die Folie passt.
    Rückgabe: (scale, rotate)."""
    avail_x = max(1.0, media_len - 2 * margin) if media_len else None
    avail_y = max(1.0, media_w - 2 * margin)
    best = None
    for rot in ([0, 90] if allow_rotate else [rotate]):
        if allow_rotate and rotate in (180, 270):
            rot = (rot + 180) % 360
        x0, y0, x1, y1 = bbox(place(polys, rot, mirror, 1.0))
        w, h = (x1 - x0) or 1e-9, (y1 - y0) or 1e-9
        s = avail_y / h
        if avail_x:
            s = min(s, avail_x / w)
        if not upscale:
            s = min(s, 1.0)
        if best is None or s > best[0] + 1e-9:
            best = (s, rot)
    return best


# --------------------------------------------------------------------------
# VectorCraft-Fernsteuerung (vectorcraft.exe --control 7979)
# --------------------------------------------------------------------------
VC_PORT = 7979


class VectorCraftError(OSError):
    pass


def _vc_call(f, sock, method, params, _id=[0]):
    import json
    _id[0] += 1
    sock.sendall((json.dumps({"id": _id[0], "method": method, "params": params}) + "\n").encode("utf-8"))
    line = f.readline()
    if not line:
        raise VectorCraftError("VectorCraft hat die Verbindung geschlossen")
    r = json.loads(line)
    if not r.get("ok"):
        raise VectorCraftError(f"VectorCraft meldet: {r.get('error')}")
    return r.get("result")


def vc_export(path, selected_only=False, port=VC_PORT, timeout=60.0):
    """Aktives VectorCraft-Dokument als SVG nach `path` exportieren (Text in Pfade).
    Das Dokument selbst bleibt unverändert. Rückgabe: (geschriebener Pfad, Dokumenttitel)."""
    import os
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    except OSError as e:
        raise VectorCraftError(f"keine Verbindung zu VectorCraft auf Port {port} – "
                               f"mit  --control {port}  gestartet?") from e
    with sock:
        sock.settimeout(timeout)
        f = sock.makefile("r", encoding="utf-8")
        ui = _vc_call(f, sock, "ui.inspect", {}) or {}
        docs, act = ui.get("documents") or [], ui.get("activeDocument")
        if act is None or not docs:
            raise VectorCraftError("in VectorCraft ist kein Dokument geöffnet")
        title = (docs[act] if isinstance(act, int) and act < len(docs) else docs[0]).get("title", "VectorCraft")
        params = {"path": path, "format": "svg", "selectedOnly": bool(selected_only),
                  "svg": {"outlineText": True}}
        try:
            res = _vc_call(f, sock, "engine.execute", {"command": "document.export", "params": params})
        except VectorCraftError as e:
            if "svg" not in str(e).lower() and "outline" not in str(e).lower():
                raise
            # ältere Version ohne diese Option – ohne SVG-Optionen erneut versuchen
            params.pop("svg")
            sock.close()
            return vc_export_plain(path, params, port, timeout, title)
    if isinstance(res, dict) and res.get("pending"):
        raise VectorCraftError(f"VectorCraft wartet auf einen Dialog ({res['pending']}) – dort bitte abbrechen")
    out = (res or {}).get("path") or path
    files = (res or {}).get("files") or []
    if not os.path.exists(out) and files:
        out = files[0] if isinstance(files[0], str) else files[0].get("path", out)
    if not os.path.exists(out):
        raise VectorCraftError(f"Export gemeldet, aber Datei fehlt: {out}")
    return out, title


def vc_export_plain(path, params, port, timeout, title):
    import os
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.settimeout(timeout)
        f = sock.makefile("r", encoding="utf-8")
        res = _vc_call(f, sock, "engine.execute", {"command": "document.export", "params": params})
    out = (res or {}).get("path") or path
    if not os.path.exists(out):
        raise VectorCraftError(f"Export gemeldet, aber Datei fehlt: {out}")
    return out, title


def main():
    ap = argparse.ArgumentParser(description="SVG per Netzwerk an Graphtec-Schneideplotter senden (HP-GL, Port 9100).")
    ap.add_argument("svg", nargs="?", help="SVG-Datei (z. B. aus VectorCraft exportiert)")
    ap.add_argument("--from-vectorcraft", action="store_true",
                    help="aktuelles Dokument direkt aus VectorCraft holen (VectorCraft mit --control 7979 starten)")
    ap.add_argument("--vc-selection", action="store_true", help="dabei nur die Auswahl exportieren")
    ap.add_argument("--vc-port", type=int, default=VC_PORT, help="Steuerport von VectorCraft (Standard 7979)")
    ap.add_argument("--test", action="store_true", help="Testmuster schneiden (Quadrat + Dreieck)")
    ap.add_argument("--test-size", type=float, default=20.0, help="Kantenlänge des Testquadrats in mm (Standard 20)")
    ap.add_argument("--host", default=DEFAULT_HOST, help=f"IP des Plotters (Standard {DEFAULT_HOST})")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP-Port (Standard 9100)")
    ap.add_argument("--mirror", action="store_true", help="spiegeln (z. B. Flex-/Flockfolie)")
    ap.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270], help="drehen in Grad")
    ap.add_argument("--scale", type=float, default=1.0, help="Skalierung, z. B. 0.5")
    ap.add_argument("--x", type=float, default=0.0, help="Abstand vom Nullpunkt in Vorschubrichtung (mm)")
    ap.add_argument("--y", type=float, default=0.0, help="Abstand vom Nullpunkt quer (mm)")
    ap.add_argument("--speed", type=int, help="Geschwindigkeit (HP-GL VS, cm/s) – leer = Einstellung am Plotter")
    ap.add_argument("--force", type=int, help="Messerdruck (FS) – leer = Einstellung am Plotter")
    ap.add_argument("--no-sort", action="store_true", help="Reihenfolge aus der SVG beibehalten")
    ap.add_argument("--out", help="HP-GL zusätzlich/stattdessen in Datei schreiben")
    ap.add_argument("--dry-run", action="store_true", help="nichts senden, nur Zusammenfassung")
    ap.add_argument("--media", action="store_true", help="nur Folienmaß vom Plotter abfragen und anzeigen")
    ap.add_argument("--fit", action="store_true", help="Motiv proportional auf die eingelegte Folie skalieren")
    ap.add_argument("--fit-rotate", action="store_true", help="beim Einpassen auch um 90° drehen, wenn es größer passt")
    ap.add_argument("--margin", type=float, default=5.0, help="Rand beim Einpassen in mm (Standard 5)")
    wg = ap.add_argument_group("Entgittern")
    wg.add_argument("--weed-box", type=float, metavar="MM",
                    help="Entgitterrahmen mit diesem Abstand ums Motiv schneiden")
    wg.add_argument("--weed-grid", type=float, metavar="MM",
                    help="Entgitterlinien im Raster (waagerecht und senkrecht) mit diesem Abstand")
    wg.add_argument("--weed-h", metavar="MM,MM", help="waagerechte Linien, mm ab Unterkante Entgitterfläche")
    wg.add_argument("--weed-v", metavar="MM,MM", help="senkrechte Linien, mm ab linker Kante Entgitterfläche")
    wg.add_argument("--weed-gap", type=float, default=1.0, help="Abstand der Linien zum Motiv in mm (Standard 1)")
    a = ap.parse_args()

    if a.media:
        try:
            ml, mw = query_media(a.host, a.port)
        except OSError as e:
            sys.exit(f"Abfrage fehlgeschlagen: {e}")
        print(f"Folie: {mw:.1f} mm breit, Schneidelänge {ml:.1f} mm")
        return

    if a.from_vectorcraft:
        import os
        import tempfile
        tmp = os.path.join(tempfile.gettempdir(), "gtsend", "vectorcraft.svg")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        try:
            a.svg, title = vc_export(tmp, a.vc_selection, a.vc_port)
        except OSError as e:
            sys.exit(f"Export aus VectorCraft fehlgeschlagen: {e}")
        print(f"Aus VectorCraft geholt: {title}" + (" (nur Auswahl)" if a.vc_selection else ""))

    if not a.test and not a.svg:
        ap.error("SVG-Datei angeben oder --test verwenden")

    if a.test:
        polys = test_pattern(a.test_size)
        name = f"Testmuster {a.test_size:g} mm"
    else:
        try:
            polys = load_svg(a.svg)
        except (ET.ParseError, OSError) as e:
            sys.exit(f"Fehler beim Lesen der SVG: {e}")
        name = a.svg
    polys = [p for p in polys if len(p) > 1]
    if not polys:
        sys.exit("Keine schneidbaren Linien gefunden (Text vorher in Pfade umwandeln?).")

    if a.fit:
        try:
            ml, mw = query_media(a.host, a.port)
        except OSError as e:
            sys.exit(f"Folienmaß nicht abfragbar ({e}) – ohne --fit oder mit --scale arbeiten.")
        s, a.rotate = fit(polys, ml, mw, a.margin, a.rotate, a.mirror, a.fit_rotate)
        a.scale = s
        a.x = a.y = a.margin
        print(f"Folie:    {mw:.1f} mm breit, {ml:.1f} mm lang  ->  Skalierung {s * 100:.1f} %, "
              f"Drehung {a.rotate}°, Rand {a.margin:g} mm")

    placed = place(polys, a.rotate, a.mirror, a.scale, (a.x, a.y))
    after = []
    if a.weed_box is not None or a.weed_grid or a.weed_h or a.weed_v:
        bm = a.weed_box if a.weed_box is not None else 0.0
        mx0, my0, mx1, my1 = bbox(placed)
        W, H = (mx1 - mx0) + 2 * bm, (my1 - my0) + 2 * bm

        def mm_list(s, extent):
            return [float(t) / extent for t in (s or "").replace(";", ",").split(",") if t.strip()]

        hs = mm_list(a.weed_h, H) + (grid_positions(H, a.weed_grid) if a.weed_grid else [])
        vs = mm_list(a.weed_v, W) + (grid_positions(W, a.weed_grid) if a.weed_grid else [])
        placed, lines, frame = weeding(placed, bm, a.weed_box is not None, hs, vs, a.weed_gap)
        after = [lines, frame]
        print(f"Entgittern: {len(lines)} Linienstücke"
              + (f", Rahmen {bm:g} mm Abstand" if frame else ""))
    x0, y0, x1, y1 = bbox(placed + [p for g in after for p in g])
    hpgl, final = to_hpgl(placed, a.speed, a.force, sort=not a.no_sort, after=after)
    npts = sum(len(p) for p in final)

    print(f"Quelle:   {name}")
    print(f"Größe:    {x1 - x0:.1f} x {y1 - y0:.1f} mm")
    print(f"Lage:     {x0:.1f}/{y0:.1f} bis {x1:.1f}/{y1:.1f} mm ab Nullpunkt")
    print(f"Inhalt:   {len(final)} Linienzüge, {npts} Punkte, {len(hpgl)} Byte HP-GL")

    if a.out:
        with open(a.out, "w", encoding="ascii") as f:
            f.write(hpgl)
        print(f"Gespeichert: {a.out}")
    if a.dry_run or (a.out and a.host == "-"):
        print("Probelauf – nichts gesendet.")
        return
    print(f"Sende an {a.host}:{a.port} …", end=" ", flush=True)
    try:
        send(a.host, a.port, hpgl)
    except OSError as e:
        print()
        sys.exit(f"Senden fehlgeschlagen: {e}")
    print("fertig.")


if __name__ == "__main__":
    main()
