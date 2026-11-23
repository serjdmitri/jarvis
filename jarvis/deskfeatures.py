"""Desk helpers: 1 desk memory (where did I put X: from what the camera saw), 2 build mode (one step at a time, checked on the camera), 3 datasheet pinouts, 5 voice project log,
6 shopping from the camera, 7 focus mode, 10 review of phrases Jarvis did not understand. Local state in data/; the camera is only used when you ask; the model never runs anything."""
import json
import re
import time
from collections import Counter

from . import config, settings

SIGHT = config.DATA / "sightings.jsonl"
SHEETS = config.DATA / "datasheets.json"
BUILD = {"name": "", "steps": [], "i": 0}


# ---- 1 desk memory
def record_sightings(boxes, source="camera"):
    """Called after every look: what was seen, when. Names only (people are just 'person'); no pictures kept here."""
    names = sorted({b["name"].lower() for b in boxes if b.get("name") and b["name"].lower() not in ("person",)})
    if not names:
        return
    config.DATA.mkdir(exist_ok=True)
    with open(SIGHT, "a") as f:
        for b in boxes:
            n = b.get("name", "").lower()
            if n and n != "person":
                f.write(json.dumps({"t": int(time.time()), "name": n, "x": b["x"], "y": b["y"], "w": b["w"], "h": b["h"], "src": source}) + "\n")
    try:
        lines = SIGHT.read_text().splitlines()
        if len(lines) > 2000:
            SIGHT.write_text("\n".join(lines[-1200:]) + "\n")
    except OSError:
        pass


def _where(x, y, w, h):
    cx, cy = x + w / 2, y + h / 2
    return ("top" if cy < .34 else "bottom" if cy > .66 else "middle") + "-" + ("left" if cx < .34 else "right" if cx > .66 else "centre")


def where_is(name):
    key = re.sub(r"s$", "", name.lower().strip())
    try:
        rows = [json.loads(l) for l in SIGHT.read_text().splitlines() if l.strip()]
    except (OSError, ValueError):
        rows = []
    hits = [r for r in rows if key and (key in r["name"] or re.sub(r"s$", "", r["name"]) in key)]
    if not hits:
        return None
    r = hits[-1]
    ago = int(time.time() - r["t"])
    when = "just now" if ago < 90 else f"{ago // 60} minutes ago" if ago < 5400 else f"{ago // 3600} hours ago" if ago < 172800 else f"{ago // 86400} days ago"
    return {"name": r["name"], "where": _where(r["x"], r["y"], r["w"], r["h"]), "when": when, "box": r}


# ---- 2 build mode
def build_start(name, steps):
    BUILD.update(name=name, steps=list(steps), i=0)
    return build_say()


def build_say():
    if not BUILD["steps"]:
        return "There is no build running. Say: start build mode for the LED."
    i = BUILD["i"]
    if i >= len(BUILD["steps"]):
        return f"The {BUILD['name']} build is finished. Verify before powering."
    return f"Step {i + 1} of {len(BUILD['steps'])}: {BUILD['steps'][i]}"


def build_next():
    if BUILD["steps"] and BUILD["i"] < len(BUILD["steps"]):
        BUILD["i"] += 1
    return build_say()


def build_check_prompt():
    return (f"[BUILD CHECK: the user is building '{BUILD['name']}'. Current step: {BUILD['steps'][BUILD['i']]}. Look at the picture and say in one short sentence whether that step looks DONE, "
            "NOT DONE or you CANNOT TELL (never guess). Start the sentence with exactly DONE, NOT DONE or CANNOT TELL. Never say a circuit is safe to power.]\n")


def steps_for_led(gpio=17):
    return [f"Place the LED on the breadboard, long leg (the anode) in its own row.", "Put a 330 ohm resistor from the LED's long-leg row to an empty row.",
            f"Run a jumper from that resistor row to GPIO {gpio}.", "Run a jumper from the LED's short leg row to a ground pin.", "Check every leg is in a different row. Verify before powering."]


# ---- 3 datasheet
def sheet_get(part):
    try:
        return json.loads(SHEETS.read_text()).get(part.lower())
    except (OSError, ValueError):
        return None


def sheet_save(part, pins):
    try:
        d = json.loads(SHEETS.read_text())
    except (OSError, ValueError):
        d = {}
    d[part.lower()] = {"pins": pins, "t": int(time.time())}
    config.DATA.mkdir(exist_ok=True)
    SHEETS.write_text(json.dumps(d))


def pinout_prompt(part):
    return (f"[PINOUT: give the pinout of {part} from your knowledge. Reply with ONLY one JSON line {{\"pins\":[\"1: NAME - what it does\", ...]}} (at most 40 entries). If you are not sure of this exact part or package, "
            "reply {\"pins\":[],\"note\":\"not sure\"} - never invent pins. No other text.]")


