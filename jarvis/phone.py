"""Send things to the user's phone through ntfy.sh (no account; the topic name acts like a password).

* The topic is 32 random URL-safe characters kept in <config dir>/jarvis/ntfy_topic (mode 600), outside every git repo.
  It is never printed, logged, committed or put in a card/message/exception text. Only `bin/jarvis-phone topic` prints it,
  and only to the terminal it is run from.
* The brain can only *ask* (a structured {"phone": {title, message, link?, map?, image?}} block). This module validates and
  builds the request; the assistant only runs it when the user's own words asked for a send.
* Nothing is sent without being asked. There is no automatic test send: `bin/jarvis-phone test` is run by the user."""
import base64
import os
import re
import secrets
import stat
import time
import urllib.parse
import urllib.request

from . import opener

SERVER = "https://ntfy.sh"
SEND_INTENT = re.compile(r"\b(send|text|message|notify|ping|remind|put)\b.*\b(phone|me|iphone)\b|\b(text|send|remind) me\b|\bsend (?:that|this|it)\b", re.I)
_last = 0.0


def _dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "jarvis")


def topic_path():
    return os.path.join(_dir(), "ntfy_topic")


def get_topic(create=True):
    p = topic_path()
    try:
        t = open(p).read().strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{24,}", t):
            return t
    except OSError:
        pass
    if not create:
        return None
    os.makedirs(_dir(), mode=0o700, exist_ok=True)
    t = secrets.token_urlsafe(24)                       # 32 characters, 192 bits
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(t + "\n")
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    return t


def _hdr(s):
    """HTTP header value; non-ASCII becomes an RFC 2047 word, which ntfy decodes."""
    s = re.sub(r"[\r\n]+", " ", s)
    return s if s.isascii() else "=?UTF-8?B?" + base64.b64encode(s.encode()).decode() + "?="


def validate(p):
    """-> {"title","message","click","attach"} or None."""
    if not isinstance(p, dict):
        return None
    title = re.sub(r"\s+", " ", str(p.get("title") or "From Jarvis")).strip()[:80]
    msg = str(p.get("message") or "").strip()[:1000]
    if not msg and not p.get("link") and not p.get("map"):
        return None
    click = None
    if p.get("map"):
        click = "https://www.google.com/maps/search/" + urllib.parse.quote(str(p["map"])[:200])
        msg = msg or str(p["map"])[:200]
    if p.get("link"):
        click = opener.clean_url(str(p["link"]))
        if not click:
            return None
    attach = opener.clean_url(str(p["image"])) if p.get("image") else None
    return {"title": title, "message": msg or click, "click": click, "attach": attach}


def send(p):
    """Validate and POST. Returns {"ok": bool, "message": str}; the message never contains the topic or the URL."""
    global _last
    v = validate(p)
    if not v:
        return {"ok": False, "message": "nothing valid to send"}
    if time.time() - _last < 4:
        return {"ok": False, "message": "too many sends; wait a few seconds"}
    topic = get_topic()
    headers = {"Title": _hdr(v["title"]), "Priority": "default", "User-Agent": "jarvis-local", "Tags": "jarvis"}      # tagged, so "anything new?" never reads our own sends back to you
    if v["click"]:
        headers["Click"] = v["click"]
    if v["attach"]:
        headers["Attach"] = v["attach"]
    req = urllib.request.Request(f"{SERVER}/{topic}", data=v["message"].encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            ok = 200 <= r.status < 300
    except Exception as e:                              # never include str(e): urllib messages can carry the URL
        return {"ok": False, "message": "could not reach ntfy (" + type(e).__name__ + ")"}
    _last = time.time()
    return {"ok": ok, "message": "sent" if ok else "ntfy refused it"}


# ----------------------------------------------------------------------------- "anything new?": read alerts that arrived on the phone topic
SEEN_PATH = None


def _seen_file():
    return os.path.join(_dir(), "ntfy_seen")


def fetch_new(limit=8):
    """Messages on the topic since the last time you asked (first time: the last 24 hours), excluding the ones Jarvis sent itself (tag 'jarvis').
    -> [{"title","message","time"}] or None if it could not be fetched. The topic is used inside the request only, never printed or logged."""
    import json as _json
    topic = get_topic(create=False)
    if not topic:
        return None
    try:
        since = int(open(_seen_file()).read().strip())
    except (OSError, ValueError):
        since = int(time.time()) - 86400
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{SERVER}/{topic}/json?poll=1&since={since}", headers={"User-Agent": "jarvis-local"}), timeout=8) as r:
            lines = r.read().decode("utf-8", "ignore").splitlines()
    except Exception as e:                                 # never str(e): it can carry the URL
        print("[phone] could not read alerts:", type(e).__name__, flush=True)
        return None
    out, newest = [], since
    for ln in lines:
        try:
            ev = _json.loads(ln)
        except ValueError:
            continue
        if ev.get("event") != "message":
            continue
        newest = max(newest, int(ev.get("time", 0)))
        if "jarvis" in (ev.get("tags") or []):
            continue
        out.append({"title": (ev.get("title") or "").strip()[:80], "message": (ev.get("message") or "").strip()[:200], "time": int(ev.get("time", 0))})
    try:
        os.makedirs(_dir(), exist_ok=True)
        open(_seen_file(), "w").write(str(newest + 1))
    except OSError:
        pass
    return out[-limit:]


def parse_command(text):
    low = text.lower().strip(" .?!")
    if re.fullmatch(r"anything new(?: on my phone)?|any new (?:alerts|notifications|messages)(?: on my phone)?|read (?:me )?my (?:phone )?(?:alerts|notifications)|check my phone(?: notifications| alerts)?|what(?:'s| is) new on my phone", low):
        return {"kind": "alerts"}
    return None
