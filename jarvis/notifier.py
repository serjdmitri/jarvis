"""Phone notifications (ntfy) by category. Each category has an on/off toggle, a priority and shared quiet hours. NOTHING PRIVATE is ever put in a notification: no email text, no message text, no file
contents, no location: counts and short fixed summaries only (callers pass already-safe text; this also cuts everything to 140 characters and refuses anything that looks like an address or a code).
Settings: notify_<category> (bool, default per CATEGORIES), notify_quiet_from / notify_quiet_to ("22:00" / "07:00"; urgent ones like find-my-phone and timers still go)."""
import re
import time

from . import phone, settings

CATEGORIES = {   # name: (default on, priority, ignores quiet hours)
    "reminders": (True, "default", False), "timers": (True, "high", True), "claude": (False, "default", False), "briefing": (True, "low", False), "delivery": (False, "low", False),
    "find": (True, "urgent", True), "mail": (False, "default", False),
}
_HM = re.compile(r"(\d{1,2}):(\d{2})")


def _mins(v, d):
    m = _HM.fullmatch(str(v or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else d


def in_quiet(now=None):
    t = time.localtime(now or time.time())
    cur, a, b = t.tm_hour * 60 + t.tm_min, _mins(settings.get("notify_quiet_from"), -1), _mins(settings.get("notify_quiet_to"), -1)
    if a < 0 or b < 0 or a == b:
        return False
    return (a <= cur or cur < b) if a > b else (a <= cur < b)


def enabled(cat):
    d = CATEGORIES.get(cat, (False, "default", False))[0]
    v = settings.get("notify_" + cat)
    return d if v is None else bool(v)


def safe_text(s):
    s = re.sub(r"\s+", " ", str(s or "")).strip()[:140]
    return s if not re.search(r"https?://|@\w+\.\w+|\b\d{6,}\b", s) else "Something needs you on the Go."


def send(cat, title, message, now=None):
    """-> {"ok": bool, "message": str}. Skipped (ok False, plain reason) when the category is off or it is quiet time."""
    if cat not in CATEGORIES:
        return {"ok": False, "message": "unknown category"}
    if not enabled(cat):
        return {"ok": False, "message": f"{cat} notifications are off"}
    if in_quiet(now) and not CATEGORIES[cat][2]:
        return {"ok": False, "message": "quiet hours"}
    return phone.send({"title": safe_text(title)[:60] or "Jarvis", "message": safe_text(message), "priority": CATEGORIES[cat][1]})


# ----------------------------------------------------------------------------- briefings on the phone (morning, afternoon, night)
SLOTS = ("morning", "afternoon", "night")
_sent = {}


def due_briefings(now=None):
    """-> the slots whose time has come today and were not sent yet. Each slot is "brief_<slot>" = "HH:MM" (or off)."""
    t = time.localtime(now or time.time())
    cur, day, out = t.tm_hour * 60 + t.tm_min, time.strftime("%Y-%m-%d", t), []
    for slot in SLOTS:
        want = _mins(settings.get("brief_" + slot), -1)
        if want >= 0 and cur >= want and cur < want + 30 and _sent.get(slot) != day:
            out.append(slot)
    return out


def compose(slot):
    """Short summaries only: counts and one weather line. Never the text of a message or an email."""
    parts = []
    try:
        from . import extras, weather
        w = weather.where()
        if w:
            c = weather.card(*w, imperial=settings.get("units") == "imperial")
            parts.append(f"{c['temp']}{c['unit']}, {c['desc']}, rain {c['pop']}%")
        n = len([p for p in extras.PENDING if p["due"] > time.time() and p["due"] - time.time() < 86400])
        if n:
            parts.append(f"{n} reminder{'s' if n != 1 else ''} today")
        k = len(extras.list_items())
        if k:
            parts.append(f"{k} on your list")
    except Exception:
        pass
    return " · ".join(parts) or "Nothing special today."


def send_due(now=None):
    """Called every 10 s by the assistant."""
    for slot in due_briefings(now):
        _sent[slot] = time.strftime("%Y-%m-%d", time.localtime(now or time.time()))
        send("briefing", f"Good {slot}", compose(slot), now=now)


def parse_command(text):
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    m = re.fullmatch(r"(?:please )?(?:send me|give me|text me|set up|set) (?:an? |the |my )?(morning|afternoon|night|evening) (?:briefing|summary)(?: (?:on|to) (?:my )?phone)?(?: at (\d{1,2})(?::(\d{2}))? ?(am|pm)?)?", low)
    if m:
        slot = "night" if m.group(1) == "evening" else m.group(1)
        h = int(m.group(2)) if m.group(2) else {"morning": 8, "afternoon": 13, "night": 21}[slot]
        if m.group(4):
            h = h % 12 + (12 if m.group(4) == "pm" else 0)
        return {"slot": slot, "time": f"{h % 24:02d}:{int(m.group(3) or 0):02d}"}
    m = re.fullmatch(r"(?:please )?(?:turn off|stop|cancel|disable) (?:the |my )?(morning|afternoon|night|evening) (?:briefing|summary)", low)
    if m:
        return {"slot": "night" if m.group(1) == "evening" else m.group(1), "time": None}
    return None
