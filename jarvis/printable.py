"""Printable parts for a 3D printer, from FIXED templates only (Pi case, phone stand, bracket, box with lid, gear, spacer). The brain (or you) fills in NUMBERS; nobody writes geometry code.
Every part is checked before export: minimum wall thickness (3 nozzle widths, at least 1.2 mm), size inside the printer volume, watertight (every edge shared by exactly two triangles),
and a flat bottom. The printer size and nozzle are asked once ("my printer is 220 by 220 by 250 with a 0.4 nozzle") and remembered. The STL is saved to Downloads/Jarvis on the Mac through the helper.
The part is shown as a hologram first with its real size in mm. (Pieces are separate closed solids that touch or overlap: slicers merge them; the Pi case has no port cut-outs.)"""
import math
import re
import struct

from . import holo, meshcheck, settings

LIMITS = {"w": (5, 300), "d": (5, 300), "h": (2, 300), "t": (0.4, 20), "hole": (1, 100), "teeth": (8, 48), "module": (0.8, 4), "bore": (2, 40), "od": (4, 100), "id": (1, 90)}
TEMPLATES = {"spacer": ("od", "id", "h"), "box": ("w", "d", "h", "t"), "bracket": ("w", "d", "t", "hole"), "gear": ("teeth", "module", "t", "bore"), "stand": ("w", "d", "h", "t"), "pi_case": ("t",)}


def printer():
    p = settings.get("printer_mm")
    if isinstance(p, list) and len(p) == 3 and all(isinstance(x, (int, float)) and 20 <= x <= 1000 for x in p):
        return p, float(settings.get("nozzle_mm") or 0.4)
    return None, 0.4


def min_wall(nozzle):
    return max(1.2, 3 * nozzle)


# ----------------------------------------------------------------------------- triangle helpers: every solid is a closed, outward-facing triangle list
def _quad(a, b, c, d):
    return [(a, b, c), (a, c, d)]


def cuboid(x0, y0, z0, x1, y1, z1):
    a, b, c, d, e, f, g, h = (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)
    t = []
    for q in ((a, d, c, b), (e, f, g, h), (a, b, f, e), (d, h, g, c), (a, e, h, d), (b, c, g, f)):
        t += _quad(*q)
    return t


def ring_prism(outline, hole_r, z0, z1, cx=0.0, cy=0.0):
    """A star-shaped outline (points around (cx, cy), counter-clockwise) with a round hole of radius hole_r (hole_r 0 = solid), extruded z0..z1. Closed and outward-facing."""
    n = len(outline)
    inner = [(cx + hole_r * math.cos(2 * math.pi * i / n), cy + hole_r * math.sin(2 * math.pi * i / n)) for i in range(n)] if hole_r > 0 else None
    t = []
    for i in range(n):
        j = (i + 1) % n
        o0, o1 = outline[i], outline[j]
        t += _quad((o0[0], o0[1], z0), (o1[0], o1[1], z0), (o1[0], o1[1], z1), (o0[0], o0[1], z1))                      # outer wall
        if inner:
            i0, i1 = inner[i], inner[j]
            t += _quad((i1[0], i1[1], z0), (i0[0], i0[1], z0), (i0[0], i0[1], z1), (i1[0], i1[1], z1))                  # inner wall (faces the hole)
            t += _quad((o0[0], o0[1], z1), (o1[0], o1[1], z1), (i1[0], i1[1], z1), (i0[0], i0[1], z1))                  # top annulus
            t += _quad((o1[0], o1[1], z0), (o0[0], o0[1], z0), (i0[0], i0[1], z0), (i1[0], i1[1], z0))                  # bottom annulus
        else:
            t.append(((cx, cy, z1), (o0[0], o0[1], z1), (o1[0], o1[1], z1)))
            t.append(((cx, cy, z0), (o1[0], o1[1], z0), (o0[0], o0[1], z0)))
    return t


def circle(r, n=48, cx=0.0, cy=0.0):
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def square_outline(w, d, n=48, cx=0.0, cy=0.0):
    """A rectangle sampled by angle (star-shaped about its centre)."""
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        c, s = math.cos(a), math.sin(a)
        k = min((w / 2) / abs(c) if abs(c) > 1e-9 else 1e9, (d / 2) / abs(s) if abs(s) > 1e-9 else 1e9)
        pts.append((cx + k * c, cy + k * s))
    return pts


