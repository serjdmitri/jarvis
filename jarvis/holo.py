"""3D explaining: scene specs, demo scenes, the scene library, drawings, and the voice commands for a model that is open.

The brain NEVER runs or supplies code for this. It returns JSON only; this module validates it (clamps every number, drops anything unknown, caps counts and string lengths,
breaks parent loops) and the page draws it with its own fixed set of shapes. No eval, no model-supplied JavaScript or shader text anywhere.

Scene spec  {"title", "unit", "parts": [{"id","label","desc","shape","pos","size","rot","color","parent","explode", "points"}]}   up to 40 parts
Draw spec   {"title", "elements": [{"t": arrow|circle|box|point|curve|text, ...}]}                                                 up to 60 elements, coordinates 0..1
Library     data/holo.json: saved scenes (spec + notes only) and the last few models; real models are saved as a source link and credit, never a copy.
Everything the brain says about a part is sent as plain text with the part's id, label and description (no screenshot unless you ask)."""
import json
import math
import re
import threading
import time

from . import config

LIB = config.DATA / "holo.json"
_lock = threading.RLock()
SHAPES = ("box", "sphere", "cylinder", "cone", "torus", "capsule", "plane", "ring", "extrude", "tube")
COLORS = ("cyan", "blue", "red", "orange", "yellow", "green", "white", "grey", "purple", "pink")
MAX_PARTS, MAX_EXTRUDE, MAX_TUBE, MAX_DRAW = 40, 40, 20, 60
MAX_JSON = 80_000


