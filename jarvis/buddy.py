"""A buddy that keeps you on track: recurring reminders in plain speech, and promises ("I'll call mum tomorrow").

Recurring reminders  "remind me every weekday at 9 to stand up", "every 2 hours remind me to drink water", "every Sunday evening remind me to plan the week".
                     Listed on a REMINDERS card (and by voice); pause, resume, edit and delete by voice or from the card. Saved in data/recurring.json.
Promises             When you tell Jarvis what you will do ("I need to send that form by Friday") it offers to keep it (always asks the first time, then offers an
                     "auto-save promises" setting), nudges you when it is due and asks "done?". Saved in data/promises.json.
Nudges are gentle: spoken only when no video or call is on and it is not quiet hours, otherwise a small card. Nothing sensitive is stored (same rule as memory).
Pure functions plus two small JSON files: the speaking and scheduling live in core.py / webapp.py."""
import json
import re
import threading
import time
import uuid
from datetime import datetime, timedelta

from . import config, memory, settings

RECUR = config.DATA / "recurring.json"
PROMISES = config.DATA / "promises.json"
_lock = threading.RLock()
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
PART = {"morning": (9, 0), "afternoon": (14, 0), "evening": (18, 0), "night": (21, 0), "noon": (12, 0), "midnight": (0, 0)}
NUMW = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty five": 45, "forty-five": 45, "ninety": 90}
LAST_NUDGE = {"id": None, "t": 0.0}          # the promise just asked about: "done" within 10 minutes answers it


def _load(path):
    try:
        v = json.loads(path.read_text(encoding="utf-8"))
        return v if isinstance(v, list) else []
    except (OSError, ValueError):
        return []


def _save(path, items):
    path.parent.mkdir(exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=1, ensure_ascii=False)
    import os
    os.replace(tmp, path)


def secret(text):
    return memory.looks_secret(text) or bool(re.search(r"\b(?:password|passcode|pin number|card number|cvv|social security)\b|\d{6,}", text, re.I))


# ----------------------------------------------------------------------------- time words
def _num(s):
    s = s.strip().lower()
    if s.isdigit():
        return int(s)
    if s in ("half an", "half a", "half"):
        return 0.5
    return NUMW.get(s)


def parse_clock(s, default_pm=False):
    """"9", "9:30", "9 am", "8 pm", "noon", "evening" -> (hour, minute) or None."""
    s = s.lower()
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?|o'?clock)?", s)
    if m and (m.group(3) or m.group(2) or re.search(r"\bat\s+\d", s)):
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "")
        if ap == "pm" and h < 12:
            h += 12
        elif ap == "am" and h == 12:
            h = 0
        elif not ap and default_pm and 1 <= h <= 11:
            h += 12
        if h <= 23 and mi <= 59:
            return h, mi
    for w, hm in PART.items():
        if re.search(r"\b" + w + r"\b", s):
            return hm
    return None


def parse_schedule(s):
    """The part after "every": -> rule {"kind": "interval", "every": seconds} or {"kind": "daily", "at": "HH:MM", "days": [0-6]}, else None."""
    s = s.lower().strip(" .,")
    m = re.match(r"(?:every\s+)?(?:(other)\s+)?(?:(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen|twenty|thirty|forty[ -]five|ninety|half an?)\s+)?(second|minute|hour)s?\b", s)
    if m:
        n = _num(m.group(2) or "1")
        unit = {"second": 1, "minute": 60, "hour": 3600}[m.group(3)]
        secs = int(n * unit) if n else 0
        if secs < 60:
            return None                                       # recurring reminders: one minute at the shortest (one-off timers can be seconds)
        return {"kind": "interval", "every": secs}
    low = " " + s + " "
    days = None
    if re.search(r"\bweekdays?\b", low):
        days = [0, 1, 2, 3, 4]
    elif re.search(r"\bweekends?\b", low):
        days = [5, 6]
    elif re.search(r"\b(?:day|daily|morning|evening|night|afternoon)\b", low) and not any(d[:3] in low for d in DAYS):
        days = list(range(7))
    else:
        found = sorted({i for i, d in enumerate(DAYS) if re.search(r"\b" + d[:3] + r"(?:s|day|sday|nesday|rsday|urday)?s?\b", low)})
        days = found or None
    if not days:
        return None
    t = parse_clock(re.sub(r"\bevery\b", "", s), default_pm=bool(re.search(r"\b(?:evening|night)\b", s)) and False) or (9, 0)
    if re.search(r"\bat\s+\d{1,2}\s*$|\bat\s+\d{1,2}(?!:)\b(?!\s*(?:am|pm))", s) and not re.search(r"\b(?:am|pm|a\.m|p\.m)\b", s) and t[0] <= 6:
        t = (t[0] + 12, t[1])                                  # "at 5" with no am/pm: afternoon for small numbers
    return {"kind": "daily", "at": f"{t[0]:02d}:{t[1]:02d}", "days": days}


