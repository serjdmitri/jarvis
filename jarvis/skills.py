"""Things answered locally, instantly and reliably (no Claude round trip): time and date, simple maths,
weather (Open-Meteo, no key) and setting the home area. handle(text, persona) -> dict or None.
Everything else (search, places, recommendations, chat) goes to the brain."""
import ast
import json
import math
import operator
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from . import maps, settings

UA = {"User-Agent": "jarvis-local-assistant/0.1"}
WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 48: "foggy", 51: "light drizzle", 53: "drizzle",
       55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain",
       67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
       82: "heavy showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail"}


def _get_json(url, timeout=6):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.load(r)


def geocode(place):
    """-> dict(name, lat, lon, tz, label) or None  (Open-Meteo geocoding; only the place name is sent)."""
    q = place.strip()
    for cand in (q, q.split(",")[0].strip()):
        d = _get_json("https://geocoding-api.open-meteo.com/v1/search?count=1&language=en&format=json&name=" + urllib.parse.quote(cand))
        if d.get("results"):
            r = d["results"][0]
            label = ", ".join(x for x in (r.get("name"), r.get("admin1"), r.get("country")) if x)
            return {"name": r["name"], "lat": r["latitude"], "lon": r["longitude"], "tz": r.get("timezone"), "label": label}
    return None


def _mac_paired():
    from . import mac
    return mac.paired()


def context_line():
    """One line prepended to every request: the date/time (so 'what's the date' and 'tonight' work) and the home area."""
    now = datetime.now().astimezone()
    home = settings.get("home_area")
    auto3d = " Auto 3D is ON: for a physical or biological subject you MAY also add a show_3d intent." if settings.get("auto_3d") else ""
    return (f"[context:{auto3d} now {now.strftime('%A %d %B %Y, %-I:%M %p')} {now.tzname()}; "
            f"Mac paired: {'yes' if _mac_paired() else 'no (if asked to send something to the Mac, say it is not paired and to say pair my Mac)'}; "
            f"home area: {home if home else 'NOT SET (if a question needs the user\'s location, ask which town or area, in one short sentence)'}]\n")


# ----------------------------------------------------------------------------- maths
NUM = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod}


def words_to_digits(t):
    """'two hundred and fifty' -> '250' (up to millions); leaves other words alone."""
    toks, out, i = t.split(), [], 0
    while i < len(toks):
        w = toks[i]
        if w in NUM or w in TENS or (w in ("hundred", "thousand", "million") and out and out[-1].isdigit()):
            total, cur, j, used = 0, 0, i, False
            while j < len(toks):
                x = toks[j]
                if x in NUM: cur += NUM[x]; used = True
                elif x in TENS: cur += TENS[x]; used = True
                elif x == "hundred" and used: cur = (cur or 1) * 100
                elif x == "thousand" and used: total += (cur or 1) * 1000; cur = 0
                elif x == "million" and used: total += (cur or 1) * 1000000; cur = 0
                elif x == "and" and used and j + 1 < len(toks) and (toks[j + 1] in NUM or toks[j + 1] in TENS): pass
                else: break
                j += 1
            out.append(str(total + cur)); i = j
        else:
            out.append(w); i += 1
    return " ".join(out)


def _safe_eval(node):
    if isinstance(node, ast.Expression): return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)): return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)): return -_safe_eval(node.operand) if isinstance(node.op, ast.USub) else _safe_eval(node.operand)
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        a, b = _safe_eval(node.left), _safe_eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(b) > 100: raise ValueError("too big")
        return OPS[type(node.op)](a, b)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "sqrt" and len(node.args) == 1: return math.sqrt(_safe_eval(node.args[0]))
    raise ValueError("unsupported")


def maths(text):
    t = text.lower().replace(",", "")
    t = re.sub(r"^(?:hey |ok |okay )?(?:what'?s|what is|whats|how much is|calculate|compute|work out|tell me|can you tell me)\s+", "", t.strip(" ?.!"))
    t = re.sub(r"^the\s+", "", t)
    t = words_to_digits(t)
    t = re.sub(r"\bsquare root of (\d+(?:\.\d+)?)", r"sqrt(\1)", t)
    t = re.sub(r"(\d+(?:\.\d+)?)\s*(?:percent|%) of (\d+(?:\.\d+)?)", r"(\1/100*\2)", t)
    t = re.sub(r"(\d+(?:\.\d+)?) squared", r"(\1**2)", t); t = re.sub(r"(\d+(?:\.\d+)?) cubed", r"(\1**3)", t)
    for pat, rep in ((r"\bto the power of\b|\bto the\b|\braised to\b", "**"), (r"\bmultiplied by\b|\btimes\b|\bx\b", "*"), (r"\bdivided by\b|\bover\b", "/"),
                     (r"\bplus\b|\band\b", "+"), (r"\bminus\b|\btake away\b|\bless\b", "-"), (r"\bmod(?:ulo)?\b", "%")):
        t = re.sub(pat, rep, t)
    expr = t.strip()
    if not re.fullmatch(r"[\d\s+\-*/().%^sqrt]+", expr) or not re.search(r"\d", expr) or not re.search(r"[+\-*/%]|sqrt", expr):
        return None
    try:
        val = _safe_eval(ast.parse(expr.replace("^", "**"), mode="eval"))
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError):
        return None
    val = int(val) if float(val).is_integer() and abs(val) < 1e15 else round(val, 6)
    return val


def _say_num(v):
    return f"{v:,}" if isinstance(v, int) else (f"{v:,.4f}".rstrip("0").rstrip("."))


# ----------------------------------------------------------------------------- time / date
def time_date(text):
    t = text.lower()
    m = re.search(r"\btime\b.*\b(?:in|at)\s+([a-z][a-z ,'.-]+?)\s*[?.!]*$", t)
    if re.search(r"\bwhat(?:'s| is)? the time\b|\bwhat time is it\b|\bcurrent time\b|\bthe time now\b|\btell me the time\b|\bwhat time\b", t):
        if m:
            g = geocode(m.group(1))
            if g and g.get("tz"):
                now = datetime.now(ZoneInfo(g["tz"]))
                return f"It's {now.strftime('%-I:%M %p')} in {g['name']}, {now.strftime('%A')}."
            return None
        return f"It's {datetime.now().strftime('%-I:%M %p')}."
    if re.search(r"\bwhat(?:'s| is)? (?:the )?(?:date|day)\b|\bwhat day (?:is it|of the week)\b|\btoday'?s date\b|\bwhat is today\b|\bwhat'?s today\b", t):
        d = datetime.now()
        return f"Today is {d.strftime('%A')}, {d.day} {d.strftime('%B %Y')}."
    return None


