"""Small things brought over from the Mac Jarvis: notes, alarms, the morning briefing, "how fast are you", "what happened" and route previews.
Everything here is matched by code (no brain call) and uses only local files or free services (RSS headlines, OpenStreetMap Nominatim, the OSRM demo router).
Spoken words are never stored: the event log (run/events.jsonl) only holds what Jarvis did ("opened YouTube", "sent a link to your Mac")."""
import json
import os
import re
import statistics
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from . import config, settings

UA = {"User-Agent": "jarvis-local-assistant/0.1"}
EVENTS = config.RUN_DIR / "events.jsonl"
LAST_ROUTE = {"url": None, "label": None, "text": ""}
PENDING = []                  # timers and reminders still to go off: {"due": epoch seconds, "label": str}
HISTORY = []                  # timing marks of the last 20 requests


def _cfg_dir():
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "jarvis")


def NOTES():
    """Plain text in the jarvis folder (data/notes.txt, git-ignored). An older notes file in the config folder is moved over once."""
    p = config.DATA / "notes.txt"
    old = os.path.join(_cfg_dir(), "notes.txt")
    if not p.exists() and os.path.exists(old):
        p.parent.mkdir(exist_ok=True)
        try:
            os.replace(old, p)
        except OSError:
            pass
    p.parent.mkdir(exist_ok=True)
    return str(p)


# ----------------------------------------------------------------------------- notes
REM_PATH = config.DATA / "reminders.json"


def save_pending():
    """Reminders and timers are kept in data/reminders.json so they survive a restart."""
    try:
        REM_PATH.parent.mkdir(exist_ok=True)
        REM_PATH.write_text(json.dumps([p for p in PENDING if p["due"] > time.time() - 300], indent=1), encoding="utf-8")
    except OSError:
        pass


def load_pending():
    """-> (still to go off, missed within the last 5 minutes while Jarvis was off)."""
    try:
        items = json.loads(REM_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], []
    now = time.time()
    return [p for p in items if p["due"] > now], [p for p in items if now - 300 < p["due"] <= now]


def list_reminders():
    now = time.time()
    pend = sorted([p for p in PENDING if p["due"] > now], key=lambda p: p["due"])
    if not pend:
        return {"speech": "You have no reminders or timers.", "cards": []}
    def when(p):
        secs = int(p["due"] - now)
        return f"in {secs // 60 + 1} minutes" if secs >= 90 else f"in {secs} seconds"
    items = [f"{datetime.fromtimestamp(p['due']):%-I:%M %p}  ({when(p)})  {p['label'] or 'timer'}" for p in pend]
    spoken = "; ".join(f"{p['label'] or 'a timer'} {when(p)}" for p in pend[:3])
    return {"speech": f"You have {len(pend)}: {spoken}.", "cards": [{"type": "list", "title": "Reminders and timers", "items": items}]}


def add_note(text):
    with open(NOTES(), "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M}  {text.strip()}\n")


def delete_last_note():
    try:
        lines = open(NOTES(), encoding="utf-8").read().splitlines()
    except OSError:
        lines = []
    if not lines:
        return None
    gone = lines.pop()
    open(NOTES(), "w", encoding="utf-8").write("\n".join(lines) + ("\n" if lines else ""))
    return re.sub(r"^\d{4}-\d\d-\d\d \d\d:\d\d\s+", "", gone)


LIST_PATH = config.DATA / "list.json"


def list_items():
    try:
        return json.loads(LIST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save_list(items):
    LIST_PATH.parent.mkdir(exist_ok=True)
    LIST_PATH.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def list_add(item):
    items = list_items()
    item = re.sub(r"\s+", " ", item).strip(" .")
    if not item or item.lower() in [i.lower() for i in items]:
        return False
    _save_list(items + [item])
    return True


def search_notes(query, limit=5):
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 1]
    try:
        lines = open(NOTES(), encoding="utf-8").read().splitlines()
    except OSError:
        return []
    hits = [ln for ln in lines if words and all(w in ln.lower() for w in words)]
    if not hits and words:
        hits = [ln for ln in lines if any(w in ln.lower() for w in words)]
    return hits[-limit:][::-1]


