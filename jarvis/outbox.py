"""Everything Jarvis sends to your Mac or phone, in one tidy log (data/outbox.jsonl, local only): what, where, when and how it ended (sent / opened on the Mac / dropped with a reason / failed),
with RESEND for failures, and a retry when the network was down. Also works out what "that" means in "send that to my phone"."""
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from . import config

LOG = config.DATA / "outbox.jsonl"
_lock = threading.Lock()


def _read():
    try:
        return [json.loads(x) for x in LOG.read_text().splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


def _write(rows):
    LOG.parent.mkdir(exist_ok=True)
    LOG.write_text("\n".join(json.dumps(r) for r in rows[-200:]) + "\n")


def log(to, label, status, detail="", payload=None, msgid=None):
    """-> record id."""
    with _lock:
        rows = _read()
        rid = (rows[-1]["id"] + 1) if rows else 1
        rows.append({"id": rid, "t": int(time.time()), "to": to, "label": (label or "")[:80], "status": status, "detail": (detail or "")[:120], "payload": payload, "msgid": msgid})
        _write(rows)
    return rid


def update(rid, status, detail=""):
    with _lock:
        rows = _read()
        for r in rows:
            if r["id"] == rid:
                r["status"], r["detail"] = status, detail[:120]
        _write(rows)


def sync_acks():
    """Mac replies (signed acks) -> statuses: a message that was only 'sent' becomes 'opened on the Mac' or 'dropped: reason'."""
    from . import mac
    with _lock:
        rows = _read()
        for r in rows:
            if r["to"] == "mac" and r["status"] == "sent" and r.get("msgid") in mac.STATE["acks"]:
                res = mac.STATE["acks"][r["msgid"]]
                r["status"], r["detail"] = ("opened" if res == "opened" else "dropped"), ("" if res == "opened" else res[len("dropped: "):])
        _write(rows)


def recent(n=8):
    sync_acks()
    return _read()[-n:][::-1]


def get(rid):
    return next((r for r in _read() if r["id"] == rid), None)


ICON = {"sent": "SENT", "opened": "OPENED", "dropped": "NOT OPENED", "failed": "FAILED", "retrying": "RETRYING"}


def lines(n=8):
    out = []
    for r in recent(n):
        when = time.strftime("%H:%M", time.localtime(r["t"]))
        out.append(f"{when}  to your {r['to']}: {r['label']}  [{ICON.get(r['status'], r['status'].upper())}{(': ' + r['detail']) if r['detail'] else ''}]")
    return out


def retry_later(fn, rid, delays=(20, 60, 180)):
    """A send that failed because the network was down is tried again after 20 s, 1 min and 3 min (in the background)."""
    def go():
        for d in delays:
            time.sleep(d)
            update(rid, "retrying", f"trying again in a moment")
            ok, why = fn()
            if ok:
                update(rid, "sent", "after a retry")
                return
        update(rid, "failed", "gave up after 3 retries")
    update(rid, "retrying", "network down, will retry")
    threading.Thread(target=go, daemon=True).start()


# ---------------------------------------------------------------------------- what is "that"?
SEND = re.compile(r"\b(?:send|text|share|forward|push|put|get|email|drop)\b", re.I)
DEST = re.compile(r"\b(?:to|onto|on|for) (?:my |the )?(mac(?:book)?|computer|laptop|pc|phone|iphone|cell|mobile)\b|\b(?:my |the )(mac(?:book)?|computer|laptop|phone|iphone)\b", re.I)
WHAT = [("look", r"\b(?:look|camera|what you (?:saw|see)|the picture|the photo|the frame|the image)\b"), ("map", r"\b(?:map|route|directions?|way there)\b"), ("report", r"\b(?:report|research|findings)\b"),
        ("note", r"\b(?:notes?|list|shopping list)\b"), ("video", r"\b(?:video|song|music)\b"), ("link", r"\b(?:link|page|site|website|article|url)\b"), ("clipboard", r"\b(?:clipboard|what i copied)\b"),
        ("answer", r"\b(?:answer|what you said|the text|that message|your reply)\b")]


def parse(text):
    """"send the route to my phone", "text me the address", "share that with my computer" -> {"to": "mac"|"phone", "what": ...} or None. Only with a send word."""
    low = text.lower().strip(" .?!")
    if not SEND.search(low):
        return None
    m = DEST.search(low)
    to = None
    if m:
        w = next(g for g in m.groups() if g)
        to = "phone" if w in ("phone", "iphone", "cell", "mobile") else "mac"
    elif re.search(r"\b(?:text|message) me\b|\bsend me\b", low):
        to = "phone"
    if not to:
        return None
    what = next((k for k, pat in WHAT if re.search(pat, low)), "current")
    if what == "current" and re.search(r"\b(?:the address|my location|where i am)\b", low):
        what = "address"
    return {"to": to, "what": what}


def resolve(a, what, to):
    """-> {"kind": "url"|"file"|"text", "value", "label"} or None. `a` is the assistant (for the open look view, the last answer...)."""
    from . import opener, research, video
    def look():
        p = a.look.get("path") if a.look.get("open") else None
        return {"kind": "file", "value": p, "label": "the look picture"} if p and os.path.exists(p) else None
    def song():
        from . import spotify_cli
        u = spotify_cli.now_url()
        return {"kind": "url", "value": u, "label": "the song"} if u else None
    def page():
        from . import maps
        sp = maps.SPECS.get(maps.LAST_SPEC["id"]) if maps.LAST_SPEC["id"] and time.time() - maps.LAST_SPEC["t"] < 3600 else None
        if to == "phone" and sp and sp.get("dest"):                              # the directions: an Apple Maps link opens in Maps on the iPhone
            o, d = sp.get("origin"), sp["dest"]
            return {"kind": "url", "value": maps.apple_link((o["lat"], o["lon"]) if o else None, (d["lat"], d["lon"]), sp.get("mode", "car")), "label": "the directions to " + str(d.get("label", "there"))[:40]}
        u = opener.EMBED.get("page_url") or video.LAST.get("url")
        return {"kind": "url", "value": u, "label": ("the video" if video.LAST.get("url") == u else "the page")} if u else None
    def report():
        if to == "mac" and research.LAST.get("path"):
            return {"kind": "file", "value": research.LAST["path"], "label": "the research report"}
        if research.LAST.get("summary"):
            return {"kind": "text", "value": research.LAST["summary"][:900], "label": "the research summary", "link": research.LAST.get("link")}
    def answer():
        t = a.recent[-1][1] if a.recent else ""
        return {"kind": "text", "value": t[:900], "label": "my last answer"} if t else None
    def clip():
        try:
            t = subprocess.run(["wl-paste", "--no-newline"], capture_output=True, text=True, timeout=3).stdout.strip()
        except Exception:
            t = ""
        return {"kind": "text", "value": t[:1500], "label": "the clipboard"} if t else None
    def note():
        p = config.ROOT / "notes.txt"
        from . import extras
        try:
            t = open(extras.NOTES_PATH).read()[-1500:] if hasattr(extras, "NOTES_PATH") else ""
        except OSError:
            t = ""
        return {"kind": "text", "value": t, "label": "your notes"} if t.strip() else None
    order = {"look": [look], "map": [page], "video": [song, page], "link": [page], "report": [report], "answer": [answer], "clipboard": [clip], "note": [note], "address": [page, answer],
             "current": [look, page, report, answer]}[what]
    for f in order:
        r = f()
        if r:
            return r
    return None


def text_to_note_file(text, label):
    """The Mac opens links and files, not bare text: a short sentence becomes a .txt note it can open."""
    d = config.ROOT / "notes"
    d.mkdir(exist_ok=True)
    p = d / f"send-{int(time.time())}.txt"
    p.write_text(text + "\n")
    return str(p)