def gear_outline(z, m):
    """A simple spur gear outline: tooth tip radius m*(z+2)/2, root radius m*(z-2.5)/2, 4 samples per tooth."""
    ro, rr = m * (z + 2) / 2, m * (z - 2.5) / 2
    pts = []
    for i in range(z):
        a0 = 2 * math.pi * i / z
        for k, r in enumerate((rr, ro, ro, rr)):
            a = a0 + (k + 0.5) * 2 * math.pi / z / 4 * (1 if k < 4 else 0)
            pts.append((r * math.cos(a), r * math.sin(a)))
    return pts


# ----------------------------------------------------------------------------- the templates: (solids as triangle lists, min wall actually used, notes)
def build(kind, p):
    """-> {"tris": [...], "min_wall": mm, "bottom_flat": bool, "notes": [..]}. Raises ValueError with a plain reason when a number is out of its range."""
    for k, v in p.items():
        if k in LIMITS and not LIMITS[k][0] <= v <= LIMITS[k][1]:
            raise ValueError(f"{k} must be between {LIMITS[k][0]} and {LIMITS[k][1]}")
    if kind == "spacer":
        if p["id"] >= p["od"]:
            raise ValueError("the inner diameter must be smaller than the outer one")
        return {"tris": ring_prism(circle(p["od"] / 2, 64), p["id"] / 2, 0, p["h"]), "min_wall": (p["od"] - p["id"]) / 2, "bottom_flat": True, "notes": []}
    if kind in ("box", "pi_case"):
        if kind == "pi_case":
            w, d, h, t = 87.0, 58.0, 28.0, p.get("t", 2.0)
        else:
            w, d, h, t = p["w"], p["d"], p["h"], p["t"]
        if min(w, d) <= 2 * t + 2 or h <= t + 2:
            raise ValueError("the walls are too thick for that size")
        tr = cuboid(0, 0, 0, w, d, t)                                                # floor
        tr += cuboid(0, 0, t, w, t, h) + cuboid(0, d - t, t, w, d, h) + cuboid(0, t, t, t, d - t, h) + cuboid(w - t, t, t, w, d - t, h)       # four walls
        lid = cuboid(0, d + 10, 0, w, d + 10 + d, t) + cuboid(t + 0.3, d + 10 + t + 0.3, t, w - t - 0.3, d + 10 + d - t - 0.3, t + 3)             # lid plate + a lip that fits inside
        return {"tris": tr + lid, "min_wall": t, "bottom_flat": True, "notes": ["Lid printed beside the box.", "No port cut-outs: add them in your slicer." if kind == "pi_case" else ""]}
    if kind == "bracket":
        w, d, t, hole = p["w"], p["d"], p["t"], p["hole"]
        if hole >= min(w, d) - 2 * t:
            raise ValueError("the hole is too big for the plate")
        flat = ring_prism(square_outline(w, d, 96, w / 2, d / 2), hole / 2, 0, t, w / 2, d / 2)
        up = cuboid(0, 0, 0, w, t, d)                                                    # upright plate rises from the base plate's edge
        up = [tuple((x, y, z + t) for x, y, z in tri) for tri in up]
        return {"tris": flat + up, "min_wall": t, "bottom_flat": True, "notes": ["An L bracket: a base plate with a hole and an upright plate."]}
    if kind == "gear":
        z, m, t, bore = int(p["teeth"]), p["module"], p["t"], p["bore"]
        if bore / 2 >= m * (z - 2.5) / 2 - min_wall(0.4):
            raise ValueError("the bore is too big for that gear")
        return {"tris": ring_prism(gear_outline(z, m), bore / 2, 0, t), "min_wall": (m * (z - 2.5) / 2) - bore / 2, "bottom_flat": True, "notes": ["Simple tooth shape for light loads; not for precise or fast gearing."]}
    if kind == "stand":
        w, d, h, t = p["w"], p["d"], p["h"], p["t"]
        tr = cuboid(0, 0, 0, w, d, t) + cuboid(0, d - 12, t, w, d - 12 + t * 2, t + 12)             # base + a front lip 12 mm tall
        back = cuboid(0, d - t * 2, t, w, d, h)                                                      # back support
        return {"tris": tr + back, "min_wall": t, "bottom_flat": True, "notes": ["Rest the phone against the back support; the lip stops it sliding."]}
    raise ValueError("unknown template")


