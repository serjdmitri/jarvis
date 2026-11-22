"""Help that points at your real screen: "Show me how" and "Circle to ask".

Show me how   "how do I change the font size in this app?" -> a screenshot (only now, only when you ask) goes to the brain, which answers with the NEXT single step and a box around
              the control to use. A glowing ring is drawn there by jarvis/screenfx.py (click-through: you click the real button), the step is spoken and captioned, and Jarvis waits
              until the screen changes after your click (or you say "next" / press NEXT), then looks again. "stop" ends it; at most 12 steps; it asks if you seem stuck.
              It NEVER clicks or types for you. The box is checked with the Look-box rules (look.validate_boxes).
Circle to ask "circle to ask" / the CIRCLE button / SUPER+SHIFT+C, then draw a circle or rectangle with a finger or the mouse (and ask out loud, in either order): the region is
              cropped from a screenshot and answered, with the answer card placed next to it.
Screenshots live only in the RAM-backed runtime folder (mode 600), are deleted right after the brain has looked at them and again when the session ends; nothing is kept."""
import json
import os
import re
import subprocess
import time
import uuid

import numpy as np

from . import look

MAX_STEPS = 12
STUCK_S = 60                       # no change this long after a step: ask whether you are stuck
GIVE_UP_S = 120


def _ramdir():
    d = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return d if os.access(d, os.W_OK) else "/tmp"


def monitor():
    """-> {"name", "x", "y", "w", "h"} of the focused monitor in logical px (grim and the overlay use these)."""
    try:
        mons = json.loads(subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=1.5).stdout)
        m = next((m for m in mons if m.get("focused")), mons[0])
        sc = m.get("scale") or 1
        return {"name": m["name"], "x": int(m.get("x", 0)), "y": int(m.get("y", 0)), "w": int(m["width"] / sc), "h": int(m["height"] / sc)}
    except Exception:
        return {"name": None, "x": 0, "y": 0, "w": 1280, "h": 800}


def _grim(extra, out):
    m = monitor()
    cmd = ["grim"] + (["-o", m["name"]] if m["name"] else []) + extra + [out]
    subprocess.run(cmd, check=True, timeout=8, capture_output=True)


def grab(scale=0.6):
    """A JPEG of the focused monitor in the RAM folder (mode 600). Delete it with drop() as soon as it has been looked at."""
    p = os.path.join(_ramdir(), f"jarvis-sh-{uuid.uuid4().hex[:10]}.jpg")
    _grim(["-t", "jpeg", "-q", "72", "-s", str(scale)], p)
    os.chmod(p, 0o600)
    return p