# ----------------------------------------------------------------------------- events ("what happened"); no spoken words
def log_event(kind, detail=""):
    try:
        config.RUN_DIR.mkdir(exist_ok=True)
        with open(EVENTS, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "kind": kind, "detail": str(detail)[:120]}) + "\n")
        lines = EVENTS.read_text(encoding="utf-8").splitlines()
        if len(lines) > 500:
            EVENTS.write_text("\n".join(lines[-300:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def recent_events(n=5):
    try:
        lines = EVENTS.read_text(encoding="utf-8").splitlines()[-n:]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out


def say_events(n=5):
    ev = recent_events(n)
    if not ev:
        return "Nothing has happened yet."
    parts = []
    for e in ev:
        when = datetime.fromtimestamp(e["t"]).strftime("%-I:%M")
        parts.append(f"{when}, {e['kind']}" + (f" {e['detail']}" if e["detail"] else ""))
    return "Recently: " + "; ".join(parts) + "."


# ----------------------------------------------------------------------------- alarms
def parse_alarm(text):
    """'set an alarm for 7 am' / 'wake me at 6:30' / 'remind me at 5 pm to call mum' -> (seconds from now, label, spoken time) or None."""
    t = text.lower()
    m = re.search(r"\b(?:alarm|wake me|remind me)\b.*?\b(?:at|for)\s+(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?", t)
    if not m or not re.search(r"\b(alarm|wake me|remind me)\b", t):
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "")
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    now = datetime.now()
    due = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    if due <= now:
        due += timedelta(days=1)
    lab = re.search(r"\b(?:to|that|about)\s+(.+)$", t[m.end():])
    spoken = due.strftime("%-I:%M %p").replace(":00", "")
    return int((due - now).total_seconds()), (lab.group(1).strip()[:120] if lab else ""), spoken


# ----------------------------------------------------------------------------- headlines + briefing
def headlines(n=3):
    try:
        req = urllib.request.Request("https://feeds.bbci.co.uk/news/rss.xml", headers=UA)
        with urllib.request.urlopen(req, timeout=4) as r:
            xml = r.read().decode("utf-8", "ignore")
        titles = re.findall(r"<item>.*?<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", xml, re.S)
        return [re.sub(r"\s+", " ", t).strip() for t in titles[:n]]
    except Exception:
        return []


# ----------------------------------------------------------------------------- routes
def _geocode(q):
    req = urllib.request.Request("https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" + urllib.parse.quote(q), headers=UA)
    with urllib.request.urlopen(req, timeout=4) as r:
        hit = json.load(r)
    return (float(hit[0]["lat"]), float(hit[0]["lon"]), hit[0].get("display_name", q).split(",")[0]) if hit else None


def route_preview(dest):
    """-> {"speech", "url", "label"} or {"error"}. From the home area (set it with 'set my home to ...') by car, using free OpenStreetMap services."""
    lat, lon = settings.get("home_lat"), settings.get("home_lon")
    if lat is None or lon is None:
        return {"error": "Tell me where you start first: say set my home to, and the place."}
    try:
        g = _geocode(dest)
        if not g:
            return {"error": f"I couldn't find {dest}."}
        url = f"http://router.project-osrm.org/route/v1/driving/{lon},{lat};{g[1]},{g[0]}?overview=false"
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=5) as r:
            rt = json.load(r)["routes"][0]
    except Exception:
        return {"error": "I couldn't get a route just now."}
    km, mins = rt["distance"] / 1000, round(rt["duration"] / 60)
    web = f"https://www.openstreetmap.org/directions?engine=fossgis_osrm_car&route={lat}%2C{lon}%3B{g[0]}%2C{g[1]}"
    when = f"{mins // 60} hour{'s' if mins >= 120 else ''} {mins % 60} minutes" if mins >= 60 else f"{mins} minutes"
    pad = 0.02
    w_, e_ = min(lon, g[1]) - pad, max(lon, g[1]) + pad
    s_, n_ = min(lat, g[0]) - pad, max(lat, g[0]) + pad
    emb = "https://www.openstreetmap.org/export/embed.html?bbox=%f%%2C%f%%2C%f%%2C%f&layer=mapnik&marker=%f%%2C%f" % (w_, s_, e_, n_, g[0], g[1])
    return {"speech": f"{g[2]} is {km:.0f} kilometres away, about {when} by car. Here it is on the map.", "url": web, "embed": emb, "label": g[2], "km": km, "mins": mins}


# ----------------------------------------------------------------------------- what the user says
def parse_command(text):
    t = text.strip()
    low = t.lower().strip(" .?!")
    if re.fullmatch(r"(?:please )?delete (?:the )?last note|(?:please )?remove (?:the )?last note|(?:please )?forget (?:the )?last note", low):
        return {"kind": "note_delete"}
    m = re.match(r"(?:please )?(?:add|put) (.+?) (?:to|on|onto) (?:my |the )?(?:shopping |grocery )?list$", t.strip(" .?!"), re.I)
    if m:
        return {"kind": "list_add", "item": m.group(1).strip()}
    if re.fullmatch(r"what(?:'s| is) on my (?:shopping |grocery )?list|read (?:me )?my (?:shopping |grocery )?list|show (?:me )?my (?:shopping |grocery )?list", low):
        return {"kind": "list_read"}
    if re.fullmatch(r"(?:please )?(?:clear|empty|wipe) (?:my |the )?(?:shopping |grocery )?list", low):
        return {"kind": "list_clear"}
    if re.fullmatch(r"(?:please )?(?:send|text) (?:me )?(?:my |the )?(?:shopping |grocery )?list(?: to my phone| to me)?", low):
        return {"kind": "list_phone"}
    m = re.match(r"(?:please )?(?:note down|make a note|note) (?:that )?(.+)$", t, re.I)
    if m and not re.search(r"\b(alarm|timer)\b", low):
        return {"kind": "note", "text": m.group(1).strip(" .")}
    m = re.search(r"\b(?:search|look through|look in|find in|check) my notes? (?:for|about) (.+)$", low)
    if m:
        return {"kind": "notes_search", "q": m.group(1).strip()}
    if re.fullmatch(r"(?:read|show|what are) (?:me )?my notes|what did i ask you to remember", low):
        return {"kind": "notes_recent"}
    if re.fullmatch(r"what(?:'s| are| is) (?:on )?my (?:reminders?|timers?|alarms?)(?: and (?:timers?|reminders?))?|what (?:reminders?|timers?) (?:do i have|are (?:set|running))|list (?:my )?(?:reminders?|timers?)|show (?:me )?my (?:reminders?|timers?)", low):
        return {"kind": "reminders"}
    if re.fullmatch(r"(?:good morning|morning briefing|brief me|give me (?:my|the) (?:morning )?briefing|daily briefing)(?: (?:jarvis|rose))?", low):
        return {"kind": "briefing"}
    m = re.search(r"\bbrief me every (?:morning|day) at (\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?", low)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "am").replace(".", "")
        h = h + 12 if ap == "pm" and h < 12 else 0 if ap == "am" and h == 12 else h
        return {"kind": "briefing_at", "hhmm": f"{h:02d}:{mi:02d}", "spoken": f"{(h % 12) or 12}{(':%02d' % mi) if mi else ''} {'AM' if h < 12 else 'PM'}"}
    if re.fullmatch(r"(?:stop|cancel|turn off) (?:the )?(?:daily|morning) briefing", low):
        return {"kind": "briefing_off"}
    m = re.match(r"(?:set )?my usual (?:place|destination|spot) (?:is|to) (.+)$", low)
    if m:
        return {"kind": "usual_place", "place": t[m.start(1):m.end(1)].strip(" .")}
    if re.search(r"\bhow fast are you\b|\bhow quick are you\b|\bhow long do you take\b", low):
        return {"kind": "speed"}
    if re.fullmatch(r"what (?:just )?happened|what have you done|what did you just do|recent events", low):
        return {"kind": "events"}
    a = parse_alarm(t)
    if a:
        return {"kind": "alarm", "secs": a[0], "label": a[1], "spoken": a[2]}
    if re.search(r"\b(?:send|text|show)\b.*\b(?:route|directions)\b.*\b(?:to|on)\b.*\bphone\b", low):
        return {"kind": "route_phone"}
    m = re.search(r"\b(?:take me to|directions to|route to|how do i get to|navigate to|get me to)\s+(.+)$", t, re.I)
    if m:
        return {"kind": "route", "dest": m.group(1).strip(" .?!")}
    return None


def speed_line(history):
    """history: list of dicts of timing marks (seconds after you finished speaking)."""
    fa = [h["first_audio"] for h in history if "first_audio" in h]
    dn = [h["done"] for h in history if "done" in h]
    if not fa:
        return "I don't have any timings yet. Ask me something first."
    return (f"Over my last {len(fa)} requests I started speaking after a median of {statistics.median(fa):.1f} seconds, "
            f"the slowest took {max(fa):.1f}, and I finished after a median of {statistics.median(dn):.1f}.")