def describe(rule):
    if rule["kind"] == "interval":
        s = rule["every"]
        return "every hour" if s == 3600 else f"every {s // 3600} hours" if s % 3600 == 0 and s > 3600 else f"every {s // 60} minutes" if s >= 120 else "every minute"
    h, m = map(int, rule["at"].split(":"))
    at = f"{(h % 12) or 12}{(':%02d' % m) if m else ''} {'AM' if h < 12 else 'PM'}"
    d = rule["days"]
    if d == list(range(7)):
        when = "every day"
    elif d == [0, 1, 2, 3, 4]:
        when = "every weekday"
    elif d == [5, 6]:
        when = "every weekend"
    else:
        when = "every " + " and ".join(DAYS[i].capitalize() for i in d)
    return f"{when} at {at}"


def next_due(rule, after):
    """Epoch seconds of the next time this rule goes off after `after`."""
    if rule["kind"] == "interval":
        return after + rule["every"]
    h, m = map(int, rule["at"].split(":"))
    base = datetime.fromtimestamp(after)
    for add in range(0, 9):
        cand = (base + timedelta(days=add)).replace(hour=h, minute=m, second=0, microsecond=0)
        if cand.timestamp() > after + 1 and cand.weekday() in rule["days"]:
            return cand.timestamp()
    return after + 86400


# ----------------------------------------------------------------------------- recurring reminders
REM_WORDS = r"(?:remind(?:er)?|ping|nudge|alert|tell|prompt)"


def parse_recurring(text):
    """-> {"label", "rule"} or None."""
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    if not re.search(r"\bevery\b", low) or not re.search(r"\b" + REM_WORDS + r"\b", low):
        return None
    m = re.search(r"\bevery\b(.*?)(?=\s(?:remind|ping|nudge|tell|alert|prompt|to|that|about|please)\b|$)", low)
    if not m:
        return None
    sched = "every " + m.group(1).strip()
    # the time may follow a part-of-day or "at": keep "at 9" with the schedule even when "to" cut it short
    rule = parse_schedule(sched)
    if not rule:
        return None
    rest = (low[: m.start()] + " " + low[m.end():]).strip()
    lab = re.search(r"\b(?:to|that|about)\s+(.+)$", rest)
    label = lab.group(1) if lab else re.sub(r"\b(?:please|me|remind|reminder|ping|nudge|tell|alert|prompt|to|that|about)\b", " ", rest)
    label = re.sub(r"\s+", " ", label).strip(" ,.")
    label = re.sub(r"^me\s+", "", label)
    if rule["kind"] == "daily":
        label = re.sub(r"\bat\s+\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?\b", "", label).strip(" ,.")
    if not label or secret(label):
        return None
    return {"label": label[:120], "rule": rule}


def list_recurring():
    return _load(RECUR)


def add_recurring(label, rule):
    with _lock:
        items = _load(RECUR)
        items = [i for i in items if not (i["label"] == label and i["rule"] == rule)]
        item = {"id": uuid.uuid4().hex[:8], "label": label, "rule": rule, "paused": False, "next": next_due(rule, time.time()), "created": time.time()}
        items.append(item)
        _save(RECUR, items)
        return item


def find_item(items, words):
    words = [w for w in re.findall(r"[a-z]+", words.lower()) if w not in ("the", "my", "a", "reminder", "reminders", "to", "about", "that")]
    if not words:
        return None
    scored = sorted(((sum(w in i["label"].lower() for w in words), i) for i in items), key=lambda x: -x[0])
    return scored[0][1] if scored and scored[0][0] > 0 else None