def crop(box, pad=0.015):
    """The region (normalised x, y, w, h) from a fresh screenshot at full resolution -> path (RAM folder), or None."""
    m = monitor()
    x, y, w, h = [float(box[k]) for k in ("x", "y", "w", "h")]
    x0, y0 = max(0.0, x - pad), max(0.0, y - pad)
    x1, y1 = min(1.0, x + w + pad), min(1.0, y + h + pad)
    gx, gy, gw, gh = m["x"] + int(x0 * m["w"]), m["y"] + int(y0 * m["h"]), max(8, int((x1 - x0) * m["w"])), max(8, int((y1 - y0) * m["h"]))
    p = os.path.join(_ramdir(), f"jarvis-sh-{uuid.uuid4().hex[:10]}.png")
    try:
        subprocess.run(["grim", "-g", f"{gx},{gy} {gw}x{gh}", p], check=True, timeout=8, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        return None
    os.chmod(p, 0o600)
    return p


def drop(path):
    try:
        if path and os.path.basename(path).startswith("jarvis-sh-"):
            os.unlink(path)
    except OSError:
        pass


def cleanup():
    """Remove any screenshot of ours left behind (a crash mid-session)."""
    d = _ramdir()
    for f in os.listdir(d):
        if f.startswith("jarvis-sh-"):
            drop(os.path.join(d, f))


def sig():
    """A tiny grey picture of the screen (about 90x60) to notice when something changed. In memory only: grim writes a PPM to a pipe."""
    m = monitor()
    out = subprocess.run(["grim"] + (["-o", m["name"]] if m["name"] else []) + ["-s", "0.05", "-t", "ppm", "-"], capture_output=True, timeout=6).stdout
    mm = re.match(rb"P6\s+(\d+)\s+(\d+)\s+255\s", out)
    if not mm:
        return None
    w, h = int(mm.group(1)), int(mm.group(2))
    a = np.frombuffer(out[mm.end(): mm.end() + w * h * 3], dtype=np.uint8)
    if a.size != w * h * 3:
        return None
    return a.reshape(h, w, 3).mean(axis=2).astype(np.int16)


def changed(a, b, frac=0.02, delta=28):
    """True when at least `frac` of the pixels differ by more than `delta` between two sig() pictures (a click that opened a menu or dialog easily does; a blinking cursor does not)."""
    if a is None or b is None or a.shape != b.shape:
        return False
    return float((np.abs(a - b) > delta).mean()) >= frac


# ----------------------------------------------------------------------------- phrases
HOWTO = [
    re.compile(r"^(?:please )?(?:show me how|how (?:do|can|could) i|how to|walk me through|guide me through|help me|teach me how)\b(?: to)? (.+?) (?:in|on|with|using|inside) (?:this|that|the|my)(?: current)? (?:app|application|page|screen|website|site|window|program|menu|settings|browser|terminal|editor|tab|dialog)\b.*$", re.I),
    re.compile(r"^(?:please )?(?:show me how|how (?:do|can|could) i|walk me through|guide me through|help me)\b(?: to)? (.+?) (?:right )?here$", re.I),
    re.compile(r"^(?:please )?(?:where|what|which) (?:do i|should i|can i) (?:click|press|tap|touch|choose|select) (?:to|for) (.+)$", re.I),
    re.compile(r"^(?:please )?(?:show me how|walk me through|guide me through|point out how)\b(?: to)? (.+?) on (?:my|the) screen$", re.I),
    re.compile(r"^(?:please )?(?:show me (?:where|which button) (?:to|i)|point (?:to|at|me to)) (?:the )?(?:button|control|option|menu|setting) (?:to |for |that )?(.+)$", re.I),
]


def parse_howto(text):
    """-> goal string or None. Needs a screen cue (in this app, on my screen, here, where do I click): "show me how to tie a tie" is a video request, not this."""
    t = re.sub(r"\s+", " ", (text or "").strip(" .?!"))
    for pat in HOWTO:
        m = pat.match(t)
        if m and len(m.group(1).split()) <= 14:
            return m.group(1).strip()
    if re.fullmatch(r"(?:please )?(?:start )?(?:show me how|walk me through(?: it)?|screen guide|guide me)(?: please)?", t, re.I):
        return ""                                                        # asks what you want to do
    return None


def parse_control(text):
    """While a guide runs: "next" / "done" / "okay" -> "next"; "stop" / "cancel" / "never mind" -> "stop"; else None."""
    t = re.sub(r"[^a-z' ]", "", (text or "").lower()).strip()
    if re.fullmatch(r"(?:ok(?:ay)?,? )?(?:next(?: step)?|done|i did it|did it|finished|got it|go on|continue|what(?:'s| is) next|and then|then what)(?: please)?", t):
        return "next"
    if re.fullmatch(r"(?:please )?(?:stop|cancel|quit|exit|never ?mind|that'?s enough|enough|end(?: the)? guide|stop (?:showing|guiding)(?: me)?|i(?:'ve| have) got it|i got it from here)(?: please)?", t):
        return "stop"
    return None


CIRCLE = re.compile(r"^(?:please )?(?:circle to ask|let me circle (?:something|it|that)|i want to circle (?:something|it)|i(?:'ll| will) circle (?:it|something)|ask about (?:this|that) (?:part|bit|area|region) of (?:the|my) screen|"
                    r"(?:what(?:'s| is) )?(?:this|that) (?:on|in) (?:the|my) (?:screen|page|window)|what(?:'s| is) (?:this|that) (?:thing )?(?:here|on screen)|explain (?:this|that) (?:part|bit|area|section) of (?:the|my) screen)$", re.I)


def parse_circle(text):
    t = re.sub(r"\s+", " ", (text or "").strip(" .?!"))
    return bool(CIRCLE.match(t))


# ----------------------------------------------------------------------------- the brain's step
PROMPT = ("[SHOW ME HOW. The user wants to: {goal}. The attached image is their screen right now ({done}). Reply with ONLY one JSON object, nothing else: "
          "{{\"say\": \"ONE short sentence (max 16 words) telling them the next single action\", \"box\": {{\"x\": 0-1, \"y\": 0-1, \"w\": 0-1, \"h\": 0-1}} or null, \"done\": true or false}}. "
          "The box surrounds the ONE control they should use next, as fractions of the image (x, y = top-left corner, w, h = size); use null if that control is not visible and say where to look instead. "
          "Set done to true only if the goal is already achieved on screen. Point at what is really visible; never invent a control. Never ask the user to type a password or a secret.]")


def step_prompt(goal, steps):
    done = ("steps already shown: " + "; ".join(s[:60] for s in steps[-6:])) if steps else "this is the first step"
    return PROMPT.format(goal=goal[:140], done=done)


def parse_step(reply):
    """The brain's JSON -> {"say", "box" (validated, or None), "done"} or None when it is not usable."""
    m = re.search(r"\{.*\}", reply or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    say = re.sub(r"\s+", " ", str(d.get("say") or "")).strip()[:160]
    box = None
    b = d.get("box")
    if isinstance(b, dict):
        good = look.validate_boxes([{"name": "step", "box": [b.get("x"), b.get("y"), b.get("w"), b.get("h")]}], 1)
        box = good[0] if good else None
    if not say and not d.get("done"):
        return None
    return {"say": say, "box": box, "done": bool(d.get("done"))}


def circle_prompt(question):
    return (f"[CIRCLE TO ASK. The attached image is a crop of the user's screen, the part they circled. Their question: {question[:300]!r}. "
            "Answer in at most 3 short plain sentences (no markdown, no JSON). If it is text in another language, give the meaning. If it is an error, say what it means and the likely fix.]")
