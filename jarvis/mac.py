"""Send links and files to the user's Mac (it opens them there). The Mac side is mac/jarvis_inbox.py (outbound only, nothing listens).

Secrets: the 24-character pairing code is shown ON SCREEN ONLY (a card that removes itself); it is never spoken, logged, printed or written to
disk. Only the derived ntfy topic and HMAC key are saved, in <config dir>/jarvis/mac.json (mode 600, outside git). The code is kept in RAM so
"show the pairing code" can show it again until the server restarts. Nothing here ever prints the code, topic or key.
The brain only *asks* ({"mac": {"url": ...}} or {"mac": {"file": ...}}); this module validates and signs."""
import hashlib
import json
import os
import re
import stat
import time
import urllib.parse
import urllib.request

from . import config, jarvis_pair as jp, opener

SERVER = "https://ntfy.sh"
EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mov", ".m4v", ".webm", ".pdf", ".txt", ".md"}
MAX_BYTES = 15 * 1024 * 1024         # the Mac side allows 25 MB, but the free ntfy.sh server only accepts attachments up to 15 MB
SEND_INTENT = re.compile(r"\b(?:send|share|text|forward|push|message)\b.*\bmac(?:book)?\b|\bmac(?:book)?\b.*\b(?:send|share|text)\b", re.I)
_code_ram = None                      # the last pairing code, in memory only
_last = 0.0


def _path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "jarvis", "mac.json")


def paired():
    return _load() is not None


def _load():
    try:
        d = json.load(open(_path()))
        return d["topic"], bytes.fromhex(d["key"])
    except Exception:
        return None


def _ack_topic():
    try:
        return json.load(open(_path())).get("ack_topic")
    except Exception:
        return None


def pair():
    """Make a new pairing code (replacing any earlier pairing), save only the derived topic + key, return the code for the screen."""
    global _code_ram
    code = jp.new_code()
    topic, key = jp.derive(code)
    os.makedirs(os.path.dirname(_path()), mode=0o700, exist_ok=True)
    fd = os.open(_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"topic": topic, "key": key.hex(), "ack_topic": jp.ack_topic(code)}, f)
    os.chmod(_path(), stat.S_IRUSR | stat.S_IWUSR)
    _code_ram = code
    return code


def current_code():
    return _code_ram


def code_card(code):
    return {"type": "text", "title": "Pairing code (on your Mac: python3 jarvis_inbox.py pair)", "text": code, "secret": True}


MAC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mac")


def _host():
    import socket
    h = socket.gethostname().split(".")[0]
    return (h + ".local") if h else "the-go.local"


def guide_card(code):
    """ONE card with big plain steps. The code is shown only here (card.secret: it removes itself) and is never logged or saved."""
    user = os.environ.get("USER") or "user"
    return {"type": "guide", "gid": "mac", "title": "Pair your Mac", "full": True, "secret": True, "code": code, "paged": False,
            "steps": [
                {"n": 1, "text": "On your Mac, open Terminal.", "hint": "Press Cmd + Space, type Terminal, press Enter."},
                {"n": 2, "text": "Have this project's mac folder on your Mac (clone the repository or download its ZIP once).", "hint": "I can't see your Mac, so I can't know where that folder is. To find it, paste this and it prints the path of jarvis_inbox.py:",
                 "cmd": 'find ~ -maxdepth 5 -name jarvis_inbox.py 2>/dev/null | head -1'},
                {"n": 3, "text": "Paste this one command, then type the code below when it asks (it stays hidden as you type):",
                 "cmd": 'cd "$(dirname "$(find ~ -maxdepth 5 -name jarvis_inbox.py 2>/dev/null | head -1)")" && python3 jarvis_inbox.py pair && python3 jarvis_inbox.py run', "showCode": True,
                 "hint": "It finds the folder itself. If it says no such folder, use the path from step 2 instead. To get newer Mac files: git pull in your clone, or download the ZIP again."},
                {"n": 4, "text": "Optional: make it start by itself every time you log in. Open a new Terminal tab and paste:", "cmd": 'cd "$(dirname "$(find ~ -maxdepth 5 -name jarvis_inbox.py 2>/dev/null | head -1)")" && python3 jarvis_inbox.py install', "optional": True}],
            "status": "waiting for the Mac", "buttons": [{"label": "RETRY", "action": "mac_retry"}, {"label": "SEND TEST TO MY MAC", "action": "mac_test"}]}