def _num(v, lo, hi, default=0.0):
    """A finite number clamped to [lo, hi]; anything else (None, text, NaN, infinity) becomes the default."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    v = float(v)
    if math.isnan(v) or math.isinf(v):
        return default
    return max(lo, min(hi, v))


def _vec(v, n, lo, hi, default):
    if not isinstance(v, (list, tuple)):
        return list(default)
    out = [_num(v[i] if i < len(v) else None, lo, hi, default[i]) for i in range(n)]
    return [round(x, 3) for x in out]


def _txt(v, n):
    """Plain text: control characters removed, whitespace collapsed, clipped. (The page puts it in with textContent, so markup could never run anyway.)"""
    if v is None:
        return ""
    s = re.sub(r"[\x00-\x1f\x7f<>`]", " ", str(v)[: n * 3])
    return re.sub(r"\s+", " ", s).strip()[:n]


def _id(v, i):
    s = re.sub(r"[^A-Za-z0-9_-]", "", str(v if v is not None else ""))[:24]
    return s or f"p{i}"


def validate_scene(spec):
    """-> a clean scene dict, or None when nothing drawable is left. Never raises."""
    try:
        if not isinstance(spec, dict) or len(json.dumps(spec, default=str)) > MAX_JSON:
            return None
        raw = spec.get("parts")
        if not isinstance(raw, list):
            return None
        parts, seen = [], set()
        for i, p in enumerate(raw[:MAX_PARTS]):
            if not isinstance(p, dict) or p.get("shape") not in SHAPES:
                continue
            pid = _id(p.get("id"), i)
            if pid in seen:
                pid = f"{pid}_{i}"[:24]
            seen.add(pid)
            shape = p["shape"]
            part = {"id": pid, "label": _txt(p.get("label"), 28) or pid, "desc": _txt(p.get("desc"), 160), "shape": shape,
                    "pos": _vec(p.get("pos"), 3, -100, 100, (0, 0, 0)), "size": _vec(p.get("size"), 3, 0.01, 100, (1, 1, 1)), "rot": _vec(p.get("rot"), 3, -360, 360, (0, 0, 0)),
                    "color": p.get("color") if p.get("color") in COLORS else "cyan", "parent": p.get("parent") if isinstance(p.get("parent"), str) else None,
                    "explode": _vec(p.get("explode"), 3, -20, 20, (0, 0, 0))}
            if shape == "extrude":
                pts = [_vec(q, 2, -100, 100, (0, 0)) for q in (p.get("points") or [])[:MAX_EXTRUDE] if isinstance(q, (list, tuple))]
                if len(pts) < 3:
                    continue
                part["points"] = pts
            elif shape == "tube":
                pts = [_vec(q, 3, -100, 100, (0, 0, 0)) for q in (p.get("points") or [])[:MAX_TUBE] if isinstance(q, (list, tuple))]
                if len(pts) < 2:
                    continue
                part["points"] = pts
            parts.append(part)
        if not parts:
            return None
        ids = {p["id"] for p in parts}
        by = {p["id"]: p for p in parts}
        for p in parts:                                                     # unknown parents and parent loops become "no parent"
            if p["parent"] not in ids or p["parent"] == p["id"]:
                p["parent"] = None
        for p in parts:
            seen_p, cur = {p["id"]}, p["parent"]
            while cur:
                if cur in seen_p:
                    p["parent"] = None
                    break
                seen_p.add(cur)
                cur = by[cur]["parent"]
        out = {"title": _txt(spec.get("title"), 60) or "3D model", "unit": _txt(spec.get("unit"), 8) or "cm", "parts": parts}
        if spec.get("built"):
            out["built"] = _txt(spec.get("built"), 40)
        return out
    except Exception:
        return None


def validate_draw(spec):
    """-> {"title","elements":[...]} or None. Elements: arrow, circle, box, point, curve, text; coordinates 0..1; optional "s" = which spoken sentence reveals it."""
    try:
        if not isinstance(spec, dict) or len(json.dumps(spec, default=str)) > MAX_JSON or not isinstance(spec.get("elements"), list):
            return None
        out = []
        for e in spec["elements"][:MAX_DRAW]:
            if not isinstance(e, dict):
                continue
            t = e.get("t")
            s = int(_num(e.get("s"), 0, 20, 0))
            c = e.get("color") if e.get("color") in COLORS else None
            if t == "arrow":
                el = {"t": t, "x1": _num(e.get("x1"), 0, 1, 0.2), "y1": _num(e.get("y1"), 0, 1, 0.5), "x2": _num(e.get("x2"), 0, 1, 0.8), "y2": _num(e.get("y2"), 0, 1, 0.5), "label": _txt(e.get("label"), 24)}
            elif t == "circle":
                el = {"t": t, "x": _num(e.get("x"), 0, 1, 0.5), "y": _num(e.get("y"), 0, 1, 0.5), "r": _num(e.get("r"), 0.01, 0.5, 0.1), "label": _txt(e.get("label"), 24)}
            elif t == "box":
                el = {"t": t, "x": _num(e.get("x"), 0, 1, 0.3), "y": _num(e.get("y"), 0, 1, 0.3), "w": _num(e.get("w"), 0.01, 1, 0.2), "h": _num(e.get("h"), 0.01, 1, 0.1), "label": _txt(e.get("label"), 24)}
            elif t == "point":
                el = {"t": t, "x": _num(e.get("x"), 0, 1, 0.5), "y": _num(e.get("y"), 0, 1, 0.5), "label": _txt(e.get("label"), 24)}
            elif t == "curve":
                pts = [_vec(q, 2, 0, 1, (0.5, 0.5)) for q in (e.get("pts") or [])[:12] if isinstance(q, (list, tuple))]
                if len(pts) < 2:
                    continue
                el = {"t": t, "pts": pts, "label": _txt(e.get("label"), 24)}
            elif t == "text":
                el = {"t": t, "x": _num(e.get("x"), 0, 1, 0.5), "y": _num(e.get("y"), 0, 1, 0.5), "text": _txt(e.get("text"), 60)}
                if not el["text"]:
                    continue
            else:
                continue
            el["s"] = s
            if c:
                el["color"] = c
            out.append(el)
        return {"title": _txt(spec.get("title"), 60) or "Drawing", "elements": out} if out else None
    except Exception:
        return None


def bullets(v, n=5):
    return [_txt(b, 120) for b in (v if isinstance(v, list) else [])[:n] if _txt(b, 120)]


# ----------------------------------------------------------------------------- demo scenes (original shapes, no brain needed)
def _p(id, label, desc, shape, pos=(0, 0, 0), size=(1, 1, 1), rot=(0, 0, 0), color="cyan", parent=None, explode=(0, 0, 0), **kw):
    d = {"id": id, "label": label, "desc": desc, "shape": shape, "pos": list(pos), "size": list(size), "rot": list(rot), "color": color, "parent": parent, "explode": list(explode)}
    d.update(kw)
    return d


def demo_engine():
    return {"title": "Four-stroke engine piston", "unit": "cm", "built": "demo", "parts": [
        _p("block", "Cylinder block", "The strong housing. The piston slides up and down inside its bore.", "cylinder", (0, 3.5, 0), (5.6, 7, 5.6), color="grey", explode=(0, 0, 0)),
        _p("head", "Cylinder head", "Seals the top of the cylinder and holds the valves and the spark plug.", "box", (0, 7.6, 0), (6, 1.2, 6), color="blue", explode=(0, 5, 0)),
        _p("plug", "Spark plug", "Makes the spark that lights the fuel and air mixture at the top of the stroke.", "cylinder", (0, 9.2, 0), (0.8, 2.2, 0.8), color="yellow", explode=(0, 8, 0)),
        _p("intake", "Intake valve", "Opens to let fresh air and fuel into the cylinder on the intake stroke.", "cone", (-1.3, 6.6, 0), (0.9, 1.6, 0.9), (180, 0, 0), "green", explode=(-3, 6, 0)),
        _p("exhaust", "Exhaust valve", "Opens to let burnt gas out on the exhaust stroke.", "cone", (1.3, 6.6, 0), (0.9, 1.6, 0.9), (180, 0, 0), "orange", explode=(3, 6, 0)),
        _p("piston", "Piston", "Pushed down by the burning gas; its up-and-down motion is what the engine turns into rotation.", "cylinder", (0, 3.0, 0), (5.2, 2.2, 5.2), color="red", explode=(0, 2, 0)),
        _p("rod", "Connecting rod", "Joins the piston to the crankshaft and turns straight motion into circular motion.", "box", (0, 0.6, 0), (0.8, 4.2, 0.8), color="white", explode=(0, -1, 0)),
        _p("crank", "Crankshaft", "Turns once for every two piston strokes and delivers the power to the wheels.", "cylinder", (0, -2.0, 0), (1.2, 7, 1.2), (0, 0, 90), "purple", explode=(0, -5, 0)),
        _p("counter", "Counterweight", "Balances the piston and rod so the engine does not shake itself apart.", "torus", (0, -2.0, 0), (3.2, 0.7, 3.2), (0, 90, 0), "pink", explode=(0, -7, 0))]}


def demo_earth_moon():
    return {"title": "Earth and Moon (not to scale)", "unit": "1000 km", "built": "demo", "parts": [
        _p("earth", "Earth", "Our planet, about 12,700 km wide. Here it is drawn bigger than true scale next to the Moon's distance.", "sphere", (0, 0, 0), (8, 8, 8), color="blue", explode=(-4, 0, 0)),
        _p("moon", "Moon", "About 3,500 km wide and about 384,000 km away: it takes 27 days to go around the Earth.", "sphere", (22, 0, 0), (2.2, 2.2, 2.2), color="grey", explode=(8, 0, 0)),
        _p("orbit", "Moon's orbit", "The Moon's path round the Earth is a slightly squashed circle.", "ring", (0, 0, 0), (44, 43.4, 1), (90, 0, 0), "white"),
        _p("axis", "Earth's axis", "The Earth spins around a line tilted about 23 degrees, which gives us seasons.", "cylinder", (0, 0, 0), (0.25, 13, 0.25), (0, 0, 23), "yellow", explode=(0, 5, 0))]}


def demo_atom():
    parts = [_p("nucleus", "Nucleus", "A tiny, dense centre made of protons and neutrons, holding almost all of the atom's mass.", "sphere", (0, 0, 0), (1.6, 1.6, 1.6), color="red", explode=(0, 0, 0)),
             _p("shell1", "First shell", "The inner electron shell holds up to two electrons.", "ring", (0, 0, 0), (6, 5.9, 1), (90, 0, 0), "blue"),
             _p("shell2", "Second shell", "The next shell holds up to eight electrons.", "ring", (0, 0, 0), (10, 9.9, 1), (70, 20, 0), "blue")]
    for i, (x, y, z) in enumerate([(3, 0, 0), (-3, 0, 0)]):
        parts.append(_p(f"e1{i}", "Electron", "A tiny negatively charged particle that orbits the nucleus.", "sphere", (x, y, z), (0.5, 0.5, 0.5), color="cyan", explode=(x, 3, z)))
    for i, (x, y, z) in enumerate([(5, 0, 0), (-4.7, 1.7, 0), (0, 3.5, 4.2), (0, -1.5, -4.6)]):
        parts.append(_p(f"e2{i}", "Electron", "A tiny negatively charged particle that orbits the nucleus.", "sphere", (x, y, z), (0.5, 0.5, 0.5), color="cyan", explode=(x, 3, z)))
    return {"title": "An atom (neon-like)", "unit": "relative", "built": "demo", "parts": parts}


def demo_water():
    return {"title": "Water molecule, H2O", "unit": "angstrom", "built": "demo", "parts": [
        _p("o", "Oxygen atom", "One oxygen atom: it pulls the shared electrons closer, so it is slightly negative.", "sphere", (0, 0, 0), (2.6, 2.6, 2.6), color="red", explode=(0, -2, 0)),
        _p("h1", "Hydrogen atom", "A hydrogen atom: slightly positive.", "sphere", (-2.2, 1.7, 0), (1.5, 1.5, 1.5), color="white", explode=(-3, 2, 0)),
        _p("h2", "Hydrogen atom", "A second hydrogen atom, about 104.5 degrees from the first around the oxygen.", "sphere", (2.2, 1.7, 0), (1.5, 1.5, 1.5), color="white", explode=(3, 2, 0)),
        _p("b1", "Bond", "A covalent bond: the atoms share a pair of electrons.", "cylinder", (-1.1, 0.85, 0), (0.35, 2.8, 0.35), (0, 0, 52), "grey", explode=(-1.5, 1, 0)),
        _p("b2", "Bond", "A covalent bond: the atoms share a pair of electrons.", "cylinder", (1.1, 0.85, 0), (0.35, 2.8, 0.35), (0, 0, -52), "grey", explode=(1.5, 1, 0))]}


def demo_house():
    return {"title": "A house with labelled rooms", "unit": "m", "built": "demo", "parts": [
        _p("floor", "Ground floor", "The slab the house stands on.", "box", (0, -0.15, 0), (12, 0.3, 8), color="grey", explode=(0, -3, 0)),
        _p("living", "Living room", "The big room at the front, for sitting and meeting people.", "plane", (-2.8, 0.02, 0), (6, 8, 1), color="blue"),
        _p("kitchen", "Kitchen", "Where food is cooked, next to the living room.", "plane", (3.0, 0.02, -2), (5.6, 4, 1), color="orange"),
        _p("bedroom", "Bedroom", "A quiet room at the back for sleeping.", "plane", (3.0, 0.02, 2), (5.6, 4, 1), color="purple"),
        _p("wallb", "Back wall", "The wall at the back of the house.", "box", (0, 1.3, -4), (12, 2.6, 0.25), color="cyan", explode=(0, 0, -4)),
        _p("wallf", "Front wall", "The front wall, with the door and windows.", "box", (0, 1.3, 4), (12, 2.6, 0.25), color="cyan", explode=(0, 0, 4)),
        _p("walll", "Left wall", "The left side wall.", "box", (-6, 1.3, 0), (0.25, 2.6, 8), color="cyan", explode=(-4, 0, 0)),
        _p("wallr", "Right wall", "The right side wall.", "box", (6, 1.3, 0), (0.25, 2.6, 8), color="cyan", explode=(4, 0, 0)),
        _p("divide", "Inner wall", "Separates the living room from the kitchen and bedroom.", "box", (0.2, 1.3, 0), (0.2, 2.6, 8), color="white", explode=(0, 2, 0)),
        _p("door", "Front door", "The way in.", "box", (-3.4, 1.0, 4.14), (1.1, 2.0, 0.1), color="yellow", explode=(0, 0, 6)),
        _p("roof", "Roof", "Keeps rain out. A simple pitched roof.", "extrude", (0, 2.6, 0), (1, 1, 8.6), color="red", explode=(0, 6, 0), points=[[-6.4, 0], [6.4, 0], [0, 3.2]])]}


DEMOS = {"engine": (demo_engine, r"engine|piston|four.?stroke|cylinder"), "earth_moon": (demo_earth_moon, r"earth|moon|solar system|planet"),
         "atom": (demo_atom, r"atom|electron|nucleus"), "water": (demo_water, r"water|h2o|molecule"), "house": (demo_house, r"house|home|rooms?|building")}


def demo_for(text):
    """The name of a built-in demo that matches a subject, or None."""
    low = (text or "").lower()
    for name, (_f, pat) in DEMOS.items():
        if re.search(r"\b(?:" + pat + r")", low):
            return name
    return None


def demo(name):
    return validate_scene(DEMOS[name][0]())


# ----------------------------------------------------------------------------- the library (scenes and notes only)
def _load():
    try:
        d = json.loads(LIB.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d):
    LIB.parent.mkdir(exist_ok=True)
    tmp = str(LIB) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    import os
    os.replace(tmp, LIB)


def remember(spec, source=None):
    """Add a model to the history (last 20), keyed by title. For a real model `source` holds the link and credit (never a copy of the file)."""
    with _lock:
        d = _load()
        hist = [h for h in d.get("history", []) if h.get("title") != spec["title"]]
        hist.append({"title": spec["title"], "t": time.time(), "spec": spec, "source": source})
        d["history"] = hist[-20:]
        _save(d)


def save_scene(name, spec, notes=None):
    name = _txt(name, 40)
    if not name or not spec:
        return False
    with _lock:
        d = _load()
        d.setdefault("scenes", {})[name.lower()] = {"name": name, "t": time.time(), "spec": spec, "notes": notes or []}
        _save(d)
    return True


def set_notes(title, notes):
    """The notes of the open model (also stored with a saved scene of that title, if one exists)."""
    clean = [{"pos": _vec(n.get("pos"), 3, -100, 100, (0, 0, 0)), "text": _txt(n.get("text"), 120)} for n in (notes or [])[:30] if isinstance(n, dict) and _txt(n.get("text"), 120)]
    with _lock:
        d = _load()
        for s in d.get("scenes", {}).values():
            if s["spec"]["title"] == title:
                s["notes"] = clean
        d.setdefault("notes", {})[title] = clean
        _save(d)
    return clean


def get_notes(title):
    return _load().get("notes", {}).get(title, [])


def list_scenes():
    d = _load()
    return [s["name"] for s in sorted(d.get("scenes", {}).values(), key=lambda s: -s["t"])]


def find_scene(words):
    d = _load()
    w = [x for x in re.findall(r"[a-z0-9]+", (words or "").lower()) if x not in ("the", "my", "scene", "model", "a")]
    best = None
    for s in d.get("scenes", {}).values():
        n = sum(x in s["name"].lower() or x in s["spec"]["title"].lower() for x in w)
        if w and n and (not best or n > best[0]):
            best = (n, s)
    return best[1] if best else None


def last_model():
    h = _load().get("history", [])
    return h[-1] if h else None


def history():
    return list(reversed(_load().get("history", [])))


# ----------------------------------------------------------------------------- cards
def scene_card(spec, source=None, notes=None, bullets_=None, more=None, tour=None):
    c = {"type": "scene3d", "title": spec["title"], "spec": spec, "source": source or {"kind": "basic", "line": "Built from basic shapes, not a real model." if spec.get("built") != "demo" else "Demo model built from basic shapes (ships with Jarvis, works offline)."},
         "notes": notes if notes is not None else get_notes(spec["title"]), "bullets": bullets_ or [], "more": _txt(more, 800) if more else ""}
    return c


# ----------------------------------------------------------------------------- voice phrases
THREED = re.compile(r"\b(?:in|as a|into a)? ?(?:3 ?d|three ?d|three dimensional|3 dimensional)\b|\b(?:hologram|holographic)\b", re.I)


def parse_open(text):
    """-> {"subject"} for "show me an engine in 3D", "3D model of the solar system", "build me a model of a house", "hologram of a heart"; else None."""
    t = re.sub(r"\s+", " ", (text or "").strip(" .?!"))
    if re.search(r"\b(?:on the map|send|text me|to my (?:mac|phone))\b", t, re.I):
        return None
    m = re.search(r"^(?:please )?(?:how (?:does|do|is|are|did) |what is |what are |explain |tell me about )(?:a |an |the )?(.+?)(?: work| works| made| built)?,? (?:and |then |please )?(?:show|build|make|give) (?:it|me|that|one)(?: to me)?(?: in| as a| as)? (?:3 ?d|three ?d|a hologram|a 3 ?d model)$", t, re.I)
    if m:
        return {"subject": m.group(1).strip(" ,.")[:80]}
    m = (re.search(r"^(?:please )?(?:a |an )?(?:3 ?d|three ?d) (?:model|hologram) (?:of |for )(?:a |an |the )?(.+)$", t, re.I)
         or re.search(r"^(?:please )?(?:show|build|make|draw|give|create|display|bring up|open)(?: me)?(?: a| an| the)? (?:3 ?d |three ?d |holographic )?(?:model|hologram|hologram model|3 ?d model|3 ?d)(?: of| for)? (?:a |an |the )?(.+?)(?: in 3 ?d)?$", t, re.I)
         or re.search(r"^(?:please )?(?:show|build|make|display|give|bring up)(?: me)?(?: a| an| the)? (.+?) (?:in|as a|as) (?:3 ?d|three ?d|a hologram|a 3 ?d model)$", t, re.I)
         or re.search(r"^(?:please )?(?:build|make|create) (?:me )?a (?:3 ?d )?model of (?:a |an |the )?(.+)$", t, re.I))
    if m:
        s = re.sub(r"\b(?:please|in 3 ?d)\b", "", m.group(1), flags=re.I).strip(" ,.")
        return {"subject": s[:80]} if s else None
    return None


CTL = [("explode", r"(?:please )?(?:explode(?: it| the model)?|pull(?: it)? apart|take it apart)"), ("implode", r"(?:please )?(?:put it back together|put it back|assemble(?: it)?|un-?explode(?: it)?)"),
       ("spin_on", r"(?:please )?(?:spin(?: it)?|start spinning|rotate(?: it)?)"), ("spin_off", r"(?:please )?(?:stop spinning|stop rotating|don'?t spin|hold still)"),
       ("wire", r"(?:please )?(?:wireframe|show (?:the )?wireframe)"), ("solid", r"(?:please )?(?:solid|show (?:it )?solid|solid view)"),
       ("all", r"(?:please )?(?:show all|show everything|show all (?:the )?parts)"), ("next", r"(?:please )?next part"), ("prev", r"(?:please )?(?:previous|last) part"),
       ("reset", r"(?:please )?reset (?:the )?(?:view|model|camera)"), ("close", r"(?:please )?(?:close|hide|remove) (?:the )?(?:model|3 ?d(?: model| card)?|hologram)")]


def parse_control(text, parts):
    """Commands for the OPEN model: select/hide/isolate a part by name, next/previous part, explode, spin, wireframe, close. -> {"op", "id"?} or None. `parts` is the open spec's part list."""
    low = re.sub(r"\s+", " ", (text or "").lower().strip(" .?!"))
    for op, pat in CTL:
        if re.fullmatch(pat, low):
            return {"op": op}
    m = re.fullmatch(r"(?:please )?(select|hide|isolate|show|highlight|focus on|zoom (?:in )?(?:on|to)|where is|point (?:to|at))(?: me)? (?:the |a |an )?(.+)", low)
    if m:
        want = re.findall(r"[a-z0-9]+", m.group(2))
        best = None
        for p in parts:
            lab = re.findall(r"[a-z0-9]+", p["label"].lower()) + re.findall(r"[a-z0-9]+", p["id"].lower())
            n = sum(w in lab or any(l.startswith(w) or w.startswith(l) for l in lab if len(l) > 3 and len(w) > 3) for w in want if w not in ("part", "piece"))
            if n and (not best or n > best[0]):
                best = (n, p)
        if best:
            op = {"select": "select", "highlight": "select", "focus on": "select", "where is": "select", "hide": "hide", "isolate": "isolate", "show": "select"}.get(m.group(1), "select")
            if m.group(1) == "show" and not best:
                return None
            return {"op": op, "id": best[1]["id"]}
    return None


