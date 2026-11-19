"""User preferences that the page and voice both change: speaking speed (per voice), auto visuals, stay out, clock timing/format.
apply(key, value) validates and saves (settings.json); parse_command(text) turns voice into (key, value) changes."""
import re

from . import settings

SPEED_MIN, SPEED_MAX, STEP = 0.7, 1.6, 0.15
IDLE_CHOICES = (0.5, 1, 2, 5, 10, 30, 0)            # minutes; 0 = never
DEFAULTS = {"show_heard": True, "large_text": False, "high_contrast": False, "update_check": False, "onboarded": False, "notify_phone_reminders": True, "phone_voice": False, "phone_voice_speak": False, "phone_control_from_phone": False, "term_wake": False, "machine_warnings": True, "auto_visuals": True, "stay_out": True, "idle_minutes": 5, "clock24": False, "clock_seconds": True, "followup": False}


def get(key):
    return settings.get(key, DEFAULTS.get(key))


def speed(persona):
    try:
        return max(SPEED_MIN, min(SPEED_MAX, float(settings.get("speed_" + persona, 1.0))))
    except (TypeError, ValueError):
        return 1.0


def clean(key, value):
    """-> the validated value, or raises ValueError."""
    if key in ("auto_visuals", "stay_out", "clock24", "clock_seconds", "machine_warnings", "term_wake", "phone_voice", "show_heard", "large_text", "high_contrast", "update_check", "onboarded", "notify_phone_reminders", "phone_voice_speak", "phone_control_from_phone"):
        return bool(value)
    if key == "speed":
        v = round(float(value), 2)
        if not SPEED_MIN - 1e-6 <= v <= SPEED_MAX + 1e-6:
            raise ValueError("speed out of range")
        return max(SPEED_MIN, min(SPEED_MAX, v))
    if key == "volume":
        v = int(float(value))
        if not 0 <= v <= 100:
            raise ValueError("volume out of range")
        return v
    if key == "idle_minutes":
        v = float(value)
        if v not in IDLE_CHOICES:
            raise ValueError("not a preset")
        return v if v != int(v) else int(v)
    raise ValueError("unknown setting")


def _volume():
    import subprocess
    try:
        out = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True, timeout=2).stdout
        return int(round(float(out.split()[1]) * 100))
    except Exception:
        return 50


def public(persona):
    return {"volume": _volume(), "home": (settings.get("home_area") or "").split(",")[0][:60], "speed": speed(persona), "auto_visuals": bool(get("auto_visuals")), "machine_warnings": bool(get("machine_warnings")), "show_heard": bool(get("show_heard")), "large_text": bool(get("large_text")), "high_contrast": bool(get("high_contrast")), "term_wake": bool(get("term_wake")), "stay_out": bool(get("stay_out")), "idle_minutes": get("idle_minutes"),
            "clock24": bool(get("clock24")), "clock_seconds": bool(get("clock_seconds"))}


NUM = {"thirty": 30, "ten": 10, "five": 5, "two": 2, "one": 1, "half a": 0.5}


def parse_command(text, persona):
    """-> {"set": {key: value}, "say": reply} or None."""
    low = text.lower().strip(" .?!")
    cur = speed(persona)
    if re.fullmatch(r"(?:please )?(?:(?:can|could|would|will) you (?:please )?)?(?:speak|talk|go) (?:a (?:bit|little) )?(?:faster|quicker)(?: please)?|speed up(?: please)?|(?:please )?(?:(?:can|could|would|will) you (?:please )?)?(?:speak|talk) (?:a (?:bit|little) )?(?:more )?quickly(?: please)?", low):
        v = min(SPEED_MAX, round(cur + STEP, 2))
        return {"set": {"speed": v}, "say": "Faster." if v > cur else "That's the fastest I go."}
    if re.fullmatch(r"(?:please )?(?:(?:can|could|would|will) you (?:please )?)?(?:speak|talk|go) (?:a (?:bit|little) )?(?:slower|more slowly)(?: please)?|slow down(?: please)?", low):
        v = max(SPEED_MIN, round(cur - STEP, 2))
        return {"set": {"speed": v}, "say": "Slower." if v < cur else "That's the slowest I go."}
    if re.fullmatch(r"(?:please )?(?:normal|regular|default|usual) (?:speaking )?speed|(?:please )?(?:speak|talk) normally|reset (?:the )?(?:speaking )?speed", low):
        return {"set": {"speed": 1.0}, "say": "Normal speed."}
    if re.fullmatch(r"(?:please )?turn (?:off|on) (?:the )?auto(?:matic)? (?:visuals|pictures|images)|(?:please )?(?:stop|start) (?:showing )?(?:automatic |auto )?(?:visuals|pictures)(?: automatically)?", low):
        on = bool(re.search(r"turn on|start", low))
        return {"set": {"auto_visuals": on}, "say": "Auto visuals on." if on else "Auto visuals off. I'll show pictures only when you ask."}
    m = re.fullmatch(r"(?:please )?(?:turn|switch) (?:stay out|stay away) (on|off)|(?:please )?(?:stay out|don'?t (?:take|grab) (?:the )?screen(?: back)?)", low)
    if m:
        on = (m.group(1) or "on") == "on"
        return {"set": {"stay_out": on}, "say": "I'll stay out of your way." if on else "I'll come to the front when I have something."}
    if re.fullmatch(r"(?:please )?(?:turn|switch) (off|on) (?:the )?(?:machine|memory|system) warnings?|(?:please )?(stop|start) (?:the )?(?:machine|memory|system) warnings?", low):
        on = bool(re.search(r"turn on|switch on|start", low))
        return {"set": {"machine_warnings": on}, "say": "Machine warnings on." if on else "Machine warnings off."}
    m = re.fullmatch(r"(?:please )?(?:show |start )?(?:the )?clock after (half a|one|two|five|ten|thirty|\d+(?:\.\d+)?) (?:minutes?|mins?)", low)
    if m:
        n = NUM.get(m.group(1)) if m.group(1) in NUM else float(m.group(1))
        if n in IDLE_CHOICES:
            return {"set": {"idle_minutes": n if n != int(n) else int(n)}, "say": f"The clock will come on after {m.group(1)} minutes."}
        return {"say": "I can do 30 seconds, 1, 2, 5, 10 or 30 minutes, or never."}
    if re.fullmatch(r"(?:please )?(?:turn|switch) (?:the )?(?:clock|screensaver|screen saver|idle screen) off|(?:please )?(?:never|don'?t) show (?:the )?clock", low):
        return {"set": {"idle_minutes": 0}, "say": "The clock is off."}
    m = re.fullmatch(r"(?:please )?(?:use |switch to |set (?:the )?clock to )(12|24)[ -]hour(?: time| clock)?", low)
    if m:
        return {"set": {"clock24": m.group(1) == "24"}, "say": f"{m.group(1)} hour time."}
    m = re.fullmatch(r"(?:please )?(?:turn|switch) (?:the )?(?:clock )?seconds (on|off)|(?:please )?(show|hide) (?:the )?seconds", low)
    if m:
        on = (m.group(1) or ("on" if m.group(2) == "show" else "off")) == "on"
        return {"set": {"clock_seconds": on}, "say": "Seconds on." if on else "Seconds off."}
    return None