# ---------------------------------------------------------------------------- is the Mac there? (its signed hello / ack arrive on the same private topic)
STATE = {"connected": 0.0, "ack": 0.0, "watching": False, "error": "", "acks": {}, "last_id": None}
_seen = set()


def _poll(since):
    """-> list of verified replies from the Mac since `since`, read from the second private topic. Topic is used inside the request only."""
    keys, at = _load(), _ack_topic()
    if not keys or not at:
        STATE["error"] = "this pairing has no reply channel: say pair my Mac again"
        return []
    key = keys[1]
    out = []
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{SERVER}/{at}/json?poll=1&since={int(since)}", headers={"User-Agent": "jarvis-local"}), timeout=10) as r:
            lines = r.read().decode("utf-8", "ignore").splitlines()
    except Exception as e:
        STATE["error"] = "could not reach ntfy (" + type(e).__name__ + ")"
        return out
    STATE["error"] = ""
    for ln in lines:
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if ev.get("event") != "message":
            continue
        p = jp.verify(key, ev.get("message", ""), _seen)
        if p and p.get("type") in ("hello", "ack"):
            out.append(p)
    return out


def watch(notify, minutes=10):
    """Background: tells `notify(state, text)` when the Mac says hello ('connected'), acknowledges a message ('delivered'), or never turns up ('waiting')."""
    if STATE["watching"]:
        return
    STATE["watching"] = True
    since, end = time.time() - 5, time.time() + minutes * 60
    try:
        while time.time() < end and paired():
            for p in _poll(since):
                if p["type"] == "hello":
                    STATE["connected"] = time.time(); notify("connected", "Mac connected")
                elif p["type"] == "ack":
                    r = str(p.get("result", ""))[:120]
                    STATE["acks"][str(p.get("id"))] = r
                    STATE["ack"] = time.time()
                    if r == "opened":
                        notify("delivered", "Delivered: your Mac opened it")
                    else:
                        notify("dropped", "Mac did not open it: " + (r[len("dropped: "):] if r.startswith("dropped: ") else r))
            since = time.time() - 30
            if STATE["error"]:
                notify("error", "Can't reach the message service: " + STATE["error"] + ". Check the Go's internet.")
            time.sleep(3)
        if not STATE["connected"]:
            notify("waiting", "Still waiting for the Mac. Check steps 2 and 3, then press RETRY.")
    finally:
        STATE["watching"] = False


def post_main(payload):
    """A signed message from the Go to the Mac on the main topic (replies to an ask). Never prints the topic."""
    keys = _load()
    if not keys:
        return False
    topic, key = keys
    try:
        urllib.request.urlopen(urllib.request.Request(f"{SERVER}/{topic}", data=jp.sign(key, payload).encode(), method="POST", headers={"User-Agent": "jarvis-local", "Tags": "jarvis"}), timeout=10).read()
        return True
    except Exception as e:
        print("[mac] reply failed:", type(e).__name__, flush=True)
        return False


def send_test():
    """A harmless link to open on the Mac. -> bool sent (delivery is reported by the watcher when the Mac acknowledges)."""
    return send({"url": "https://www.wikipedia.org/", "label": "test link"})


# ---------------------------------------------------------------------------- allowed files
def _allowed_dirs():
    out = [config.SHOTS_DIR, config.ROOT / "run", config.ROOT / "notes", os.path.expanduser("~/Downloads/Jarvis")]
    return [os.path.realpath(str(d)) for d in out]


def check_file(path):
    """-> real path or None. Only files Jarvis made (shots, notes, run/ downloads) with an allowed type and size."""
    try:
        real = os.path.realpath(os.path.expanduser(str(path)))
        if not any(real == d or real.startswith(d + os.sep) for d in _allowed_dirs()):
            return None
        if os.path.splitext(real)[1].lower() not in EXT or not os.path.isfile(real) or not 0 < os.path.getsize(real) <= MAX_BYTES:
            return None
        return real
    except (OSError, ValueError):
        return None