def parse_library(text):
    low = re.sub(r"\s+", " ", (text or "").lower().strip(" .?!"))
    m = re.fullmatch(r"(?:please )?save (?:this|it|the model|this model|the scene)(?: as| to| under)? (?:the )?(?!(?:my |the )?(?:mac|macbook|phone|iphone|downloads|computer)\b)(.+?)(?: scene)?", low)
    if m and not re.fullmatch(r"(?:this|it)", m.group(1)) and not re.match(r"(?:to |onto |on |in )?(?:my |the )?(?:mac|macbook|phone|iphone|downloads|computer|laptop)\b", m.group(1)):
        return {"op": "save", "name": m.group(1)}                         # "save this to my Mac" is a send, not a scene called "to my mac"
    if re.fullmatch(r"(?:please )?(?:list|show|what are)(?: me)?(?: all)? (?:of )?(?:my )?(?:saved )?(?:scenes|models|3 ?d (?:models|scenes|history)|holograms)(?: list)?|what (?:scenes|models) (?:do i have|have i saved)", low):
        return {"op": "list"}
    if re.fullmatch(r"(?:please )?(?:show|open|bring back|load)(?: me)? (?:my )?(?:last|previous|latest) (?:model|3 ?d(?: model)?|hologram|scene)", low):
        return {"op": "last"}
    m = re.fullmatch(r"(?:please )?(?:show|open|bring back|load|recall)(?: me)? (?:the |my )?(.+?) scene", low)
    if m:
        return {"op": "scene", "name": m.group(1)}
    if re.fullmatch(r"(?:please )?(?:turn |switch )?(on|off) (?:the )?auto ?3 ?d|(?:please )?(?:turn on|turn off|enable|disable) auto(?:matic)? 3 ?d(?: models| cards)?", low):
        return {"op": "auto3d", "on": "on" in low.split() or "enable" in low.split()}
    m = re.fullmatch(r"(?:please )?compare (?:a |an |the )?(.+?) (?:and|with|to|versus|vs\.?|against) (?:a |an |the )?(.+?)(?: (?:in|side by side|in 3 ?d))*", low)
    if m:
        return {"op": "compare", "a": m.group(1)[:60], "b": m.group(2)[:60]}
    if re.fullmatch(r"(?:please )?(?:explain it|walk me through it|take me through it|give me a tour|tour)(?: of (?:it|the model))?(?: please)?", low):
        return {"op": "tour"}
    return None


