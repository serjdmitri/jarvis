"""Calendar and email, read-only. Calendar: the private iCal address of your calendar (Google Calendar: Settings > your calendar > "Secret address in iCal format"), kept in ~/.config/jarvis/calendar_url (mode 600).
Email: IMAP with an APP PASSWORD (not your real password), kept in ~/.config/jarvis/mail_user and mail_pass (mode 600). Jarvis only READS headers (sender, subject): it never sends, deletes, moves or marks mail
as read, and the address, password and calendar URL are never printed, logged or shown again after saving."""
import datetime as dt
import email
import email.header
import imaplib
import os
import re
import time
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from . import settings

CFG = Path(settings.PATH).parent
_cache = {"t": 0.0, "text": ""}
DAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _w600(name, text):
    CFG.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(CFG / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text.strip() + "\n")


def _r(name):
    try:
        return (CFG / name).read_text().strip() or None
    except OSError:
        return None


def save_calendar(url):
    u = (url or "").strip().replace("webcal://", "https://")
    if not re.fullmatch(r"https://[^\s]{10,500}", u) or ".ics" not in u and "ical" not in u.lower() and "calendar" not in u.lower():
        return False, "That does not look like a calendar address. It should be the private iCal link (it starts with https:// and usually ends with .ics)."
    _w600("calendar_url", u)
    _cache.update(t=0.0, text="")
    return True, "Calendar address saved privately."


# ----------------------------------------------------------------------------- iCal
def _unfold(text):
    return re.sub(r"\r?\n[ \t]", "", text).splitlines()


def _parse_dt(val, params):
    tz = None
    m = re.search(r"TZID=([^;:]+)", params or "")
    if m:
        try:
            tz = ZoneInfo(m.group(1))
        except Exception:
            tz = None
    if len(val) == 8:                                                  # all-day
        d = dt.datetime.strptime(val, "%Y%m%d")
        return d.astimezone() if False else d.replace(tzinfo=None), True
    z = val.endswith("Z")
    d = dt.datetime.strptime(val.rstrip("Z"), "%Y%m%dT%H%M%S")
    if z:
        d = d.replace(tzinfo=dt.timezone.utc).astimezone().replace(tzinfo=None)
    elif tz:
        d = d.replace(tzinfo=tz).astimezone().replace(tzinfo=None)
    return d, False


def parse_ics(text):
    """-> [{"start": datetime(local, naive), "allday", "title", "rrule": dict|None, "end"}]"""
    out, cur = [], None
    for ln in _unfold(text):
        if ln.startswith("BEGIN:VEVENT"):
            cur = {}
        elif ln.startswith("END:VEVENT") and cur is not None:
            if "start" in cur and cur.get("status") != "CANCELLED":
                out.append(cur)
            cur = None
        elif cur is not None and ":" in ln:
            k, v = ln.split(":", 1)
            name, _, params = k.partition(";")
            if name == "DTSTART":
                try:
                    cur["start"], cur["allday"] = _parse_dt(v, params)
                except ValueError:
                    pass
            elif name == "DTEND":
                try:
                    cur["end"], _ = _parse_dt(v, params)
                except ValueError:
                    pass
            elif name == "SUMMARY":
                cur["title"] = v.replace("\\,", ",").replace("\\n", " ")[:80]
            elif name == "STATUS":
                cur["status"] = v.strip()
            elif name == "RRULE":
                cur["rrule"] = dict(p.split("=", 1) for p in v.split(";") if "=" in p)
    return out


def occurrences(ev, lo, hi):
    """Start times of one event inside [lo, hi) (simple DAILY/WEEKLY/MONTHLY/YEARLY rules, INTERVAL, COUNT, UNTIL, BYDAY for weekly)."""
    s, r = ev["start"], ev.get("rrule")
    if not r:
        return [s] if lo <= s < hi else []
    freq, iv = r.get("FREQ"), int(r.get("INTERVAL", 1) or 1)
    until = None
    if r.get("UNTIL"):
        try:
            until = _parse_dt(r["UNTIL"], "")[0]
        except ValueError:
            pass
    count, out, n, cur = int(r["COUNT"]) if r.get("COUNT") else None, [], 0, s
    byday = [DAYS[d[-2:]] for d in r.get("BYDAY", "").split(",") if d[-2:] in DAYS]
    for _ in range(4000):
        cands = [cur]
        if freq == "WEEKLY" and byday:
            mon = (cur - dt.timedelta(days=cur.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            cands = [mon + dt.timedelta(days=d, hours=s.hour, minutes=s.minute) for d in sorted(byday)]
        for c in cands:
            if c < s:
                continue
            if until and c > until:
                return out
            n += 1
            if count and n > count:
                return out
            if lo <= c < hi:
                out.append(c)
        if cur >= hi and all(c >= hi for c in cands):
            break
        if freq == "DAILY":
            cur += dt.timedelta(days=iv)
        elif freq == "WEEKLY":
            cur += dt.timedelta(weeks=iv)
        elif freq == "MONTHLY":
            m_ = cur.month - 1 + iv
            try:
                cur = cur.replace(year=cur.year + m_ // 12, month=m_ % 12 + 1)
            except ValueError:
                cur += dt.timedelta(days=30 * iv)
        elif freq == "YEARLY":
            try:
                cur = cur.replace(year=cur.year + iv)
            except ValueError:
                cur += dt.timedelta(days=365 * iv)
        else:
            break
    return out


def events_between(text, lo, hi):
    out = []
    for ev in parse_ics(text):
        for o in occurrences(ev, lo, hi):
            out.append({"start": o, "allday": ev.get("allday", False), "title": ev.get("title", "Busy")})
    return sorted(out, key=lambda e: e["start"])


def fetch():
    url = _r("calendar_url")
    if not url:
        return None
    if time.time() - _cache["t"] < 300 and _cache["text"]:
        return _cache["text"]
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "jarvis-local"}), timeout=12) as r:
        _cache.update(t=time.time(), text=r.read(3_000_000).decode("utf-8", "ignore"))
    return _cache["text"]


