"""Projects, parts and shopping LISTS (never buying). Your parts bin ("I have a Raspberry Pi 4 and two servos"), saved projects with their parts, steps done and notes, and a PARTS LIST
the brain drafts for a project ("what do I need for a weather station") that is trimmed by what you already have. Everything is a list: nothing is ever bought, ordered, paid or added to a cart.
Prices: only what a fetched store page really shows (see product.py); a part with no verified price says "price not found", never a guess. Data in data/projects.json (mode 600, outside git).
Interests noted: Raspberry Pi, robotics, electronics repair; no 3D printer yet (one at school), so ideas may include printed parts."""
import json
import os
import re
import time

from . import config, webjson

FILE = config.DATA / "projects.json"


def _load():
    try:
        d = json.loads(FILE.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d):
    config.DATA.mkdir(parents=True, exist_ok=True)
    tmp = str(FILE) + ".tmp"                                            # written aside and swapped in: a crash can never leave a half-empty file
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f)
    os.replace(tmp, FILE)


NUMW = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "ten": 10}


def bin_items():
    return _load().get("bin", [])


def add_to_bin(text):
    """"a Raspberry Pi 4 and two servos" -> the items added. -> [{"name","qty"}]"""
    added = []
    d = _load()
    for part in re.split(r",| and ", text.lower()):
        part = part.strip(" .")
        m = re.fullmatch(r"(?:(\d+|a|an|one|two|three|four|five|six|ten) )?(.{2,60})", part)
        if not m or re.fullmatch(r"(?:some|the|my|more|few|couple)", m.group(2)):
            continue
        qty = int(m.group(1)) if m.group(1) and m.group(1).isdigit() else NUMW.get(m.group(1) or "a", 1)
        name = re.sub(r"^(?:my|the|some) ", "", m.group(2)).strip()
        name = re.sub(r"s$", "", name) if qty > 1 and name.endswith("s") and not name.endswith("ss") else name
        d.setdefault("bin", [])
        hit = next((x for x in d["bin"] if x["name"] == name), None)
        if hit:
            hit["qty"] += qty
        else:
            d["bin"].append({"name": name[:60], "qty": qty})
        added.append({"name": name, "qty": qty})
    _save(d)
    return added


def remove_from_bin(name):
    d = _load()
    n0 = len(d.get("bin", []))
    w = name.lower().strip()
    d["bin"] = [x for x in d.get("bin", []) if not re.search(r"\b" + re.escape(w) + r"\b", x["name"])]      # whole words only: "led" does not remove "oled display"
    _save(d)
    return n0 - len(d["bin"])


def clean_parts(parts):
    out = []
    for p in parts if isinstance(parts, list) else []:
        if isinstance(p, dict) and isinstance(p.get("name"), str) and p["name"].strip():
            try:
                qty = max(1, min(99, int(p.get("qty") or 1)))
            except (TypeError, ValueError):
                qty = 1
            out.append({"name": re.sub(r"\s+", " ", p["name"]).strip()[:70], "qty": qty, "why": re.sub(r"\s+", " ", str(p.get("why") or "")).strip()[:100]})
    return out[:25]


def draft_parts_list(topic):
    """-> {"parts": [...], "tools": [...], "steps": [...], "safety": str} from the brain, clamped. {} when it fails."""
    prompt = (f"A student who likes Raspberry Pi, robotics and electronics repair wants to build: {topic[:140]}. Prefer common, cheap parts. They have no 3D printer at home (one at school), so printed parts may be suggested "
              'separately. Answer with ONE JSON object: {"parts":[{"name":...,"qty":n,"why":"short reason"}], "tools":[names], "steps":[up to 8 short steps], "safety":"one short plain warning"}. '
              "No prices. At most 15 parts.")
    r = webjson.ask_json(prompt)
    if not isinstance(r, dict):
        return {}
    return {"parts": clean_parts(r.get("parts")), "tools": [str(x)[:40] for x in (r.get("tools") or [])[:10]], "steps": [str(x)[:140] for x in (r.get("steps") or [])[:8]], "safety": str(r.get("safety") or "")[:200]}