def parse_follow(text):
    """A question about the selected part ("what does this do", "why is it that shape", "how big is it", "what is it made of") -> True."""
    low = re.sub(r"\s+", " ", (text or "").lower().strip(" .?!"))
    return bool(re.fullmatch(r"(?:so )?(?:what|why|how|which|where|when|who)\b.*\b(?:this|it|that|the part|this part|that part)\b.*|(?:tell me|explain|describe)(?: more)?(?: about)? (?:this|it|that|this part|that part)", low))


# ----------------------------------------------------------------------------- brain prompts and answers
def scene_prompt(subject):
    return ("[SHOW 3D. Build a simple labelled 3D model of: " + subject[:80] + ". Reply with ONE short spoken sentence, then ONE JSON line "
            "{\"intent\":{\"name\":\"show_3d\",\"subject\":\"...\",\"scene\":{\"title\":\"...\",\"unit\":\"cm\",\"parts\":[{\"id\":\"a\",\"label\":\"SHORT\",\"desc\":\"one sentence\",\"shape\":\"box|sphere|cylinder|cone|torus|capsule|plane|ring|extrude|tube\","
            "\"pos\":[x,y,z],\"size\":[x,y,z],\"rot\":[degrees],\"color\":\"cyan|blue|red|orange|yellow|green|white|grey|purple|pink\",\"parent\":\"id or omit\",\"explode\":[x,y,z]}]}}}. "
            "At most 30 parts, units in a sane range (positions within -50..50, sizes 0.1..40), y is up, shapes centred on pos, explode = which way the part moves when pulled apart. "
            "Real proportions where you know them. No code, no URLs.]")