def update_recurring(rid, **kw):
    with _lock:
        items = _load(RECUR)
        for i in items:
            if i["id"] == rid:
                i.update(kw)
                if "rule" in kw:
                    i["next"] = next_due(i["rule"], time.time())
                if kw.get("paused") is False:
                    i["next"] = next_due(i["rule"], time.time())
        _save(RECUR, items)


def delete_recurring(rid):
    with _lock:
        items = _load(RECUR)
        _save(RECUR, [i for i in items if i["id"] != rid])


def due_recurring(now=None):
    """The reminders that should go off now (and their next time is set). One that was missed by more than 10 minutes (Jarvis was off) is skipped, not replayed."""
    now = now or time.time()
    out = []
    with _lock:
        items = _load(RECUR)
        changed = False
        for i in items:
            if i.get("paused") or i["next"] > now:
                continue
            if now - i["next"] <= 600:
                out.append(dict(i))
            i["next"] = next_due(i["rule"], max(now, i["next"]))
            changed = True
        if changed:
            _save(RECUR, items)
    return out


# ----------------------------------------------------------------------------- promises
ACT = (r"call|phone|ring|text|message|email|e-mail|send|pay|buy|book|submit|finish|write|fix|clean|pick up|return|visit|order|renew|cancel|schedule|check|do|make|take|bring|water|feed|fill|file|sign|reply|answer|mail|post|upload|prepare|plan|study|practice|exercise|walk|tidy|wash|cook|organi[sz]e|update|print|drop off|get|go to|see|talk to|speak to|ask|tell|return|collect|apply|register|back up|backup")
PROMISE_RE = re.compile(r"^(?:ok(?:ay)?,? |right,? |so |and )?(?:i(?:'ll| will| need to| have to| must| should| gotta| got to| ought to|'m going to| am going to| promised to| promise to)|i've got to|don't let me forget to|i promise(?: to)?)\s+((?:" + ACT + r")\b.*)$", re.I)


def parse_when(s, now=None):
    """A due time from "tomorrow", "tonight", "by Friday", "in 3 hours", "next week", "on Monday at 5" -> (epoch, cleaned phrase) or (None, s)."""
    now = now or datetime.now()
    low = " " + s.lower() + " "
    due = None
    cut = None
    m = re.search(r"\b(?:in|after)\s+(\d+|a|an|one|two|three|four|five|six|ten|half an?)\s+(minute|hour|day|week)s?\b", low)
    if m:
        n = _num(m.group(1)) or 1
        due = now + timedelta(**{m.group(2) + "s": n})
        cut = m
    if due is None:
        m = re.search(r"\b(?:by|before|on|this|next|every)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", low)
        if m and not re.search(r"\bevery\b", m.group(0)):
            wd = DAYS.index(m.group(1))
            add = (wd - now.weekday()) % 7 or (7 if "next" in m.group(0) else 0)
            if add == 0 and now.hour >= 17:
                add = 7
            due = (now + timedelta(days=add)).replace(hour=9, minute=0, second=0, microsecond=0)
            cut = m
    if due is None:
        m = re.search(r"\b(tomorrow|tonight|today|this (?:morning|afternoon|evening)|next week|later)\b", low)
        if m:
            w = m.group(1)
            if w == "tomorrow":
                due = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
            elif w == "tonight":
                due = max(now.replace(hour=20, minute=0, second=0, microsecond=0), now + timedelta(minutes=30))
                if due.date() != now.date():
                    due = now + timedelta(minutes=30)
            elif w == "today":
                due = now.replace(hour=18, minute=0, second=0, microsecond=0)
            elif w == "next week":
                due = (now + timedelta(days=(7 - now.weekday()))).replace(hour=9, minute=0, second=0, microsecond=0)
            elif w == "later":
                due = now + timedelta(hours=3)
            else:
                h = {"morning": 9, "afternoon": 14, "evening": 18}[w.split()[-1]]
                due = now.replace(hour=h, minute=0, second=0, microsecond=0)
            cut = m
    if due is None:
        m = re.search(r"\bat\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.))", low)             # "at 6 pm" with no day: today (tomorrow if it has passed)
        if m:
            hm = parse_clock(m.group(0))
            if hm:
                due = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
                cut = m
                if due <= now:
                    due += timedelta(days=1)
                phrase = re.sub(r"\s+", " ", low[: m.start()] + " " + low[m.end():]).strip(" ,.")
                return due.timestamp(), phrase
        return None, s
    at = re.search(r"\bat\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?)", low) or re.search(r"\b(?:in the )?(morning|afternoon|evening)\b", low)
    if at and not re.search(r"\b(?:in|after)\s+\w+\s+(?:minute|hour)", low):
        hm = parse_clock(at.group(0), default_pm=False)
        if hm:
            h = hm[0] + (12 if not re.search(r"am|pm|a\.m|p\.m|morning|afternoon|evening", at.group(0)) and hm[0] < 8 else 0)
            due = due.replace(hour=h % 24, minute=hm[1])
    if due <= now:
        due += timedelta(days=1)
    phrase = re.sub(r"\s+", " ", (low[: cut.start()] + " " + low[cut.end():]) if cut else low).strip()
    phrase = re.sub(r"\b(?:in the |this )?(?:morning|afternoon|evening)\b", "", phrase)
    phrase = re.sub(r"\b(?:at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?|in the (?:morning|afternoon|evening)|by|before|on)\s*$", "", phrase).strip(" ,.")
    return due.timestamp(), phrase


