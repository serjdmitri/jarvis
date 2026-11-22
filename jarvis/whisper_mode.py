"""Whisper mode, like Alexa's: lower volume, shorter answers, no sound effects, no blinking, softer visuals. "whisper mode on / off", or automatically between the night hours you set
(settings "whisper_auto", "whisper_from" / "whisper_to" as "22:00" / "07:00"). One place decides; the speaker, the sounds, the brain prompt and the page ask here."""
import re
import time

from . import settings

GAIN = 0.4                         # pw-play volume while whispering


def _hm(v, default):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(v or ""))
    return (int(m.group(1)) * 60 + int(m.group(2))) if m and int(m.group(1)) < 24 and int(m.group(2)) < 60 else default


def active(now=None):
    if settings.get("whisper"):
        return True
    if not settings.get("whisper_auto"):
        return False
    t = time.localtime(now or time.time())
    cur, a, b = t.tm_hour * 60 + t.tm_min, _hm(settings.get("whisper_from"), 22 * 60), _hm(settings.get("whisper_to"), 7 * 60)
    return (a <= cur or cur < b) if a > b else (a <= cur < b)


def prompt_suffix():
    return "\nWhisper mode is on: answer in one short, calm sentence." if active() else ""


def parse_command(text):
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    if re.fullmatch(r"(?:please )?(?:turn |switch )?(?:on |into )?(?:the )?whisper(?: mode)?(?: on)?|(?:please )?(?:go|be) (?:into )?(?:a )?whisper(?: mode)?|(?:please )?whisper to me|(?:please )?(?:turn |switch )?(?:the )?whisper mode on|(?:please )?(?:turn |switch )?(?:on )?whisper mode", low):
        return {"kind": "on"}
    if re.fullmatch(r"(?:please )?(?:turn |switch )?(?:off |out of )?(?:the )?whisper(?: mode)?(?: off)?|(?:please )?(?:stop|quit|exit|end) (?:the )?whisper(?:ing)?(?: mode)?|(?:please )?(?:turn |switch )?(?:the )?whisper mode off|(?:please )?talk normally|(?:please )?normal voice", low) and ("off" in low or "stop" in low or "quit" in low or "exit" in low or "end" in low or "normal" in low):
        return {"kind": "off"}
    m = re.fullmatch(r"(?:please )?(?:whisper|quiet) (?:automatically |mode )?(?:at night|from (\d{1,2})(?::(\d{2}))? ?(am|pm)? (?:to|until) (\d{1,2})(?::(\d{2}))? ?(am|pm)?)", low)
    if m:
        if not m.group(1):
            return {"kind": "auto", "from": "22:00", "to": "07:00"}
        def hm(h, mi, ap):
            h = int(h) % 12 + (12 if ap == "pm" else 0) if ap else int(h)
            return f"{h:02d}:{int(mi or 0):02d}"
        return {"kind": "auto", "from": hm(m.group(1), m.group(2), m.group(3)), "to": hm(m.group(4), m.group(5), m.group(6))}
    return None
