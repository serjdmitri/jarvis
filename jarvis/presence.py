"""Desk presence: cheap and private. Only while "desk presence" is ON, one tiny grey picture (libjpeg decodes it at 1/8 size, about 80x60) of the phone camera every 1.5 seconds is compared with the one before it (frame differencing).
Motion means you are at your desk; no motion for 5 minutes means away. NO face recognition, NO identification, NO frames kept (only the previous 768 grey numbers live in memory), NO recording,
nothing is sent anywhere. It shares the phone's single stream with everything else (no second viewer) and pauses while the full Look view is open. Used for: reminders that wait for you
("remind me to X when I'm back at my desk"), a short welcome-back card, dimming the clock while you are away, and waking the idle clock when you sit down."""
import json
import re
import subprocess
import threading
import time

from . import config, phonecam, settings

INTERVAL = 1.5
MOTION = 2.5                  # mean absolute grey difference (0-255) that counts as movement (sensor noise is about 0.5-1)
AWAY_AFTER_S = 300
STATE = {"on": False, "paused": False, "present": None, "last_motion": 0.0, "score": 0.0, "err": "", "frames": 0}
_th = {"t": None, "stop": threading.Event()}
_cb = {"change": None, "busy": lambda: False}
DESK = config.DATA / "desk_reminders.json"


def _score(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a) if a and b and len(a) == len(b) else 0.0


def _grey(jpeg):
    """One JPEG -> a tiny grey picture (libjpeg's own 1/8 decode: about 5 ms) as bytes, or None."""
    try:
        r = subprocess.run(["djpeg", "-grayscale", "-scale", "1/8", "-pnm"], input=jpeg, capture_output=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.match(rb"P5\s+(\d+)\s+(\d+)\s+255\s", r.stdout)
    return r.stdout[m.end():] if m and len(r.stdout) - m.end() == int(m.group(1)) * int(m.group(2)) else None


def _set_present(p):
    if STATE["present"] != p:
        was = STATE["present"]
        STATE["present"] = p
        if _cb["change"]:
            try:
                _cb["change"](p, was)
            except Exception:
                pass


def _loop():
    prev, last_n = None, -1
    phonecam.RELAY.hold()
    try:
        while not _th["stop"].is_set():
            if STATE["paused"] or _cb["busy"]() or not phonecam.get_url():
                prev = None
                time.sleep(2)
                continue
            f, n = phonecam.RELAY.latest, phonecam.RELAY.n
            phonecam.RELAY.want(10)
            if f and n != last_n:
                last_n = n
                g = _grey(f)
                if g:
                    STATE["frames"] += 1
                    sc = _score(prev, g)
                    STATE["score"] = round(sc, 2)
                    if prev is not None and sc >= MOTION:
                        STATE["last_motion"] = time.time()
                        _set_present(True)
                    prev = g
                if STATE["present"] is None:
                    _set_present(True if STATE["last_motion"] else None)
                elif STATE["present"] and time.time() - STATE["last_motion"] > AWAY_AFTER_S:
                    _set_present(False)
            elif phonecam.RELAY.err and not f:
                STATE["err"] = "the phone camera is not reachable"
            time.sleep(INTERVAL)
    finally:
        phonecam.RELAY.release()


def start(on_change=None, busy=None):
    """Turn presence on (idempotent). -> True if running."""
    if on_change:
        _cb["change"] = on_change
    if busy:
        _cb["busy"] = busy
    if _th["t"] and _th["t"].is_alive():
        return True
    if not phonecam.get_url():
        STATE["err"] = "no phone camera is set up"
        return False
    _th["stop"].clear()
    STATE.update(on=True, paused=False, present=None, err="", frames=0)
    _th["t"] = threading.Thread(target=_loop, daemon=True)
    _th["t"].start()
    return True


def stop():
    _th["stop"].set()
    STATE.update(on=False, present=None)


def pause(on):
    STATE["paused"] = bool(on)


# ----------------------------------------------------------------------------- reminders that wait for you
def _load():
    try:
        return json.loads(DESK.read_text())
    except (OSError, ValueError):
        return []


def add_desk_reminder(label):
    items = _load() + [{"label": re.sub(r"\s+", " ", label).strip()[:120], "t": time.time()}]
    config.DATA.mkdir(parents=True, exist_ok=True)
    DESK.write_text(json.dumps(items[-20:]))


def take_desk_reminders():
    """-> the waiting reminders (each is returned once, then removed: it fires exactly once)."""
    items = _load()
    if items:
        DESK.write_text("[]")
    return items


def parse_command(text):
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    m = re.fullmatch(r"(?:please )?remind me (?:to |about |that )?(.+?) (?:when|as soon as|once|the moment) (?:i(?:'m| am)|i get|i'm) (?:back|at|sitting|here)(?: (?:at|to|on|in) (?:my |the )?(?:desk|computer|office|chair|room|workspace))?|(?:please )?when i(?:'m| am) back(?: (?:at|to) (?:my |the )?(?:desk|computer))? remind me (?:to |about )?(.+)", low)
    if m:
        return {"kind": "remind", "label": (m.group(1) or m.group(2)).strip()}
    if re.fullmatch(r"(?:please )?(?:turn on|start|enable|switch on) (?:the )?(?:desk )?presence(?: detection)?(?: mode)?|(?:please )?(?:turn on|start) (?:the )?desk (?:watch|sensor)", low):
        return {"kind": "on"}
    if re.fullmatch(r"(?:please )?(?:turn off|stop|disable|switch off) (?:the )?(?:desk )?presence(?: detection)?|(?:please )?(?:turn off|stop) (?:the )?desk (?:watch|sensor)", low):
        return {"kind": "off"}
    if re.fullmatch(r"(?:please )?pause (?:the )?(?:desk )?presence(?: detection)?", low):
        return {"kind": "pause"}
    if re.fullmatch(r"(?:please )?resume (?:the )?(?:desk )?presence(?: detection)?", low):
        return {"kind": "resume"}
    return None