def tour_prompt(spec):
    parts = "; ".join(f"{p['id']} = {p['label']}" for p in spec["parts"][:40])
    return ("[3D TOUR of the model '" + spec["title"] + "'. Parts: " + parts + ". Reply with ONLY one JSON object {\"tour\":[{\"id\":\"part id\",\"say\":\"ONE short spoken sentence about that part\"}]} "
            "with 4 to 8 steps in a sensible order. Use only the ids listed.]")


def parse_tour(reply, spec):
    m = re.search(r"\{.*\}", reply or "", re.S)
    if not m:
        return []
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return []
    ids = {p["id"] for p in spec["parts"]}
    out = []
    for s in (d.get("tour") if isinstance(d, dict) else None) or []:
        if isinstance(s, dict) and s.get("id") in ids and _txt(s.get("say"), 200):
            out.append({"id": s["id"], "say": _txt(s["say"], 200)})
    return out[:10]


def part_prompt(spec, part, question):
    return (f"[ABOUT A 3D MODEL. Model: {spec['title']}. The user selected the part {part['label']!r} ({part['desc']!r}). Their question: {question[:300]!r}. "
            "Answer in at most 2 short spoken sentences, then on a new line a JSON object {\"bullets\":[\"short point\", ...up to 4]}. No markdown.]")