# ----------------------------------------------------------------------------- weather
def _unit(imperial): return ("°F", "mph") if imperial else ("°C", "km/h")


def weather(text, persona):
    t = text.lower()
    if not re.search(r"\b(weather|forecast|temperature|how (?:hot|cold|warm)|raining|rain|umbrella|snowing|windy)\b", t):
        return None
    m = re.search(r"\b(?:in|for|at|near)\s+(.+?)(?:\s+(?:today|tomorrow|tonight|now|right now|this\s+\w+|next\s+\w+)\b)?\s*[?.!]*$", text.strip(), re.I)
    place = m.group(1).strip() if m and not re.fullmatch(r"(the|a|my)\b.*", m.group(1).strip(), re.I) else None
    imperial = settings.get("units") == "imperial" or (settings.get("units") is None and maps.imperial())
    from . import weather as wx
    if place:
        g = geocode(place)
        if not g:
            return {"speech": f"I couldn't find a place called {place}."}
        loc = (g["name"], g["lat"], g["lon"], g.get("label"))
    else:
        loc = wx.where()                                    # where you really are: the iPhone's GPS fix, the home area, Wi-Fi, then an IP guess
        if not loc:
            return {"speech": "I don't know where you are yet. Which town should I use?", "expect": "home", "retry": text}
    try:
        card = wx.card(*loc, imperial=imperial)
    except OSError:
        return {"speech": "I can't reach the weather service right now."}
    return {"speech": wx.summary(card, tomorrow=bool(re.search(r"\btomorrow\b", t))), "cards": [card]}


# ----------------------------------------------------------------------------- home area
def set_home(text):
    m = re.search(r"\b(?:set|change|update|make)?\s*(?:my )?home(?: area| town| city| location)?\s*(?:to|is|as|in|=)\s+(.+?)\s*[?.!]*$", text.strip(), re.I)
    if not m or not re.search(r"\bhome\b", text, re.I) or re.search(r"daddy|dad'?s home|welcome home", text, re.I):
        return None
    place = m.group(1).strip()
    g = geocode(place)
    if not g:
        return {"speech": f"I couldn't find {place}. Try the town and country."}
    settings.update(home_area=g["label"], home_lat=g["lat"], home_lon=g["lon"])
    return {"speech": f"Home area set to {g['label']}."}


# ----------------------------------------------------------------------------- opening things
def open_command(text):
    from . import opener
    return opener.parse_command(text)


# ----------------------------------------------------------------------------- volume, mute, timers (fast path: no brain)
_WORDN = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15,
          "twenty": 20, "thirty": 30, "forty": 40, "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90, "half": 0.5}


def _dur(text):
    """'5 minutes', 'two and a half hours', 'half an hour' -> seconds, or None."""
    t = text.lower().replace("-", " ")
    for a, b in (("half an hour", "30 minutes"), ("half a minute", "30 seconds"), ("quarter of an hour", "15 minutes"), ("an hour and a half", "90 minutes"), ("a minute and a half", "90 seconds")):
        t = t.replace(a, b)
    total, found = 0.0, False
    for m in re.finditer(r"(\d+(?:\.\d+)?|(?:forty five|fifteen|twenty|thirty|forty|fifty|sixty|ninety|ten|nine|eight|seven|six|five|four|three|two|one|an|a|half))\s*(?:and a half\s*)?(seconds?|secs?|minutes?|mins?|hours?|hrs?)", t):
        n = float(m.group(1)) if m.group(1)[0].isdigit() else _WORDN[m.group(1)]
        unit = m.group(2)[0]
        if "and a half" in m.group(0):
            n += 0.5
        total += n * (3600 if unit == "h" else 60 if unit == "m" else 1)
        found = True
    return int(total) if found and total > 0 else None


def volume_command(text):
    t = text.lower().strip(" .?!")
    m = re.search(r"\b(?:set |turn )?(?:the )?(?:volume|sound)(?: up| down)? (?:to|at) (\d{1,3}) ?(?:%|percent)?", t)
    if m:
        v = max(0, min(100, int(m.group(1))))
        return {"speech": f"Volume {v} percent.", "volume": {"set": v}}
    if re.search(r"\b(?:turn (?:it |the volume |the sound )?up|volume up|louder|raise the volume|increase the volume)\b", t):
        return {"speech": "Louder.", "volume": {"delta": 10}}
    if re.search(r"\b(?:turn (?:it |the volume |the sound )?down|volume down|quieter|lower the volume|decrease the volume|softer)\b", t):
        return {"speech": "Quieter.", "volume": {"delta": -10}}
    if re.search(r"\b(?:mute|silence) (?:the )?(?:sound|audio|volume|speakers?)\b", t):
        return {"speech": "Sound muted.", "volume": {"mute": True}}
    if re.search(r"\bunmute (?:the )?(?:sound|audio|volume|speakers?)\b", t):
        return {"speech": "Sound on.", "volume": {"mute": False}}
    if re.fullmatch(r"(?:please )?(?:mute|mute yourself|go deaf|stop listening)", t):
        return {"speech": "Muted. Press Super Alt M, or tap the mute icon, to wake me.", "mute": True}
    return None


def timer_command(text):
    t = text.lower().strip(" .?!")
    if re.search(r"\b(?:cancel|stop|clear|delete)\b.*\b(?:timers?|alarms?)\b", t):
        return {"speech": "Timers cancelled.", "timer": {"cancel": True}}
    m = re.search(r"\b(?:set|start|create|make)\s+(?:a |an |me a )?(?:timer|alarm)\b(?:\s+for|\s+of)?\s+(.+)$|\btimer for (.+)$", t)
    if m:
        secs = _dur(m.group(1) or m.group(2) or "")
        if secs:
            return {"speech": f"Timer set for {_say_dur(secs)}.", "timer": {"seconds": secs, "label": ""}}
    m = re.search(r"\bremind me (?:in|after) (.+?) (?:to|that|about) (.+)$", t)
    if m:
        secs = _dur(m.group(1))
        if secs:
            return {"speech": f"I'll remind you in {_say_dur(secs)}.", "timer": {"seconds": secs, "label": m.group(2)[:120]}}
    return None