def split_have(parts):
    """-> (have, need): parts you already own (by name words) and the buy list."""
    have_names = [b["name"] for b in bin_items()]
    have, need = [], []
    for p in parts:
        words = set(re.findall(r"[a-z0-9]+", p["name"].lower())) - {"the", "a", "and", "with", "for", "kit"}
        owned = any(words and words <= set(re.findall(r"[a-z0-9]+", h)) or (h in p["name"].lower()) for h in have_names)
        (have if owned else need).append(p)
    return have, need


def parts_card(topic, plan):
    have, need = split_have(plan.get("parts", []))
    items = [f"BUY  {p['qty']}× {p['name']}" + (f"  ({p['why']})" if p["why"] else "") + "   price not found" for p in need] + [f"HAVE {p['qty']}× {p['name']}" for p in have]
    if plan.get("tools"):
        items.append("TOOLS: " + ", ".join(plan["tools"]))
    for i, s in enumerate(plan.get("steps", []), 1):
        items.append(f"{i}. {s}")
    if plan.get("safety"):
        items.append("SAFETY: " + plan["safety"])
    return {"type": "list", "gid": "parts", "wide": 400, "small": True, "title": "PARTS · " + topic.upper()[:28], "items": items or ["No parts found."], "buttons": []}


def save_project(name, plan, done=None, notes=""):
    d = _load()
    d.setdefault("projects", {})[name.lower()[:60]] = {"name": name[:60], "plan": plan, "done": done or [], "notes": notes[:400], "t": time.time()}
    _save(d)


def get_project(name):
    ps = _load().get("projects", {})
    k = next((k for k in ps if name.lower().strip() in k or k in name.lower()), None)
    return ps.get(k) if k else None


def list_projects():
    return sorted(_load().get("projects", {}).values(), key=lambda p: -p["t"])


def forget_all():
    try:
        FILE.unlink()
    except OSError:
        pass


def parse_command(text):
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    m = re.fullmatch(r"(?:please )?(?:i have|i've got|i own|i got) (.+)", low)
    if m and not re.search(r"\b(?:a question|to go|to leave|no idea|to say)\b", m.group(1)) and re.search(r"\b(?:raspberry|arduino|servo|sensor|breadboard|resistors?|leds?|motors?|wires?|batter(?:y|ies)|boards?|esp\d*|multimeter|solder(?:ing)?|cables?|displays?|cameras?|relays?|jumpers?|capacitors?|transistors?)\b", m.group(1)):
        return {"kind": "have", "text": m.group(1)}
    m = re.fullmatch(r"(?:please )?add (.+?) to my (?:parts|parts bin|bin|inventory)", low)
    if m:
        return {"kind": "have", "text": m.group(1)}
    if re.fullmatch(r"(?:please )?(?:what|which) parts do i have|(?:please )?(?:show|list|open) my (?:parts|parts bin|inventory)", low):
        return {"kind": "bin"}
    m = re.fullmatch(r"(?:please )?(?:remove|delete|take) (.+?) (?:from|off) my (?:parts|parts bin|bin|inventory)", low)
    if m:
        return {"kind": "unbin", "name": m.group(1)}
    m = re.fullmatch(r"(?:please )?(?:make|build|give me|create|write|show me) (?:me )?(?:a |the )?(?:parts list|shopping list|buy list|bill of materials)(?: for| to build| of)? (?:a |an |the )?(.+)|(?:please )?what do i need (?:to build|for|to make) (?:a |an |the )?(.+)|(?:please )?i want to build (?:a |an |the )?(.+)", low)
    if m:
        return {"kind": "plan", "topic": next(g for g in m.groups() if g)}
    if re.fullmatch(r"(?:please )?(?:save|keep|store) (?:this|that|the) project(?: as (.+))?", low):
        return {"kind": "save", "name": (re.search(r"as (.+)$", low) or [None, ""])[1]}
    if re.fullmatch(r"(?:please )?(?:what|which) projects do i have|(?:please )?(?:show|list|open) my projects", low):
        return {"kind": "list"}
    m = re.fullmatch(r"(?:please )?(?:continue|open|resume|show) my (.+?)(?: project)?", low)
    if m and m.group(1) not in ("projects", "parts", "list", "notes", "timers", "music", "day", "today") and get_project(m.group(1)):      # only a project you really saved ("open my email" is not one)
        return {"kind": "open", "name": m.group(1)}
    return None