def parse_pins(reply):
    m = re.search(r"\{.*\}", reply or "", re.S)
    try:
        j = json.loads(m.group(0)) if m else {}
    except ValueError:
        return []
    return [str(p)[:90] for p in (j.get("pins") or [])][:40]


# ---- 5 voice project log
def log_add(text):
    from . import vault
    return vault.remember(text, kind="daily", source="voice log")


# ---- 7 focus mode
def focus(on):
    settings.update(focus_mode=bool(on))
    return "Focus mode on. I will stay quiet except for timers and alarms." if on else "Focus mode off."


# ---- 10 review of what was not understood
def review():
    from . import unknown
    rows = unknown.last(200)
    if not rows:
        return None, "I have nothing in the not-understood list."
    key = lambda t: " ".join(re.findall(r"[a-z']+", t.lower())[:1])
    groups = Counter(key(r["text"]) for r in rows)
    items = []
    for k, n in groups.most_common(8):
        ex = next(r["text"] for r in rows if key(r["text"]) == k)
        items.append(f"{n}x  \"{k} ...\"   e.g. {ex[:60]}   -> add a rule for phrases starting this way")
    return {"type": "list", "title": "NOT UNDERSTOOD: REVIEW", "items": items}, f"{len(rows)} phrases in {len(groups)} groups. The biggest is \"{groups.most_common(1)[0][0]}\"."


def parse_command(text):
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    m = re.fullmatch(r"(?:please )?(?:where (?:did i (?:put|leave)|is|are|'?s)|have you seen|did you see) (?:my |the )?(.{2,40})", low)
    if m and not re.search(r"\b(?:i am|we are|the nearest|near me|closest|airport|starbucks)\b", low) and not re.match(r"(?:am i|you)\b", m.group(1)):
        return {"kind": "where", "name": m.group(1)}
    m = re.fullmatch(r"(?:please )?(?:start|begin) (?:build mode|a build|building)(?: (?:for|of))?(?: (?:the|a|an))? ?(.*)", low)
    if m:
        return {"kind": "build_start", "name": m.group(1) or "LED"}
    if re.fullmatch(r"(?:please )?(?:next step|what'?s next|go on|continue the build|i did it|done with this step|step done)", low):
        return {"kind": "build_next"} if BUILD["steps"] else None
    if re.fullmatch(r"(?:please )?(?:repeat(?: the)? step|what(?:'s| is) the (?:current )?step)", low):
        return {"kind": "build_say"} if BUILD["steps"] else None
    if re.fullmatch(r"(?:please )?(?:check (?:my )?(?:step|build|this step)|did i do (?:it|this step) (?:right|correctly))", low) and BUILD["steps"]:
        return {"kind": "build_check"}
    if re.fullmatch(r"(?:please )?(?:stop|end|cancel) (?:the )?build(?: mode)?", low):
        return {"kind": "build_stop"}
    if re.fullmatch(r"(?:please )?(?:show |what is |what's )?(?:the )?(?:pinout|pins|datasheet) (?:of |for )?(?:this|that|it)(?: (?:chip|part|board))?", low) or low in ("pinout", "show the pinout"):
        return {"kind": "pinout_look"}
    m = re.fullmatch(r"(?:please )?(?:show |what is |what's |give me )?(?:the )?(?:pinout|pin ?out|pins|datasheet)(?: (?:of|for))? (?:the )?(.{2,40})", low)
    if m:
        return {"kind": "pinout", "part": m.group(1)}
    m = re.fullmatch(r"(?:please )?(?:log|write down|put in my log|add to my log|project log)[: ]+(?:that )?(.+)", low)
    if m:
        return {"kind": "log", "text": m.group(1)}
    if re.fullmatch(r"(?:please )?(?:show|read)(?: me)? (?:my |today'?s |the )?(?:project )?log(?: for today)?|what did i do today", low):
        return {"kind": "log_show"}
    if re.fullmatch(r"(?:please )?add (?:this|that|it|what you see|this part|this item) to (?:my |the )?(?:shopping|buy|buying|parts)(?: list)?", low):
        return {"kind": "shop_look"}
    m = re.fullmatch(r"(?:please )?(?:turn |switch )?(?:on |off )?focus mode(?: (on|off))?|(?:please )?(?:start|stop|end) focus mode", low)
    if m:
        off = "off" in low or low.startswith(("stop", "end"))
        return {"kind": "focus", "on": not off}
    if re.fullmatch(r"(?:please )?(?:review|go through) (?:what|the phrases) (?:you )?(?:did not|didn't|couldn't) understand|(?:please )?review (?:the )?(?:unknown|not understood)(?: phrases)?", low):
        return {"kind": "review"}
    return None