def _say_dur(secs):
    h, rem = divmod(secs, 3600)
    mi, s = divmod(rem, 60)
    parts = [f"{h} hour{'s' if h != 1 else ''}"] if h else []
    if mi:
        parts.append(f"{mi} minute{'s' if mi != 1 else ''}")
    if s and not h:
        parts.append(f"{s} second{'s' if s != 1 else ''}")
    return " and ".join(parts) or "0 seconds"


def maps_geocode(q):
    from . import maps
    return maps.geocode(q)


def briefing():
    """Greeting and date, weather, reminders and timers, the top 5 headlines and the drive time to the usual place. Each piece is fetched in parallel with a 10 s cap and skipped if it fails."""
    from concurrent.futures import ThreadPoolExecutor
    from . import extras, maps
    now = datetime.now().astimezone()
    parts = [f"Good {'morning' if now.hour < 12 else 'afternoon' if now.hour < 18 else 'evening'}. It's {now.strftime('%A %-d %B')}."]
    cards = []

    def get_weather():
        return weather("what's the weather today", "jarvis")

    def get_news():
        return extras.headlines(5)

    def get_drive():
        la, lo, nm = settings.get("usual_lat"), settings.get("usual_lon"), settings.get("usual_place")
        me = maps.my_location()
        if not (maps.valid(la, lo) and me):
            return None
        r = maps.route("car", (me["lat"], me["lon"]), (la, lo))
        return f"The drive to {nm} is {maps.fmt_dur(r['dur_s'])}." if r else None

    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {"weather": ex.submit(get_weather), "news": ex.submit(get_news), "drive": ex.submit(get_drive)}
        res = {}
        for k, f in futs.items():
            try:
                res[k] = f.result(timeout=10)
            except Exception:
                res[k] = None                                          # a piece that fails or is slow is simply skipped
    w = res.get("weather")
    if w and w.get("speech"):
        parts.append(w["speech"]); cards += w.get("cards", [])
    pend = [p for p in extras.PENDING if p["due"] > time.time()]
    if pend:
        parts.append(f"You have {len(pend)} reminder{'s' if len(pend) != 1 else ''} or timer{'s' if len(pend) != 1 else ''} pending.")
        cards.append({"type": "list", "title": "Reminders", "items": [f"{datetime.fromtimestamp(p['due']):%-I:%M %p}  {p['label'] or 'timer'}" for p in pend]})
    if res.get("drive"):
        parts.append(res["drive"])
    news = res.get("news") or []
    if news:
        parts.append("Headlines: " + "; ".join(n.rstrip(".") for n in news[:3]) + ".")
        cards.append({"type": "list", "title": "Headlines", "items": news[:5]})
    return {"speech": " ".join(parts), "cards": cards, "briefing": True}


def maps_command(text):
    from . import maps
    r = maps.parse_command(text)
    if not r:
        return None
    kind = r["kind"]
    if kind == "ask_location":
        return {"speech": "Type your address on the card, or say it." if not r["today"] else "Type where you are on the card, or say it.", "expect": "settemp" if r["today"] else "setloc", "loccard": "today" if r["today"] else "home"}
    if kind == "set_location":
        return maps.apply_location(r["place"], r["today"])
    if kind == "sources":
        return maps.sources_card()
    if kind in ("places", "directions", "map") and maps.needs_confirm(maps.my_location()):          # "near me" on a rough IP guess: confirm it first
        me = maps.my_location()
        return {"speech": f"I only have a rough guess from your internet address: {me['label']}. Is that where you are? Say yes, or tell me where you are.", "expect": "confirmip", "retry": text}
    if kind == "map_ask":
        return {"speech": "Which place should I show?", "expect": "map"}
    if kind == "directions":
        maps.LAST["q"] = r["dest"]
        spec, speech = maps.directions_spec(r["dest"], r["mode"])
    elif kind == "places":
        maps.LAST["q"] = r["q"]
        spec, speech = maps.places_spec(r["q"])
        if not spec:
            spec, speech = maps.place_map_spec(r["q"])
    else:
        spec, speech = maps.place_map_spec(r["place"]) if r["place"] else (maps.new_spec(origin=maps.my_location(), title="Map"), "Here is the map.")
        if spec and not spec.get("origin") and not spec.get("dest"):
            return {"speech": "I don't know where you are yet. Tell me, for example: I'm in Miami Beach."}
    if not spec:
        return {"speech": speech}
    out = {"speech": speech, "open": {"action": "show_map", "id": spec["id"]}}
    if spec.get("places"):
        out["cards"] = [{"type": "list", "title": spec["title"], "items": [f"{p['name']}  ({maps.fmt_dist(p['dist_m'])})" for p in spec["places"][:8]]}]
    return out


def health_command(text):
    from . import health, opener
    r = health.parse_command(text)
    if not r:
        return None
    if r["kind"] == "report":
        sp, lines = health.report()
        return {"speech": sp, "cards": [{"type": "list", "title": "Machine", "items": lines}]}
    if r["kind"] == "free_ask":
        top = ", ".join(f"{n} {mb / 1024:.1f} gigabytes" if mb > 1024 else f"{n} {mb:.0f} megabytes" for n, mb in health.top_memory(3))
        return {"speech": f"The biggest users are {top}. I won't kill anything. Say yes, free memory, and I'll close the extra windows I opened and park my browser."}
    opener.clear_embed()
    opener.pool_park()
    return {"speech": "Done. I closed my extra windows and parked my browser."}


def prefs_command(text, persona="jarvis"):
    from . import prefs
    r = prefs.parse_command(text, persona)
    return {"speech": r["say"], "set": r.get("set", {})} if r else None


def visual_command(text):
    """Clear picture/video requests are answered by code in about a second, with no brain call: "show me pictures of X", "what does X look like", "show me a video of X", "show me how to X"."""
    low = text.lower().strip(" .?!")
    m = (re.fullmatch(r"(?:please )?(?:show|find|get|pull up|bring up|let me see|can i see|i want to see) (?:me )?(?:some |a few |a |an |the )?(?:pictures?|photos?|images?|pics?)(?: of| for)? (.+)", low)
         or re.fullmatch(r"(?:pictures?|photos?|images?) of (.+)", low) or re.fullmatch(r"what (?:does|do) (?:an? |the )?(.+?) look(?:s)? like", low))
    if m:
        from . import visuals
        what = re.sub(r"^(?:an? |the )", "", m.group(1).strip())
        card = visuals.gallery_card(what)
        return {"speech": f"Here are pictures of {what}.", "cards": [card]} if card else None
    m = (re.fullmatch(r"(?:please )?(?:show|play|find|let me see|can i see) (?:me )?(?:a |the |some )?(?:videos?|clips?|trailers?)(?: of| for| about)? (.+)", low)
         or re.fullmatch(r"(?:please )?(?:show|teach) me (how to .+)", low) or re.fullmatch(r"(?:please )?play (?:the )?(.+ trailer)", low))
    if m:
        return {"speech": "Here's a video.", "playvideo": m.group(1).strip()}
    return None