# ----------------------------------------------------------------------------- running things
HELP = "I can build an engine, the Earth and Moon, an atom, a water molecule or a house offline, and anything else with the brain."


def run_intent(intent):
    """The brain's show_3d intent -> a result dict with the 3D card. Real model first is handled by models3d (only with your approval); otherwise the brain's scene spec; otherwise a demo."""
    spec = validate_scene(intent.get("scene"))
    if spec:
        spec["built"] = ""
    else:
        name = demo_for(intent.get("subject", ""))
        spec = demo(name) if name else None
    if not spec:
        return {"speech": "I couldn't build a model of that. " + HELP}
    remember(spec)
    return {"speech": "", "cards": [scene_card(spec)]}


def demo_result(subject):
    name = demo_for(subject)
    if not name:
        return None
    spec = demo(name)
    remember(spec)
    return {"speech": f"Here is {spec['title'].lower()}. Tap a part to ask about it.", "cards": [scene_card(spec)]}


def command(text, online=lambda: True):
    """The local (no brain) 3D phrases: demos by name, the library (save, list, recall), auto 3D, compare and tour are handled in core. -> a skill-style dict or None."""
    from . import settings
    lib = parse_library(text)
    if lib:
        op = lib["op"]
        if op == "save":
            return {"speech": f"Saving this as {lib['name']}.", "holo_save": lib["name"]}
        if op == "list":
            names = list_scenes()
            hist = [h["title"] for h in history()[:6]]
            items = [f"SCENE  {n}" for n in names] + [f"RECENT  {t}" for t in hist]
            return {"speech": (f"You have {len(names)} saved scene{'s' if len(names) != 1 else ''}." if names else "You have no saved scenes yet.") + (" Your last models are listed." if hist else ""),
                    "cards": [{"type": "list", "title": "3D scenes and models", "items": items or ["Nothing yet. Ask: show me an atom in 3D."]}]}
        if op == "last":
            h = last_model()
            return {"speech": "Here is your last model.", "cards": [scene_card(h["spec"])]} if h else {"speech": "You haven't opened a model yet."}
        if op == "scene":
            sc = find_scene(lib["name"])
            return {"speech": f"Here is {sc['name']}.", "cards": [scene_card(sc["spec"], notes=sc.get("notes"))]} if sc else {"speech": f"I don't have a scene called {lib['name']}."}
        if op == "auto3d":
            settings.update(auto_3d=bool(lib["on"]))
            return {"speech": "Automatic 3D is on. It uses more power." if lib["on"] else "Automatic 3D is off.", "set_pref": {"auto_3d": bool(lib["on"])}}
        if op in ("compare", "tour"):
            return {"speech": "Comparing them." if op == "compare" else "Okay, let me walk you through it.", "holo_" + op: lib}
    o = parse_open(text)
    if o:
        simple = re.fullmatch(r"(?:a |an |the )?(?:(?:four[- ]?stroke )?engine|piston|earth and (?:the )?moon|earth|moon|atom|water(?: molecule)?|molecule|house|home)", o["subject"].lower().strip())
        if simple or not online():
            r = demo_result(o["subject"])
            if r:
                return r
        if not online():
            return {"speech": "I'm offline, so I can only build the built-in models: " + HELP}
        return {"speech": "Building a model of " + o["subject"] + ".", "holo_build": o["subject"]}                          # the brain builds it (core.holo_build)
    return None
