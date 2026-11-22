"""Scenes: "movie mode", "focus mode", "goodnight" run a short saved list of ALLOWED actions. Nothing else can be in a scene:
  volume {set 0-100}   brightness {set 1-100}   mute {on}   hide_ui {on}   open {url | app} (validated like any other open request)   timer {seconds, label}   say {text}   close_all
Each step is checked here and executed by fixed code (wpctl / brightnessctl with fixed arguments); the model never supplies a command.
You can add a scene by voice: "make a scene called study that sets volume to 30, brightness to 60 and opens YouTube". Scenes live in data/scenes.json."""
import json
import re
import subprocess

from . import config, opener

PATH = config.DATA / "scenes.json"
DEFAULTS = {
    "movie": [{"do": "volume", "set": 60}, {"do": "brightness", "set": 30}, {"do": "hide_ui", "on": True}],
    "focus": [{"do": "volume", "set": 20}, {"do": "brightness", "set": 75}, {"do": "hide_ui", "on": True}, {"do": "timer", "seconds": 1500, "label": "focus time is up"}],
    "goodnight": [{"do": "close_all"}, {"do": "volume", "set": 10}, {"do": "brightness", "set": 5}, {"do": "hide_ui", "on": True}, {"do": "say", "text": "Goodnight."}],
}
ALLOWED = {"volume", "brightness", "mute", "hide_ui", "open", "timer", "say", "close_all"}
SNAP = {"brightness": None, "volume": None}              # what things were before the last scene, for "back to normal"


def load():
    try:
        user = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        user = {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in user.items() if isinstance(v, list)})
    return out


def save_user(name, steps):
    try:
        user = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        user = {}
    user[name] = steps
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(json.dumps(user, indent=1), encoding="utf-8")


def delete_user(name):
    try:
        user = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if name in user:
        user.pop(name)
        PATH.write_text(json.dumps(user, indent=1), encoding="utf-8")
        return True
    return False


def validate_step(st):
    """-> a clean step or None. Anything outside the allowlist or out of range is refused."""
    if not isinstance(st, dict) or st.get("do") not in ALLOWED:
        return None
    d = st["do"]
    try:
        if d == "volume":
            return {"do": d, "set": max(0, min(100, int(st["set"])))}
        if d == "brightness":
            return {"do": d, "set": max(1, min(100, int(st["set"])))}
        if d in ("mute", "hide_ui"):
            return {"do": d, "on": bool(st.get("on", True))}
        if d == "timer":
            return {"do": d, "seconds": max(5, min(8 * 3600, int(st["seconds"]))), "label": str(st.get("label", ""))[:80]}
        if d == "say":
            return {"do": d, "text": str(st["text"])[:120]}
        if d == "close_all":
            return {"do": d}
        if d == "open":
            act = opener.validate({k: st[k] for k in ("url", "app") if k in st})
            return {"do": d, **{k: st[k] for k in ("url", "app") if k in st}} if act else None
    except (KeyError, TypeError, ValueError):
        return None
    return None


def brightness_get():
    try:
        cur = int(subprocess.run(["brightnessctl", "get"], capture_output=True, text=True, timeout=3).stdout)
        mx = int(subprocess.run(["brightnessctl", "max"], capture_output=True, text=True, timeout=3).stdout)
        return round(100 * cur / mx)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def brightness_set(pct):
    subprocess.run(["brightnessctl", "set", f"{max(1, min(100, int(pct)))}%"], capture_output=True, timeout=4)


def volume_get():
    try:
        out = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True, timeout=3).stdout
        return round(float(out.split()[1]) * 100)
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


# ----------------------------------------------------------------------------- voice
_NUM = r"(\d{1,3})"


def parse_clause(c):
    c = c.strip(" .,").lower()
    m = re.fullmatch(r"(?:sets? |turns? )?(?:the )?volume (?:to|at|down to|up to) " + _NUM + r" ?(?:%|percent)?", c)
    if m: return {"do": "volume", "set": int(m.group(1))}
    m = re.fullmatch(r"(?:sets? |turns? |dims? )?(?:the )?(?:brightness|screen|display) (?:to|at|down to|up to) " + _NUM + r" ?(?:%|percent)?", c)
    if m: return {"do": "brightness", "set": int(m.group(1))}
    if re.fullmatch(r"(?:mutes?|mutes? the microphone|goes deaf)", c): return {"do": "mute", "on": True}
    if re.fullmatch(r"hides? the (?:interface|ui)", c): return {"do": "hide_ui", "on": True}
    if re.fullmatch(r"closes? (?:everything|all windows|the windows)", c): return {"do": "close_all"}
    m = re.fullmatch(r"starts? a (?:timer|focus timer) (?:for|of) (\d{1,3}) minutes?", c)
    if m: return {"do": "timer", "seconds": int(m.group(1)) * 60, "label": "your scene timer is up"}
    m = re.fullmatch(r"opens? (.+)", c)
    if m:
        p = opener.parse_command("open " + m.group(1))
        o = p and p.get("open")
        if o and o.get("action") == "open_url": return {"do": "open", "url": o["url"]}
        if o and o.get("action") == "open_app": return {"do": "open", "app": o["app"]}
    return None


def parse_command(text):
    low = text.lower().strip(" .?!")
    names = load()
    m = re.fullmatch(r"(?:please )?(?:start |run |activate |switch to |go to |turn on |enable )?(?:the )?([a-z ]{2,30}?)(?: mode| scene)", low)
    if m and m.group(1).strip() in names:
        return {"kind": "run", "name": m.group(1).strip()}
    if re.fullmatch(r"(?:good ?night|night night|i'?m going to bed|going to bed)(?: (?:jarvis|rose))?", low) and "goodnight" in names:
        return {"kind": "run", "name": "goodnight"}
    m = re.fullmatch(r"(?:please )?(?:run|start|activate|play) (?:the )?([a-z ]{2,30}?) scene", low)
    if m and m.group(1).strip() in names:
        return {"kind": "run", "name": m.group(1).strip()}
    if re.fullmatch(r"back to normal|restore (?:the )?(?:volume and brightness|normal settings)|end (?:movie|focus|the) mode|normal mode", low):
        return {"kind": "restore"}
    if re.fullmatch(r"what scenes do i have|list (?:my )?scenes|which scenes (?:are there|do i have)", low):
        return {"kind": "list"}
    m = re.match(r"(?:please )?(?:make|create|add|save) a scene (?:called|named) ([a-z ]{2,30}?) (?:that|which) (.+)$", low)
    if m:
        steps, bad = [], []
        for cl in re.split(r",| and (?=(?:sets?|turns?|dims?|opens?|hides?|mutes?|starts?|closes?))|\band\b(?= (?:sets?|turns?|dims?|opens?|hides?|mutes?|starts?|closes?))", m.group(2)):
            if not cl or not cl.strip():
                continue
            st = parse_clause(cl)
            (steps if st and validate_step(st) else bad).append(validate_step(st) if st else cl.strip())
        return {"kind": "add", "name": m.group(1).strip(), "steps": [s for s in steps if s], "bad": [b for b in bad if isinstance(b, str)]}
    m = re.fullmatch(r"(?:please )?(?:delete|remove) the ([a-z ]{2,30}?) scene", low)
    if m:
        return {"kind": "delete", "name": m.group(1).strip()}
    return None