def send_command(text):
    """"send that to my phone/Mac/computer", "text me the address", "what did I send": sends only when your words say send/text/share, then logs it."""
    import re as _re
    from . import outbox
    low = text.lower().strip(" .?!")
    if _re.fullmatch(r"(?:what|which) (?:did i|have i|have you|did you) (?:send|sent)(?: (?:to )?(?:my )?(?:phone|mac|computer))?(?: today| lately| recently)?|show (?:me )?(?:the |my )?(?:outbox|sent items|what (?:you|i) sent)", low):
        return {"speech": "Here is what was sent.", "outbox": True}
    if _re.fullmatch(r"(?:please )?(?:resend|send again|try (?:sending )?again)(?: that)?", low):
        return {"speech": "Sending it again.", "resend": True}
    r = outbox.parse(text)
    if not r:
        return None
    return {"speech": "On it.", "send": r}


def mic_command(text):
    from . import micheck
    r = micheck.parse_command(text)
    return {"speech": "Listening to the microphone level." if r["kind"] == "test" else "Lowering the boost.", "mic": r["kind"]} if r else None


def calmail_command(text):
    """Calendar and email questions (read-only) and their setup cards."""
    from . import calmail
    r = calmail.parse_command(text)
    if r:
        return {"speech": "Here is the setup.", "calmail": r["which"]}
    a = calmail.ask(text) or calmail.ask_mail(text)
    if a:
        speech, lines = a
        return {"speech": speech, **({"cards": [{"type": "list", "title": "Calendar" if "alendar" in speech[:12] or "Today" in speech[:6] else "Email", "items": lines}]} if lines else {})}
    return None


def desk_command(text):
    from . import desk
    r = desk.parse_command(text)
    if not r:
        return None
    if r["kind"] == "tool":
        return {"speech": "", "tool": desk.TOOLS[r["tool"]]}
    if r["kind"] == "save":
        return {"speech": "", "layout_save": r["name"]}
    if r["kind"] == "open":
        lay = desk.get_layout(r["name"])
        return {"speech": f"Opening {r['name']}.", "layout_open": lay} if lay else {"speech": f"I don't have a layout called {r['name']}. Say: save this layout as {r['name']}."}
    names = desk.layout_names()
    return {"speech": ("Your layouts: " + ", ".join(names) + ".") if names else "You have no saved layouts yet."}


def phonecam_command(text):
    from . import phonecam
    r = phonecam.parse_command(text)
    if not r:
        return None
    return {"speech": "Here is the phone camera setup.", "phonecam": "setup"} if r["kind"] == "setup" else {"speech": "Using your phone camera for look.", "phonecam": "use"}


def meta_command(text):
    """Update, backup, restore, self-improve and publishing: all explicit requests, all confirmed with a button."""
    import re as _re
    from . import improve, updater
    r = updater.parse_command(text)
    if r:
        return {"speech": {"check": "Checking for updates.", "update": "Updating.", "backup": "Backing up.", "restore": "Restoring the newest backup."}[r["kind"]], "meta": r["kind"]}
    low = text.lower().strip(" .?!")
    if _re.fullmatch(r"(?:please )?(?:show me around|take the tour|give me a tour|how do i use (?:you|this|jarvis)|what can you do)", low):
        return {"speech": "Here is a quick tour.", "meta": "tour"}
    if _re.fullmatch(r"(?:please )?(?:set ?up|setup|connect|configure|open) (?:the |my )?(?:iphone|phone) (?:voice|shortcut|endpoint)|(?:hey jarvis|jarvis) (?:from|on) (?:my )?iphone(?: setup)?", low):
        return {"speech": "Here is the iPhone voice setup.", "meta": "voicecard"}
    if _re.fullmatch(r"(?:please )?(?:improve yourself|learn from your mistakes|fix yourself|get better)", low):
        return {"speech": "I'll look at where I went wrong and propose a fix. It takes a few minutes and changes nothing until you approve.", "meta": "improve"}
    if _re.fullmatch(r"(?:please )?apply (?:the |that )?(?:improvement|change|fix)", low):
        return {"speech": "Applying it.", "meta": "improve_apply"}
    if _re.fullmatch(r"(?:please )?(?:discard|drop|cancel|reject) (?:the |that )?(?:improvement|change|fix)", low):
        return {"speech": "Discarded.", "meta": "improve_discard"}
    if _re.fullmatch(r"(?:please )?(?:prepare|check) (?:a |the )?(?:public|github) (?:update|release)|(?:please )?publish (?:to github|the public repo(?:sitory)?)", low):
        return {"speech": "Preparing the public update and scanning it for private data.", "meta": "publish"}
    return None


def window_command(text):
    from . import opener
    return opener.window_command(text)


def spotify_command(text):
    """Spotify setup, and (once connected) play/search/pause/next/previous/volume/devices/what's playing. Plain "play X" falls back to YouTube Music when Spotify has no player."""
    from . import music, spotify, spotify_cli
    if spotify.parse_command(text):
        return {"speech": "Here are the steps to set up Spotify.", "spotify": "setup"}
    if not spotify.connected() or settings.get("spotify_premium") is False:
        return None
    import time as _t
    cmd = spotify_cli.parse(text)
    if cmd and cmd["op"] == "status" and music.STATE["active"] and not re.search(r"spotify", text, re.I):
        return None                                           # YouTube music is what is playing: its own handler answers
    if not cmd and _t.time() - spotify_cli.LAST["t"] < 2 * 3600 and not music.STATE["active"]:
        cmd = spotify_cli.parse_control(text)                 # pause / next / volume go to Spotify only while it is what you are listening to
    if not cmd:
        return None
    r = spotify_cli.run(cmd)
    if not r["ok"] and cmd["op"] == "play" and not cmd.get("explicit") and not r["speech"].lower().startswith(("i couldn't find", "spotify refused")):
        return None                                           # Spotify has no player or says no: the YouTube Music path plays it instead
    if r["ok"] and cmd["op"] == "play":
        music.STATE["active"] = False                         # Spotify is playing now, not the YouTube queue
    return {"speech": r["speech"], **({"cards": r["cards"]} if r.get("cards") else {})}