def detect_promise(text):
    """"I'll call mum tomorrow" -> {"label": "Call mum", "due": epoch or None} or None. Questions, long sentences and anything sensitive are not promises."""
    t = re.sub(r"\s+", " ", (text or "").strip(" .!"))
    if not t or t.endswith("?") or len(t.split()) > 18 or secret(t):
        return None
    m = PROMISE_RE.match(t)
    if not m:
        return None
    body = m.group(1)
    due, phrase = parse_when(body)
    label = (phrase or body).strip(" ,.")
    label = re.sub(r"\b(?:my|the)\b\s+(?=\w)", lambda x: x.group(0), label)
    if len(label) < 3 or re.match(r"(?:know|see if|be able)\b", label):
        return None
    return {"label": label[0].upper() + label[1:][:119], "due": due}


def when_words(due):
    if not due:
        return "no due time"
    d = datetime.fromtimestamp(due)
    n = datetime.now()
    day = "today" if d.date() == n.date() else "tomorrow" if d.date() == (n + timedelta(days=1)).date() else d.strftime("%A")
    return f"{day} at {d.strftime('%-I:%M %p')}".replace(":00", "")


def list_promises(open_only=True):
    return [p for p in _load(PROMISES) if not (open_only and p.get("done"))]


def add_promise(label, due):
    with _lock:
        items = _load(PROMISES)
        for p in items:
            if p["label"].lower() == label.lower() and not p.get("done"):
                p["due"] = due
                _save(PROMISES, items)
                return p
        item = {"id": uuid.uuid4().hex[:8], "label": label, "due": due, "done": False, "nudges": 0, "last_nudge": 0, "created": time.time()}
        items.append(item)
        _save(PROMISES, items)
        return item


def update_promise(pid, **kw):
    with _lock:
        items = _load(PROMISES)
        for p in items:
            if p["id"] == pid:
                p.update(kw)
        _save(PROMISES, items)


def delete_promise(pid):
    with _lock:
        _save(PROMISES, [p for p in _load(PROMISES) if p["id"] != pid])


def due_promises(now=None):
    """Open promises that are due and should be nudged now: first at the due time, then again every 2 hours, at most 3 times."""
    now = now or time.time()
    out = []
    with _lock:
        items = _load(PROMISES)
        for p in items:
            if p.get("done") or not p.get("due") or p["due"] > now or p["nudges"] >= 3 or now - p["last_nudge"] < 7200:
                continue
            p["nudges"] += 1
            p["last_nudge"] = now
            out.append(dict(p))
        if out:
            _save(PROMISES, items)
    return out


def find_promise(words):
    return find_item(list_promises(), words)