def say_time(d, allday):
    return "all day" if allday else d.strftime("%-I:%M %p").lower()


def describe(evs, label):
    if not evs:
        return f"Nothing on your calendar {label}."
    parts = [f"{e['title']} {('at ' + say_time(e['start'], False)) if not e['allday'] else '(all day)'}" for e in evs[:6]]
    return f"{label.capitalize()}: " + "; ".join(parts) + ("." if len(evs) <= 6 else f"; and {len(evs) - 6} more.")


def ask(text, now=None):
    """Voice question -> (speech, [card lines]) or None if it is not a calendar question."""
    low = text.lower().strip(" .?!")
    now = now or dt.datetime.now()
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    m = re.fullmatch(r"(?:what(?:'s| is)|show me|read me|tell me) (?:on )?(?:my )?(?:calendar|schedule|agenda|diary)(?: (today|tomorrow|this week|tonight))?|what do i have (today|tomorrow|this week|tonight)|what(?:'s| is) (?:happening|on) (today|tomorrow|this week)|do i have (?:anything|any meetings?|any events?) (today|tomorrow|this week)", low)
    nxt = re.fullmatch(r"(?:what(?:'s| is) )?(?:my )?next (?:meeting|event|appointment|thing on my calendar)", low)
    if not (m or nxt):
        return None
    text_ics = fetch()
    if text_ics is None:
        return ("Your calendar is not connected. Say: connect my calendar.", [])
    if nxt:
        evs = [e for e in events_between(text_ics, now, now + dt.timedelta(days=14)) if not e["allday"]]
        if not evs:
            return ("Nothing coming up in the next two weeks.", [])
        e = evs[0]
        when = "today" if e["start"].date() == now.date() else ("tomorrow" if e["start"].date() == (now + dt.timedelta(days=1)).date() else e["start"].strftime("%A"))
        return (f"Your next one is {e['title']}, {when} at {say_time(e['start'], False)}.", [f"{e['start'].strftime('%a %H:%M')}  {e['title']}"])
    when = next(g for g in m.groups() if g) if any(m.groups()) else "today"
    if when == "tomorrow":
        lo, hi = day0 + dt.timedelta(days=1), day0 + dt.timedelta(days=2)
    elif when == "this week":
        lo, hi = now, day0 + dt.timedelta(days=7)
    elif when == "tonight":
        lo, hi = now.replace(hour=17, minute=0), day0 + dt.timedelta(days=1)
    else:
        lo, hi = now, day0 + dt.timedelta(days=1)
    evs = events_between(text_ics, lo if when != "today" else day0, hi)
    if when == "today":
        evs = [e for e in evs if e["allday"] or e["start"] >= now - dt.timedelta(hours=1)]
    return (describe(evs, when), [f"{e['start'].strftime('%a %H:%M') if not e['allday'] else e['start'].strftime('%a') + ' all day'}  {e['title']}" for e in evs[:10]])


def next_label():
    """For the clock screen: "NEXT: STAND-UP AT 9:30 AM" or None."""
    try:
        t = fetch()
        if not t:
            return None
        now = dt.datetime.now()
        evs = [e for e in events_between(t, now, now + dt.timedelta(hours=24)) if not e["allday"]]
        return {"label": evs[0]["title"], "at": say_time(evs[0]["start"], False)} if evs else None
    except Exception:
        return None


# ----------------------------------------------------------------------------- email (read-only)
HOSTS = {"gmail.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com", "outlook.com": "outlook.office365.com", "hotmail.com": "outlook.office365.com", "live.com": "outlook.office365.com",
         "icloud.com": "imap.mail.me.com", "me.com": "imap.mail.me.com", "yahoo.com": "imap.mail.yahoo.com"}