def sound_command(text):
    from . import soundtest
    r = soundtest.parse_command(text)
    return {"speech": "", "soundtest": True} if r else None


def idle_command(text):
    low = text.lower().strip(" .?!")
    if re.fullmatch(r"(?:please )?(?:show|start|go to|turn on) (?:me )?(?:the )?(?:idle screen|clock|clock screen|screensaver|screen saver)(?: now)?", low):
        return {"speech": "", "idle": True}
    return None


def quiet_command(text):
    from . import quiet
    r = quiet.parse_command(text)
    if not r:
        return None
    if r["kind"] == "set":
        settings.update(quiet_from=r["from"], quiet_to=r["to"])
        return {"speech": r["speech"]}
    if r["kind"] == "off":
        settings.update(quiet_from=None, quiet_to=None)
        return {"speech": r["speech"], "quiet_off": True}
    if r["kind"] == "focus":
        return {"speech": r["speech"], "focus": r["minutes"]}
    return {"speech": r["speech"], "focus": 0}


def instant_command(text):
    from . import instant
    return instant.parse_command(text)


def qr_command(text):
    from . import opener, qr, video
    q = qr.parse_command(text)
    if not q:
        return None
    url = q.get("url") or opener.EMBED.get("page_url") or video.LAST["url"]
    if not url:
        return {"speech": "There is no link open to make a code for. Open something first."}
    path = qr.make(url)
    if not path:
        return {"speech": "I can only make codes for secure web links, and this one isn't."}
    return {"speech": "Here is the QR code. Scan it with your phone.", "cards": [{"type": "qrcard", "file": path, "title": "QR code"}]}


def alerts_command(text):
    from . import phone
    if not phone.parse_command(text):
        return None
    new = phone.fetch_new()
    if new is None:
        return {"speech": "I couldn't reach your phone alerts, or the phone isn't set up yet."}
    if not new:
        return {"speech": "Nothing new."}
    spoken = "; ".join((n["title"] + ": " if n["title"] else "") + n["message"] for n in new[:4])
    return {"speech": f"You have {len(new)} new alert{'s' if len(new) != 1 else ''}. {spoken}"[:500], "cards": [{"type": "list", "title": "New alerts", "items": [((n["title"] + "  ") if n["title"] else "") + n["message"] for n in new]}]}


def news_command(text):
    from . import news
    o = news.open_command(text)
    if o:
        return {"speech": "", "open": {"action": "open_url", "url": o["link"], "label": o["title"][:40]}}          # "open that one": the page in the frame
    r = news.parse_command(text)
    if not r:
        return None
    c = news.card(r["topic"])
    if not c:
        return {"speech": "I couldn't reach the news feeds right now."}
    first = c["items"][0]
    return {"speech": f"{first['source']}: {first['title'].rstrip('.')}.", "cards": [c]}


WIDGETS = {"map": r"(?:the |my )?map(?: widget)?|where i am|where am i", "music": r"music(?: widget| player)?|the player|my music", "chat": r"chat(?: widget| window)?|the chat|terminal chat", "weather": r"(?:the )?weather(?: widget| card)?", "timers": r"(?:my |the )?(?:timers|reminders)(?: widget)?",
           "notes": r"(?:my |the )?notes(?: widget)?|(?:my |the )?(?:shopping |to-?do )?list", "today": r"(?:my |the )?(?:today|today's|day)(?: widget| card| view| summary)?|my day", "news": r"(?:the )?news(?: widget)?"}


def widget_command(text):
    """"show the music widget", "show chat", "show the weather", "show my timers", "show notes", "show today": one word each, no brain."""
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    m = re.fullmatch(r"(?:please )?(?:show|open|bring up|pull up|give me)(?: me)?(?: the)? (.+?)(?: widget)?", low)
    if not m:
        return None
    for name, pat in WIDGETS.items():
        if re.fullmatch(pat, m.group(1)) and name not in ("weather", "news", "timers"):          # (weather, news and reminders have their own richer commands)
            return {"speech": "", "widget": name}
    if m.group(1) in ("the music widget", "music widget", "the chat widget", "chat widget"):
        return {"speech": "", "widget": "music" if "music" in m.group(1) else "chat"}
    return None


def phone_find_command(text):
    from . import findphone
    return {"speech": "I'm sending a loud alert to your phone now.", "findphone": True} if findphone.parse_command(text) else None


def btspeaker_command(text):
    from . import btspeaker
    r = btspeaker.parse_command(text)
    if not r:
        return None
    return {"speech": "Here are the steps to use the Echo as a plain Bluetooth speaker. Nothing changes until you press yes.", "btspeaker": "setup"} if r["kind"] == "setup" else {"speech": "", "btspeaker": "back"}


def claudestat_command(text):
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    if re.fullmatch(r"(?:please )?(?:set up|setup|connect|install|show) (?:the )?(?:claude|claude code|codex)(?: code)? (?:status|hooks?|badge|notifications?)(?: badge)?", low):
        return {"speech": "Here is the exact change for Claude Code. Nothing is written until you do it.", "claudehook": True}
    return None


def presence_command(text):
    from . import presence
    r = presence.parse_command(text)
    return {"speech": "", "presence": r} if r else None


def sendfile_command(text):
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    m = re.fullmatch(r"(?:please )?(?:send|share|push|put|get)(?: me)?(?: the| my)? file (?:called |named )?(.+?) (?:to|onto|on) (?:my )?(mac|macbook|computer)", low)
    if m:
        return {"speech": "", "sendfile": m.group(1)}
    return None


def phonebrief_command(text):
    from . import notifier
    r = notifier.parse_command(text)
    if not r:
        return None
    settings.update(**{"brief_" + r["slot"]: r["time"], "notify_briefing": True} if r["time"] else {"brief_" + r["slot"]: None})
    return {"speech": f"Okay, a {r['slot']} briefing on your phone at {r['time']}." if r["time"] else f"The {r['slot']} briefing is off."}


def learn_command(text):
    from . import learn
    r = learn.parse_command(text)
    return {"speech": "", "learn": r} if r else None


def projects_command(text):
    from . import product, projects
    r = projects.parse_command(text)
    if r:
        return {"speech": "", "projects": r}
    r = product.parse_command(text)
    return {"speech": "", "product": r} if r else None