# ----------------------------------------------------------------------------- voice commands
def parse_command(text):
    """-> {"kind": ..., ...} for reminder / promise management by voice, else None. (Adding a recurring reminder is parse_recurring.)"""
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    if re.fullmatch(r"(?:please )?(?:show|list|what are|what's|what is|open|read)(?: me)?(?: all)? (?:of )?(?:my |the )?(?:recurring |repeating )?reminders?(?: list)?|what reminders do i have|what do i have to remind", low):
        return {"kind": "list_reminders"}
    if re.fullmatch(r"(?:please )?(?:show|list|what are|what's|what is|open|read)(?: me)?(?: all)? (?:of )?(?:my |the )?(?:promises|things i(?:'ve)? promised|commitments)(?: list)?|what did i promise(?: to do)?|what have i promised|what do i owe(?: people)?", low):
        return {"kind": "list_promises"}
    m = re.fullmatch(r"(?:please )?(pause|mute|stop|resume|unpause|restart|delete|remove|cancel|forget)(?: the| my)? (.+?) reminder", low)
    if m:
        verb = {"pause": "pause", "mute": "pause", "stop": "pause", "resume": "resume", "unpause": "resume", "restart": "resume"}.get(m.group(1), "delete")
        return {"kind": "manage", "verb": verb, "which": m.group(2)}
    m = re.fullmatch(r"(?:please )?(?:change|move|edit|reschedule|update|set)(?: the| my)? (.+?) reminder (?:to|for) (.+)", low)
    if m:
        return {"kind": "edit", "which": m.group(1), "to": m.group(2)}
    m = re.fullmatch(r"(?:please )?(?:delete|remove|forget|cancel|drop) (?:the |my )?promise(?: to| about)? (.+)", low)
    if m:
        return {"kind": "promise_delete", "which": m.group(1)}
    if re.fullmatch(r"(?:please )?(?:pause|stop) (?:all )?(?:my )?(?:reminders|nudges)", low):
        return {"kind": "pause_all"}
    if re.fullmatch(r"(?:please )?(?:resume|restart|start) (?:all )?(?:my )?(?:reminders|nudges)", low):
        return {"kind": "resume_all"}
    if time.time() - LAST_NUDGE["t"] < 600 and LAST_NUDGE["id"]:
        if re.fullmatch(r"(?:yes,? )?(?:i(?:'ve| have)? )?(?:done|did it|did that|finished|completed|it's done|all done|yes|yeah|yep)(?: it| that| already)?", low):
            return {"kind": "done", "id": LAST_NUDGE["id"]}
        if re.fullmatch(r"(?:not yet|later|snooze|remind me later|in a bit|not now|no)(?: please)?", low):
            return {"kind": "snooze", "id": LAST_NUDGE["id"]}
    m = re.fullmatch(r"(?:please )?(?:mark |i(?:'ve| have)? )?(?:done|did|finished|completed|called|sent|paid|booked|bought)(?: the)?(?: promise)?(?: to)? (.+)|i(?:'ve| have) (?:done|finished|completed) (?:the |my )?(.+)", low)
    if m and find_promise(m.group(1) or m.group(2) or ""):
        return {"kind": "done", "id": find_promise(m.group(1) or m.group(2))["id"]}
    return None


def reminders_card():
    """The REMINDERS card: recurring reminders with PAUSE / DELETE per row, one-off timers listed under them."""
    from . import extras
    steps = []
    for n, i in enumerate(list_recurring(), 1):
        nxt = datetime.fromtimestamp(i["next"]).strftime("%a %-I:%M %p")
        steps.append({"n": n, "text": f"{i['label']}  ·  {describe(i['rule'])}" + ("  (paused)" if i.get("paused") else f"  ·  next {nxt}"),
                      "buttons": [{"label": "RESUME" if i.get("paused") else "PAUSE", "action": "rem_resume" if i.get("paused") else "rem_pause", "payload": {"id": i["id"]}},
                                  {"label": "DELETE", "action": "rem_delete", "payload": {"id": i["id"]}}]})
    now = time.time()
    for p in sorted((p for p in extras.PENDING if p["due"] > now), key=lambda p: p["due"]):
        steps.append({"n": len(steps) + 1, "text": f"{p.get('label') or 'Timer'}  ·  at {datetime.fromtimestamp(p['due']):%-I:%M:%S %p}", "buttons": []})
    if not steps:
        steps = [{"n": 1, "text": 'No reminders yet. Say: "remind me every weekday at 9 to stand up".', "buttons": []}]
    return {"type": "guide", "gid": "reminders", "title": "REMINDERS", "steps": steps, "buttons": []}


