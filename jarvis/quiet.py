"""Quiet hours ("quiet hours from 11 to 7") and the focus timer ("focus for 25 minutes").
Quiet hours: the screen dims, he stays silent on his own (no briefing, health warnings or "Anything else?"), only your wake phrase wakes him; timers and alarms you set still go off.
Focus timer: quiet alerts (a soft tone and a card, a short spoken line only outside quiet hours), "five minutes left", then a break prompt."""
import re
from datetime import datetime

from . import settings


def _mins(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def active(now=None):
    if settings.get("focus_mode"):
        return True                                   # focus mode: the same silence as quiet hours (timers and alarms still sound)
    f, t = settings.get("quiet_from"), settings.get("quiet_to")
    if not f or not t:
        return False
    n = now or datetime.now()
    cur, a, b = n.hour * 60 + n.minute, _mins(f), _mins(t)
    return (a <= cur < b) if a < b else (cur >= a or cur < b)


def _hour(h, ap, default_pm):
    h = int(h)
    if ap:
        ap = ap.replace(".", "")
        return (h % 12) + (12 if ap == "pm" else 0)
    if default_pm:
        return (h % 12) + 12 if 6 <= h <= 12 else h % 12
    return h % 12 if h <= 12 else h


def parse_command(text):
    low = text.lower().strip(" .?!")
    m = re.fullmatch(r"(?:please )?(?:set |start |turn on )?quiet hours (?:from |between )?(\d{1,2})(?::(\d{2}))? ?(a\.?m\.?|p\.?m\.?)? (?:to|until|till|and) (\d{1,2})(?::(\d{2}))? ?(a\.?m\.?|p\.?m\.?)?", low)
    if m:
        f_h, t_h = _hour(m.group(1), m.group(3), True), _hour(m.group(4), m.group(6), False)
        f_m, t_m = int(m.group(2) or 0), int(m.group(5) or 0)
        spoken = lambda h, mi: f"{(h % 12) or 12}{(':%02d' % mi) if mi else ''} {'AM' if h % 24 < 12 else 'PM'}"
        return {"kind": "set", "from": f"{f_h % 24:02d}:{f_m:02d}", "to": f"{t_h % 24:02d}:{t_m:02d}", "speech": f"Quiet hours from {spoken(f_h, f_m)} to {spoken(t_h, t_m)}. The screen will dim and I'll stay silent unless you use my wake phrase."}
    if re.fullmatch(r"(?:please )?(?:turn off|stop|cancel|end|disable) quiet hours|quiet hours off", low):
        return {"kind": "off", "speech": "Quiet hours are off."}
    m = re.fullmatch(r"(?:please )?(?:start |begin |set )?(?:a )?(?:focus|pomodoro)(?: timer| session)?(?: for| of)? (\d{1,3}) ?(?:minutes?|mins?)", low)
    if m and 1 <= int(m.group(1)) <= 240:
        return {"kind": "focus", "minutes": int(m.group(1)), "speech": f"Focus for {m.group(1)} minutes. I'll stay quiet."}
    if re.fullmatch(r"(?:please )?(?:stop|cancel|end) (?:the )?(?:focus|pomodoro)(?: timer| session)?|(?:please )?i'?m done focusing", low):
        return {"kind": "focus_stop", "speech": "Focus ended."}
    return None