def settingsio_command(text):
    from . import settingsio
    r = settingsio.parse_command(text)
    if r:
        return {"speech": "", "settingsio": r}
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    if re.fullmatch(r"(?:please )?(?:run|do|start) (?:a |the )?self[- ]?test|(?:please )?(?:check|test) yourself|(?:please )?run (?:a )?(?:system )?check", low):
        return {"speech": "Checking everything.", "selftest": True}
    if re.fullmatch(r"(?:please )?back up my (?:jarvis )?data|(?:please )?make a backup(?: of my data)?", low):
        return {"speech": "", "backup": True}
    return None


def daylog_command(text):
    from . import daylog
    r = daylog.parse_command(text)
    return {"speech": "", "daylog": r} if r else None


def safety_command(text):
    """Things Jarvis never does, refused in code (no brain call): buying, ordering, paying, sending money, entering passwords or card numbers, and naming a real person from their face."""
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    if re.search(r"\b(?:buy|order|purchase|get me)\b (?:it|that|this|these|those|them|one|me .+|.+ (?:on|from|at) (?:amazon|best buy|ebay|walmart|target|aliexpress))$|\b(?:put|add)\b .*\b(?:cart|basket)\b|\bcheck ?out\b$|\bpay (?:for|the|my)\b|\b(?:send|wire|transfer|venmo|zelle|paypal)\b .*\b(?:money|dollars|bucks|\$\d+)|\b(?:enter|type|fill in|put in)\b .*\b(?:password|passcode|card number|credit card|cvv|pin)\b|\bplace (?:the |an |my )?order\b", low):
        return {"speech": "I don't buy, order or pay for anything, and I never enter passwords or card numbers. I can show you where to get it, with prices, and you do the rest. Say: where can I get one."}
    if re.search(r"\bwho(?:'s| is| are)? (?:this|that|he|she|they)\b.*\b(?:person|man|woman|guy|girl|lady|boy|kid)\b|\b(?:who|what)(?:'s| is) (?:the )?(?:person|man|woman|guy|girl) (?:in|on) (?:the |this )?(?:picture|photo|camera|frame|screen)\b|\bidentify (?:this |that |the )?(?:person|man|woman|face)\b|\bwhose face\b|\bwho is this\b$", low):
        return {"speech": "I don't identify people from their face. I can tell you what is visible: a person, their clothes, what they are holding."}
    return None


def scancode_command(text):
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    if re.fullmatch(r"(?:please )?(?:scan|read) (?:this |the |that )?(?:bar ?code|qr ?code|qr|code)(?: on (?:this|it|the box))?|(?:please )?what(?:'s| is) (?:this |that )?(?:bar ?code|qr ?code)", low):
        return {"speech": "Hold the code in front of the camera.", "scancode": True}
    return None


def textmsg_command(text):
    from . import textmsg
    r = textmsg.parse_command(text)
    return {"speech": "", "textmsg": r} if r else None


def whisper_command(text):
    from . import whisper_mode
    r = whisper_mode.parse_command(text)
    if not r:
        return None
    if r["kind"] == "on":
        return {"speech": "Whisper mode on.", "whisper": {"whisper": True}}
    if r["kind"] == "off":
        return {"speech": "Whisper mode off.", "whisper": {"whisper": False, "whisper_auto": False}}
    return {"speech": f"I'll whisper from {r['from']} to {r['to']}.", "whisper": {"whisper_auto": True, "whisper_from": r["from"], "whisper_to": r["to"]}}


def music_command(text):
    from . import music
    r = music.parse_command(text)
    if not r:
        return None
    kind = r["kind"]
    if kind == "play":
        sp, act = music.start(r["query"])
        return {"speech": sp, "open": act, "cards": [music.card()]} if act else {"speech": sp}
    if kind in ("next", "prev"):
        sp, act = music.step(1 if kind == "next" else -1)
        return {"speech": sp, "open": act, "cards": [music.card()]} if act else {"speech": sp}
    if kind == "what":
        return {"speech": music.now_playing()}
    if kind == "stop":
        music.STATE["active"] = False
        return {"speech": "Music stopped.", "open": {"action": "win_cmd", "cmd": "close", "type": "video"}}
    return {"speech": "Paused." if kind == "pause" else "Playing.", "open": {"action": "win_cmd", "cmd": "ypause" if kind == "pause" else "yplay", "type": "video"}}


def scene_command(text):
    from . import scenes
    r = scenes.parse_command(text)
    if not r:
        return None
    if r["kind"] == "run":
        return {"speech": f"{r['name'].capitalize()}.", "scene": r["name"]}
    if r["kind"] == "restore":
        return {"speech": "Back to normal.", "scene": "__restore__"}
    if r["kind"] == "list":
        n = list(scenes.load())
        return {"speech": "You have " + ", ".join(n) + "."}
    if r["kind"] == "delete":
        return {"speech": f"Deleted the {r['name']} scene." if scenes.delete_user(r["name"]) else f"I can't delete {r['name']}: it is built in or doesn't exist."}
    if not r["steps"]:
        return {"speech": "I couldn't make out any actions. Try: make a scene called study that sets volume to 30 and brightness to 60."}
    scenes.save_user(r["name"], r["steps"])
    skipped = f" I skipped: {'; '.join(r['bad'][:2])}." if r["bad"] else ""
    return {"speech": f"Saved the {r['name']} scene with {len(r['steps'])} step{'s' if len(r['steps']) != 1 else ''}. Say {r['name']} mode to run it.{skipped}"}


LANGS = {"spanish", "french", "german", "italian", "portuguese", "dutch", "japanese", "chinese", "mandarin", "cantonese", "korean", "russian", "arabic", "hindi", "greek", "turkish", "polish",
         "swedish", "norwegian", "danish", "finnish", "ukrainian", "vietnamese", "thai", "hebrew", "indonesian", "czech", "romanian", "hungarian", "latin", "english"}


def translate_command(text):
    low = text.lower().strip(" .?!")
    if re.fullmatch(r"(?:please )?(?:stop|end|quit|cancel|turn off) translat(?:ing|e|ion)(?: mode)?|(?:please )?translate mode off|(?:please )?that's enough translating", low):
        return {"speech": "Translate mode off.", "translate": None}
    m = re.fullmatch(r"(?:please )?(?:translate|start translating|begin translating|be my translator|translate everything i say)(?: it| everything| what i say)?(?: (?:to|into|in)) ([a-z]+)(?: for me)?(?: please)?", low)
    if m and m.group(1) in LANGS:
        lang = m.group(1).capitalize()
        return {"speech": f"Translating into {lang}. Say stop translating to end.", "translate": lang}
    return None