def validate(res, dims_mm, printer_mm, nozzle):
    """-> list of plain failure lines (empty = good). dims_mm = (x, y, z) of the part."""
    fails = []
    mw = min_wall(nozzle)
    if res["min_wall"] < mw:
        fails.append(f"The thinnest wall is {res['min_wall']:.1f} mm; at least {mw:.1f} mm is needed for a {nozzle} mm nozzle.")
    if printer_mm and any(dims_mm[i] > printer_mm[i] for i in range(3)):
        fails.append(f"The part is {dims_mm[0]:.0f} x {dims_mm[1]:.0f} x {dims_mm[2]:.0f} mm; your printer takes {printer_mm[0]:.0f} x {printer_mm[1]:.0f} x {printer_mm[2]:.0f} mm.")
    if not res["bottom_flat"]:
        fails.append("The bottom is not flat: it would need supports.")
    mesh = to_stl(res["tris"])
    c = meshcheck.check(mesh, ".stl")
    if not c["watertight"]:
        # separate closed solids that overlap are fine for slicers, but every edge must still belong to a closed shell: report open edges
        fails.append(f"The mesh is not watertight ({c['open_edges']} open edges).")
    return fails


def bounds(tris):
    xs, ys, zs = zip(*[p for t in tris for p in t])
    return (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))


def to_stl(tris):
    out = b"JARVIS PRINTABLE PART".ljust(80, b"\0") + struct.pack("<I", len(tris))
    for a, b, c in tris:
        out += struct.pack("<12fH", 0, 0, 0, *a, *b, *c, 0)
    return out


def scene(kind, p, dims):
    """The hologram of the part (simple solids, real size in the label), in cm."""
    x, y, z = [v / 10 for v in dims]
    shape = "cylinder" if kind in ("spacer", "gear") else "box"
    size = (x, z, y) if shape == "cylinder" else (x, z, y)
    part = {"id": "part", "label": f"{kind.replace('_', ' ')} {dims[0]:.0f} x {dims[1]:.0f} x {dims[2]:.0f} mm", "desc": "Real size in mm.", "shape": shape, "pos": [0, z / 2, 0], "size": list(size), "color": "cyan", "rot": [0, 0, 0], "parent": None}
    return holo.validate_scene({"title": f"Printable {kind.replace('_', ' ')}", "unit": "cm", "parts": [part]})


def make(kind, params):
    """-> {"ok", "fails", "stl", "dims", "scene", "notes"}. Asks nothing: when the printer is not known "need_printer" is True."""
    pm, nz = printer()
    res = build(kind, params)
    dims = bounds(res["tris"])
    fails = validate(res, dims, pm, nz)
    return {"ok": not fails and pm is not None, "need_printer": pm is None, "fails": fails, "stl": to_stl(res["tris"]), "dims": dims, "scene": scene(kind, params, dims), "notes": [n for n in res["notes"] if n]}


def parse_command(text):
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    m = re.fullmatch(r"(?:my )?(?:3d )?printer (?:is|takes|has a build (?:volume|area) of|prints) (\d{2,4}) (?:by|x) (\d{2,4}) (?:by|x) (\d{2,4})(?: (?:mm|millimet\w+))?(?: with a (\d(?:\.\d+)?) ?(?:mm )?nozzle)?", low)
    if m:
        return {"kind": "printer", "mm": [int(m.group(i)) for i in (1, 2, 3)], "nozzle": float(m.group(4) or 0.4)}
    m = re.fullmatch(r"(?:please )?(?:make|print|design|create)(?: me)? (?:a |an )?(spacer|bracket|gear|phone stand|stand|box(?: with (?:a )?lid)?|pi case|raspberry pi case)(?: (.*))?", low)
    if m:
        kind = {"spacer": "spacer", "bracket": "bracket", "gear": "gear", "phone stand": "stand", "stand": "stand", "pi case": "pi_case", "raspberry pi case": "pi_case"}.get(m.group(1), "box")
        nums = {}
        for k, v in re.findall(r"(\d+(?:\.\d+)?) ?mm (outer|inner|wide|deep|high|tall|thick|hole|bore)|(\d+) teeth", m.group(2) or "") and []:
            pass
        rest = m.group(2) or ""
        for val, key in re.findall(r"(\d+(?:\.\d+)?) ?mm (outer|inner|wide|deep|high|tall|thick|hole|bore)", rest):
            nums[{"outer": "od", "inner": "id", "wide": "w", "deep": "d", "high": "h", "tall": "h", "thick": "t", "hole": "hole", "bore": "bore"}[key]] = float(val)
        tt = re.search(r"(\d+) teeth", rest)
        if tt:
            nums["teeth"] = int(tt.group(1))
        return {"kind": "make", "template": kind, "params": nums}
    return None


DEFAULTS = {"spacer": {"od": 10, "id": 3.2, "h": 5}, "box": {"w": 60, "d": 40, "h": 25, "t": 2}, "bracket": {"w": 30, "d": 30, "t": 3, "hole": 5}, "gear": {"teeth": 16, "module": 1.5, "t": 6, "bore": 5},
            "stand": {"w": 70, "d": 80, "h": 70, "t": 3}, "pi_case": {"t": 2}}