def save_mail(user=None, password=None):
    if user:
        u = user.strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", u) or u.split("@")[1].lower() not in HOSTS:
            return False, "I only know Gmail, Outlook, iCloud and Yahoo addresses for now."
        _w600("mail_user", u)
        return True, "Address saved privately."
    if password:
        p = re.sub(r"\s+", "", password)
        if len(p) < 8:
            return False, "That is too short for an app password."
        _w600("mail_pass", p)
        return True, "Saved privately. I will only read sender and subject lines."
    return False, "nothing to save"


def _dec(h):
    try:
        return str(email.header.make_header(email.header.decode_header(h or ""))).strip()
    except Exception:
        return (h or "").strip()


def parse_header(raw):
    m = email.message_from_bytes(raw)
    frm = _dec(m.get("From", ""))
    name = re.sub(r"\s*<[^>]*>", "", frm).strip(' "') or frm
    return {"from": name[:40], "subject": _dec(m.get("Subject", ""))[:80]}


def unseen(limit=5, sender=None):
    """-> list of {"from","subject"} (newest first) or None if not configured. Read-only: BODY.PEEK, readonly mailbox, no flags changed."""
    user, pw = _r("mail_user"), _r("mail_pass")
    if not (user and pw):
        return None
    c = imaplib.IMAP4_SSL(HOSTS[user.split("@")[1].lower()], timeout=15)
    try:
        c.login(user, pw)
        c.select("INBOX", readonly=True)
        crit = ["UNSEEN"] + (["FROM", f'"{sender}"'] if sender else [])
        typ, data = c.search(None, *crit)
        ids = data[0].split()[-limit:][::-1]
        out = []
        for i in ids:
            typ, d = c.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            out.append(parse_header(d[0][1]))
        total = len(data[0].split())
        return {"items": out, "total": total}
    finally:
        try:
            c.logout()
        except Exception:
            pass


def ask_mail(text):
    low = text.lower().strip(" .?!")
    m = re.fullmatch(r"(?:any|do i have any|have i got any|read my|check my|what(?:'s| is) (?:in )?my) (?:new |unread )?(?:e-?mails?|inbox|messages)", low) or re.fullmatch(r"any new (?:e-?mails?|messages)", low)
    f = re.fullmatch(r"(?:any|is there an?) (?:new )?(?:e-?mails?|messages?) from (.+)", low)
    if not (m or f):
        return None
    try:
        r = unseen(5, f.group(1) if f else None)
    except Exception as e:
        return (f"I couldn't read your email ({type(e).__name__}). Check the app password.", [])
    if r is None:
        return ("Your email is not connected. Say: connect my email.", [])
    if not r["items"]:
        return ("No new email." if not f else f"No new email from {f.group(1)}.", [])
    sp = f"{r['total']} new email{'s' if r['total'] != 1 else ''}. " + " ".join(f"From {i['from']}: {i['subject']}." for i in r["items"][:3])
    return (sp, [f"{i['from']}: {i['subject']}" for i in r["items"]])


def setup_card(which):
    if which == "calendar":
        return {"type": "guide", "gid": "cal", "title": "Connect my calendar", "full": True, "paged": False, "secret": True, "status": "", "steps": [
            {"n": 1, "text": "Get your calendar's private iCal address.", "hint": "Google Calendar: Settings > click your calendar > Integrate calendar > \"Secret address in iCal format\". Anyone with that link can read your calendar, so do not share it."},
            {"n": 2, "text": "Paste it here. It is stored privately and never shown again:", "input": {"placeholder": "https://calendar.google.com/calendar/ical/.../basic.ics", "label": "SAVE", "action": "cal_save"}}]}
    return {"type": "guide", "gid": "mail", "title": "Connect my email (read-only)", "full": True, "paged": False, "secret": True, "status": "", "steps": [
        {"n": 1, "text": "I only READ sender and subject lines. I never send, delete, move or mark anything as read.", "hint": "Use an APP PASSWORD (Gmail: Google Account > Security > 2-Step Verification > App passwords; Outlook and iCloud have the same). Never your real password."},
        {"n": 2, "text": "Your email address:", "input": {"placeholder": "your email address", "label": "SAVE", "action": "mail_user"}},
        {"n": 3, "text": "The app password (stored privately, never shown again):", "input": {"placeholder": "xxxx xxxx xxxx xxxx", "label": "SAVE", "action": "mail_pass"}}]}


def parse_command(text):
    low = text.lower().strip(" .?!")
    if re.fullmatch(r"(?:please )?(?:connect|set ?up|link|add) (?:my )?(?:google )?calendar", low):
        return {"kind": "setup", "which": "calendar"}
    if re.fullmatch(r"(?:please )?(?:connect|set ?up|link|add) (?:my )?(?:e-?mail|inbox|gmail)", low):
        return {"kind": "setup", "which": "mail"}
    return None