def read_command(text):
    from . import readscreen
    return readscreen.parse_command(text)


def memory_command(text):
    from . import memory
    return memory.parse_command(text)


def extras_command(text):
    from . import extras
    r = extras.parse_command(text)
    if not r:
        return None
    kind = r["kind"]
    if kind == "note":
        extras.add_note(r["text"])
        return {"speech": "Noted."}
    if kind in ("notes_search", "notes_recent"):
        hits = extras.search_notes(r["q"]) if kind == "notes_search" else extras.search_notes("", 0) or []
        if kind == "notes_recent":
            try:
                hits = open(extras.NOTES(), encoding="utf-8").read().splitlines()[-5:][::-1]
            except OSError:
                hits = []
        if not hits:
            return {"speech": "I found nothing in your notes."}
        spoken = "; ".join(re.sub(r"^\d{4}-\d\d-\d\d \d\d:\d\d\s+", "", h) for h in hits[:3])
        return {"speech": f"{len(hits)} note{'s' if len(hits) != 1 else ''}. " + spoken[:300], "cards": [{"type": "list", "title": "Notes", "items": hits}]}
    if kind == "note_delete":
        gone = extras.delete_last_note()
        return {"speech": f"Deleted the last note: {gone[:80]}." if gone else "There are no notes."}
    if kind == "list_add":
        parts = [x.strip() for x in re.split(r",|\band\b", r["item"]) if x.strip()]
        added = [x for x in parts if extras.list_add(x)]
        return {"speech": (f"Added {', '.join(added)} to your list." if added else "Already on your list.")}
    if kind == "list_read":
        it = extras.list_items()
        if not it:
            return {"speech": "Your list is empty."}
        return {"speech": f"{len(it)} item{'s' if len(it) != 1 else ''}: " + ", ".join(it[:10]) + ".", "cards": [{"type": "list", "title": "My list", "items": it}]}
    if kind == "list_clear":
        n = len(extras.list_items()); extras._save_list([])
        return {"speech": f"Cleared your list, {n} item{'s' if n != 1 else ''}." if n else "Your list was already empty."}
    if kind == "list_phone":
        it = extras.list_items()
        if not it:
            return {"speech": "Your list is empty, so there is nothing to send."}
        return {"speech": "Sending your list to your phone.", "phonesend": {"title": "My list", "message": "\n".join("- " + i for i in it)[:900]}}
    if kind == "reminders":
        return extras.list_reminders()
    if kind == "events":
        return {"speech": extras.say_events()}
    if kind == "speed":
        return {"speech": extras.speed_line(extras.HISTORY)}
    if kind == "alarm":
        return {"speech": (f"I'll remind you at {r['spoken']}." if r["label"] else f"Alarm set for {r['spoken']}."), "timer": {"seconds": r["secs"], "label": r["label"], "text": (f"Reminder: {r['label']}." if r["label"] else "Your alarm is going off.")}}
    if kind == "briefing_at":
        settings.update(briefing_time=r["hhmm"])
        return {"speech": f"I'll brief you every morning at {r['spoken']}, if the screen is on and I'm not muted."}
    if kind == "briefing_off":
        settings.update(briefing_time=None)
        return {"speech": "The daily briefing is off."}
    if kind == "usual_place":
        g = maps_geocode(r["place"])
        if not g:
            return {"speech": f"I couldn't find {r['place']}."}
        settings.update(usual_place=g["name"], usual_lat=g["lat"], usual_lon=g["lon"])
        return {"speech": f"Your usual place is {g['name']}. I'll include the drive time in your briefing."}
    if kind == "briefing":
        return briefing()
    if kind == "route":
        rt = extras.route_preview(r["dest"])
        if rt.get("error"):
            return {"speech": rt["error"]}
        extras.LAST_ROUTE.update(url=rt["url"], label=rt["label"], text=f"{rt['km']:.0f} km, about {rt['mins']} minutes by car")
        return {"speech": rt["speech"], "open": {"action": "open_url", "url": rt["embed"], "page_url": rt["url"], "label": "route to " + rt["label"]}}
    if kind == "route_phone":
        if not extras.LAST_ROUTE["url"]:
            return {"speech": "There is no route to send yet."}
        return {"speech": "Sending the route to your phone.", "phonesend": {"title": "Route to " + extras.LAST_ROUTE["label"], "message": extras.LAST_ROUTE["text"], "link": extras.LAST_ROUTE["url"]}}
    return None


def dictate_command(text):
    from . import dictate
    return dictate.parse_command(text)


def research_command(text):
    from . import research
    r = research.parse_command(text)
    if r:
        return {"speech": f"On it. I'll research that and tell you when it's ready.", "research": r["topic"]}
    return None


def video_command(text):
    from . import video
    r = video.parse_command(text)
    if not r:
        return None
    if r["kind"] == "gist":
        return {"speech": "Reading the video. One moment.", "video": r}
    if r["kind"] == "find":
        return {"speech": f"Looking for videos about {r['query']}.", "video": r}
    if r["kind"] == "page":
        return {"speech": "Reading the page.", "video": r}
    return {"speech": "Opening it." if not r["mac"] else "Sending it to your Mac.", "video": r}


def mac_command(text):
    from . import mac
    return mac.parse_command(text)


# ----------------------------------------------------------------------------- interface
def persona_command(text):
    """"switch to the coach", "be the captain", "next persona", "who can you be": the wake word, spoken name, voice and look switch together."""
    from . import personas
    low = re.sub(r"[^a-z ]", " ", text.lower())
    low = re.sub(r"\s+", " ", low).strip()
    if re.fullmatch(r"(?:please )?(?:list|show|what are|which|who can you be|what personas?(?: do you have)?|which personas?(?: do you have)?|show (?:me )?(?:the )?personas?)(?: your| the| my)? ?(?:personas?|characters?|voices)?", low) and re.search(r"person|character|who can you be", low):
        names = ", ".join(v["name"] for v in personas.PERSONAS.values())
        return {"speech": f"I can be {names}. Say, switch to, and a name.", "cards": [{"type": "list", "title": "Personas", "items": [f"{v['name']}: {v['prompt'].split(':')[0].replace('You are ', '').split(',', 1)[-1].strip() or v['name']}" for v in personas.PERSONAS.values()]}]}
    if re.fullmatch(r"(?:please )?(?:next|change|cycle) (?:the )?(?:persona|character|voice)", low):
        return {"speech": "", "persona": personas.cycle(_CUR["p"])}
    m = re.fullmatch(r"(?:please )?(?:switch|change|go|swap|turn)(?: over)? (?:to|into) (?:the |being )?(.{2,20})|(?:please )?(?:be|become|talk like|sound like|put on) (?:the )?(.{2,20})|(?:please )?(?:let me talk to|put me through to|get me) (?:the )?(.{2,20})|(?:please )?(?:use|activate|enable) (?:the )?(.{2,20}?)(?: persona| voice| character)", low)
    if m:
        pid = personas.find(next(g for g in m.groups() if g))
        if pid:
            return {"speech": "", "persona": pid}
    return None