def validate(p):
    """{"url":...} or {"file":...} -> {"kind": "url"|"file", ...} or None."""
    if not isinstance(p, dict):
        return None
    if p.get("url"):
        u = opener.clean_url(str(p["url"]))
        if not u or not u.startswith("https://"):
            return None
        return {"kind": "url", "url": u, "label": str(p.get("label") or urllib.parse.urlsplit(u).hostname)[:60]}
    if p.get("share"):                                     # any file: served once on the local network, fetched and checked by the Mac (never opened there)
        from . import fileshare
        try:
            info = fileshare.stage(p["share"], p.get("name"))
        except fileshare.Refused as e:
            return {"kind": "refused", "why": str(e)}
        return {"kind": "share", "info": info, "label": info["name"]}
    if p.get("file"):
        f = check_file(p["file"])
        return {"kind": "file", "file": f, "label": os.path.basename(f)} if f else None
    return None


# ---------------------------------------------------------------------------- sending
def build(v, key):
    """-> (signed message text, file path or None)."""
    if v["kind"] == "share":
        i = v["info"]
        return jp.sign(key, {"type": "fetch", "url": i["url"], "name": i["name"], "size": i["size"], "sha256": i["sha256"]}), None
    if v["kind"] == "url":
        return jp.sign(key, {"type": "open_url", "url": v["url"]}), None
    h = hashlib.sha256(open(v["file"], "rb").read()).hexdigest()
    return jp.sign(key, {"type": "open_file", "name": os.path.basename(v["file"]), "sha256": h}), v["file"]


def send(p):
    """Validate, sign and send. Returns {"ok", "message", "label"}; the message never contains the code, topic or key."""
    global _last
    keys = _load()
    if not keys:
        return {"ok": False, "message": "not paired"}
    v = validate(p)
    if v and v["kind"] == "refused":
        return {"ok": False, "message": v["why"]}
    if not v:
        return {"ok": False, "message": "nothing valid to send (https links only, or a file Jarvis made: image, video, PDF or text under 15 MB)"}
    if time.time() - _last < 3:
        return {"ok": False, "message": "too many sends; wait a few seconds"}
    topic, key = keys
    try:
        text, path = build(v, key)
        if path is None:
            req = urllib.request.Request(f"{SERVER}/{topic}", data=text.encode(), method="POST", headers={"User-Agent": "jarvis-local"})
        else:
            req = urllib.request.Request(f"{SERVER}/{topic}", data=open(path, "rb").read(), method="PUT",
                                         headers={"Filename": os.path.basename(path), "Message": text, "User-Agent": "jarvis-local"})
        with urllib.request.urlopen(req, timeout=60 if path else 10) as r:
            ok = 200 <= r.status < 300
    except Exception as e:                      # never str(e): urllib messages can carry the URL (which holds the topic)
        return {"ok": False, "message": "could not reach ntfy (" + type(e).__name__ + ")"}
    _last = time.time()
    try:
        STATE["last_id"] = json.loads(text)["nonce"]
    except Exception:
        STATE["last_id"] = None
    return {"ok": ok, "message": "sent" if ok else "ntfy refused it", "label": v["label"], "id": STATE["last_id"]}


# ---------------------------------------------------------------------------- voice commands
def parse_command(text):
    low = text.lower().strip(" .?!")
    if re.search(r"\b(?:send|text|put|get)\b.*\b(?:report|research|summary|it|that)\b.*\bphone\b", low) and not re.search(r"\bmac\b", low):
        return {"speech": "Sending the summary to your phone.", "macsend": "report_phone"}
    if re.search(r"\b(?:send|show|open|put|get)\b.*\b(?:report|research|notes?)\b.*\bmac(?:book)?\b", low):
        return {"speech": "Sending the report to your Mac.", "macsend": "report"}
    if re.search(r"\b(?:send|share|text|forward|push)\b.*\b(?:that|this|it|the video|the link|the page|the article|the map)\b.*\b(?:(?:to|onto) (?:my )?|on my )mac(?:book)?\b", low):   # only send/share/text: "show it on the map" (heard as "Mac") is the map
        return {"speech": "Sending it to your Mac.", "macsend": "current"}
    if re.search(r"\b(?:pair|connect|link)\b.*\bmac(?:book)?\b|\bmac(?:book)?\b.*\b(?:pairing|pair)\b", low) and not re.search(r"\bshow\b.*\bcode\b", low):
        return {"speech": "The pairing code is on screen. Type it on your Mac.", "mac": "pair"}
    if re.search(r"\b(?:show|display|what(?:'s| is))\b.*\b(?:pairing )?code\b", low) and re.search(r"pair", low):
        return {"speech": "Here is the pairing code.", "mac": "show_code"}
    return None