def promises_card():
    steps = []
    for n, p in enumerate(list_promises(), 1):
        steps.append({"n": n, "text": f"{p['label']}  ·  {when_words(p.get('due'))}",
                      "buttons": [{"label": "DONE", "action": "prom_done", "payload": {"id": p["id"]}}, {"label": "LATER", "action": "prom_snooze", "payload": {"id": p["id"]}},
                                  {"label": "DELETE", "action": "prom_delete", "payload": {"id": p["id"]}}]})
    if not steps:
        steps = [{"n": 1, "text": 'No open promises. Tell me what you will do, like: "I\'ll call mum tomorrow".', "buttons": []}]
    return {"type": "guide", "gid": "promises", "title": "PROMISES", "steps": steps, "buttons": [{"label": "AUTO-SAVE " + ("ON" if settings.get("auto_promises") else "OFF"), "action": "prom_auto", "payload": {}}]}


def run_command(cmd):
    """Execute a parse_command() result -> {"speech", "cards"?}."""
    k = cmd["kind"]
    if k == "list_reminders":
        from . import extras
        n = len(list_recurring())
        if not n and any(p["due"] > time.time() for p in extras.PENDING):
            return {"speech": extras.list_reminders()["speech"], "cards": [reminders_card()]}
        return {"speech": f"You have {n} recurring reminder{'s' if n != 1 else ''}." if n else "You have no recurring reminders.", "cards": [reminders_card()]}
    if k == "list_promises":
        ps = list_promises()
        return {"speech": (f"You have {len(ps)} open promise{'s' if len(ps) != 1 else ''}: " + "; ".join(p["label"] for p in ps[:3]) + ".") if ps else "You have no open promises.", "cards": [promises_card()]}
    if k == "manage":
        it = find_item(list_recurring(), cmd["which"])
        if not it:
            return {"speech": f"I don't have a reminder about {cmd['which']}."}
        if cmd["verb"] == "delete":
            delete_recurring(it["id"])
            return {"speech": f"Deleted the {it['label']} reminder.", "cards": [reminders_card()]}
        update_recurring(it["id"], paused=cmd["verb"] == "pause")
        return {"speech": f"{'Paused' if cmd['verb'] == 'pause' else 'Resumed'} the {it['label']} reminder.", "cards": [reminders_card()]}
    if k == "edit":
        it = find_item(list_recurring(), cmd["which"])
        if not it:
            return {"speech": f"I don't have a reminder about {cmd['which']}."}
        rule = parse_schedule(cmd["to"] if cmd["to"].startswith("every") else "every " + cmd["to"])
        if not rule and it["rule"]["kind"] == "daily":
            hm = parse_clock(cmd["to"])
            if hm:
                rule = dict(it["rule"], at=f"{hm[0]:02d}:{hm[1]:02d}")
        if not rule:
            return {"speech": "I didn't catch the new time. Try: change the water reminder to every 3 hours."}
        update_recurring(it["id"], rule=rule)
        return {"speech": f"Okay, the {it['label']} reminder is now {describe(rule)}.", "cards": [reminders_card()]}
    if k == "promise_delete":
        p = find_promise(cmd["which"])
        if not p:
            return {"speech": "I don't have that promise."}
        delete_promise(p["id"])
        return {"speech": f"Dropped: {p['label']}.", "cards": [promises_card()]}
    if k in ("pause_all", "resume_all"):
        for i in list_recurring():
            update_recurring(i["id"], paused=(k == "pause_all"))
        return {"speech": "All reminders paused." if k == "pause_all" else "All reminders resumed.", "cards": [reminders_card()]}
    if k == "done":
        update_promise(cmd["id"], done=True)
        LAST_NUDGE["id"] = None
        return {"speech": "Nice. Marked done."}
    if k == "snooze":
        update_promise(cmd["id"], due=time.time() + 3600, nudges=0, last_nudge=0)
        LAST_NUDGE["id"] = None
        return {"speech": "Okay, I'll ask again in an hour."}
    return {"speech": ""}


def add_recurring_reply(parsed):
    item = add_recurring(parsed["label"], parsed["rule"])
    return {"speech": f"Okay, {describe(item['rule'])}: {item['label']}.", "cards": [reminders_card()]}