_CUR = {"p": "jarvis"}


def markets_command(text):
    """The Markets Desk (read-only): the master routes the words to a seat; trading and advice requests get the no-trading / not-advice reply and nothing else happens."""
    from . import markets, mktdesk
    if not mktdesk.enabled():
        return None
    r = markets.route(text)
    res = mktdesk.answer(r, text) if r else None
    if res:
        res.setdefault("mkt", []).insert(0, {"op": "seat", "seat": r["seat"], "what": r["intent"].replace("_", " ")})
    return res


def holo_command(text):
    """3D models: "show me an atom in 3D", my scenes, compare, tour, auto 3D. A model that is open also takes part commands (select the piston, explode it...), handled in core."""
    from . import holo, net
    return holo.command(text, online=net.online)


def orb_command(text):
    """"move to the top", "move left", "put the orb in the middle", "reset the orb position": the small desktop orb moves (it is remembered; see orb.py)."""
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    if re.fullmatch(r"(?:please )?reset (?:the |my )?orb(?: position| location)?|(?:please )?put the orb back(?: where it was| in its place)?", low):
        return {"speech": "Orb position reset.", "orb": "reset"}
    m = re.fullmatch(r"(?:please )?move(?: the| my)?(?: orb| yourself)? (up|down|left|right)(?: a (?:bit|little))?(?: please)?", low)
    if m:
        return {"speech": "", "orb": {"left": "leftstep", "right": "rightstep"}.get(m.group(1), m.group(1))}
    m = re.fullmatch(r"(?:please )?(?:move|put|send|drag|take)(?: the| my)?(?: little| small)?(?: orb| circle| yourself)?(?: over)? (?:to|in|into|at)? ?(?:the )?(top|bottom|middle|centre|center|left|right|corner)(?: of the screen| side)?(?: please)?", low)
    if m:
        op = {"centre": "middle", "center": "middle"}.get(m.group(1), m.group(1))
        return {"speech": {"corner": "Back to the corner.", "middle": "To the middle."}.get(op, f"To the {op}."), "orb": op}
    return None


def buddy_command(text):
    """Recurring reminders ("remind me every weekday at 9 to stand up"), their list/pause/edit/delete, and promises (list, done, delete, auto-save)."""
    from . import buddy
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    m = re.fullmatch(r"(?:please )?(?:turn |switch )?(on|off) (?:the )?auto[- ]?save(?:ing)?(?: of)? promises|(?:please )?(start|stop) (?:saving|keeping) (?:my )?promises automatically", low)
    if m:
        on = (m.group(1) or m.group(2)) in ("on", "start")
        from . import settings
        settings.update(auto_promises=on)
        return {"speech": "I'll save promises automatically." if on else "I'll ask before saving a promise."}
    c = buddy.parse_command(text)
    if c and not (c["kind"] in ("manage", "edit") and c["which"].strip() in ("a", "an", "my", "the", "new", "another")):
        return buddy.run_command(c)                           # list / pause / resume / edit / delete first: "change the water reminder to every 3 hours" is an edit, not a new reminder
    if re.search(r"\bevery\b", low) and re.search(r"\b(?:remind|reminder|ping|nudge)\b", low) and buddy.secret(text):
        return {"speech": "I don't keep reminders with passwords, card numbers or codes."}
    r = buddy.parse_recurring(text)
    return buddy.add_recurring_reply(r) if r else None


def ui_command(text):
    t = text.lower()
    obj = r"(?:the |my )?(?:interface|ui|gui|controls|buttons|display|screen clutter)"
    if re.search(r"\b(?:hide|hi|high|hyde|height|remove|get rid of|clear|dismiss)\s+" + obj, t):
        return {"speech": "Hiding the interface. Tap the orb to bring it back.", "ui": {"hidden": True}}
    if re.search(r"\b(?:show|bring back|restore|unhide|display)\s+" + obj, t):
        return {"speech": "Here it is.", "ui": {"hidden": False}}
    return None


# ----------------------------------------------------------------------------- entry point
def handle(text, persona="jarvis"):
    """-> {"speech": str, "cards": [...]} when answered locally, else None."""
    if not text:
        return None
    _CUR["p"] = persona
    if persona != "jarvis" and re.fullmatch(r"(?:please )?(?:go )?back to jarvis|(?:please )?(?:be )?jarvis again", text.lower().strip(" .?!")):
        return {"speech": "", "persona": "jarvis"}               # as another persona, "go back to Jarvis" means the persona (as Jarvis it still means the window)
    for fn in (safety_command, textmsg_command, scancode_command, ui_command, persona_command, daylog_command, settingsio_command, projects_command, learn_command, phonebrief_command, sendfile_command, presence_command, claudestat_command, phone_find_command, btspeaker_command, whisper_command, widget_command, news_command, markets_command, holo_command, orb_command, buddy_command, lambda t: prefs_command(t, persona), meta_command, desk_command, calmail_command, mic_command, send_command, visual_command, phonecam_command, spotify_command, sound_command, idle_command, quiet_command, qr_command, instant_command, alerts_command, music_command, scene_command, translate_command, read_command, health_command, memory_command, maps_command, extras_command, dictate_command, research_command, video_command, window_command, open_command, mac_command, volume_command, timer_command, set_home, lambda t: weather(t, persona)):
        try:
            r = fn(text)
        except Exception as e:                        # network down etc: fall through to the brain
            print("skill failed:", fn, e)
            r = None
        if r:
            if r.get("persona"):
                from . import autopersona
                autopersona.named_by_user()                        # you chose a persona yourself: no automatic switching for a while
            return r
    try:
        td = time_date(text)
        if td:
            return {"speech": td}
    except Exception as e:
        print("time skill failed:", e)
    v = maths(text)
    if v is not None:
        return {"speech": f"That's {_say_num(v)}."}
    return None
