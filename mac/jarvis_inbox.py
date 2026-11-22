#!/usr/bin/env python3
"""Jarvis inbox for the Mac. Listens (outbound only, no open ports) for signed messages
from the Go and opens links or files here. Standard library only.
  python3 jarvis_inbox.py pair   -> type the pairing code shown on the Go
  python3 jarvis_inbox.py run    -> listen (it also tells the Go "Mac connected")
  python3 jarvis_inbox.py install -> optional: start listening by itself every time you log in (launchd)
  python3 jarvis_inbox.py ask "what is on my calendar"   -> ask the Go and print its answer
  python3 jarvis_inbox.py note "buy milk" | link https://... | clip   -> hand a note, a link or your clipboard to the Go
"""
import getpass, hashlib, ipaddress, json, os, re, subprocess, sys, time, urllib.parse, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jarvis_pair as jp

CFG = os.path.expanduser("~/.jarvis-inbox/config.json")
DEST = os.path.expanduser("~/Downloads/Jarvis")
INBOX = os.path.expanduser("~/Downloads/Jarvis Inbox")      # everything received is SAVED here and never opened, run or unzipped
QUARANTINE = os.path.join(INBOX, "Quarantine")
BLOCKED = {".app", ".pkg", ".dmg", ".command", ".sh", ".exe", ".bat", ".cmd", ".scr", ".jar", ".js", ".vbs", ".ps1", ".msi", ".com", ".py", ".pl", ".rb", ".workflow", ".terminal", ".dylib", ".so", ".apk", ".ipa"}
FETCH_MAX = 500 * 1024 * 1024
EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mov", ".m4v", ".webm", ".pdf", ".txt", ".md"}
MAX_BYTES = 25 * 1024 * 1024
SERVER = "https://ntfy.sh"


def safe_url(u):
    p = urllib.parse.urlsplit(u)
    return (p.scheme == "https" and p.hostname and not p.username and not p.password
            and len(u) <= 2000 and "\n" not in u and " " not in u)


def private_http(u):
    """http:// to a private (home network) address only, with a one-time path."""
    p = urllib.parse.urlsplit(u)
    try:
        ip = ipaddress.ip_address(p.hostname or "")
    except ValueError:
        return False
    return p.scheme == "http" and (ip.is_private or ip.is_loopback) and not p.username and re.fullmatch(r"/o/[A-Za-z0-9_-]{20,80}", p.path or "") is not None and len(u) < 300


def fetch(payload):
    """The Go offers a file at a one-time local URL. Check everything, save it, never open it. -> (result, detail)."""
    name = re.sub(r"[^\w .()\[\]+-]", "_", os.path.basename(str(payload.get("name", "")) or "file"))[:120] or "file"
    size = int(payload.get("size") or 0)
    if not private_http(payload.get("url", "")):
        return "dropped", "the file address is not on your home network"
    if not 0 < size <= FETCH_MAX:
        return "dropped", "file too big (over 500 MB) or empty"
    ext = os.path.splitext(name)[1].lower()
    blocked = ext in BLOCKED
    folder = QUARANTINE if blocked else INBOX
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{time.strftime('%Y%m%d-%H%M%S')}-{name}")
    h, n = hashlib.sha256(), 0
    try:
        with urllib.request.urlopen(urllib.request.Request(payload["url"], headers={"User-Agent": "jarvis-mac"}), timeout=60) as r, open(path, "wb") as f:
            while chunk := r.read(1 << 16):
                n += len(chunk)
                if n > size or n > FETCH_MAX:
                    break
                h.update(chunk); f.write(chunk)
    except Exception as e:
        try: os.remove(path)
        except OSError: pass
        return "dropped", "could not fetch it (" + type(e).__name__ + "): the link works once and for 10 minutes"
    if n != size or h.hexdigest() != payload.get("sha256"):
        os.remove(path)
        return "dropped", "the file did not match its checksum (changed on the way)"
    if sys.platform == "darwin":                                     # keep macOS's own "downloaded from the internet" warning on it
        subprocess.run(["xattr", "-w", "com.apple.quarantine", f"0081;{int(time.time()):x};JarvisInbox;", path], check=False, capture_output=True)
    if blocked:
        return "opened", f"saved in Quarantine, NOT opened ({ext} files are never opened): {name}"
    return "opened", f"saved in Jarvis Inbox: {name}"


def handle(payload, event):
    """-> (result, reason). result is "opened" or "dropped"; reason says why in plain words."""
    t = payload.get("type")
    if t == "open_url":
        if not safe_url(payload.get("url", "")):
            return "dropped", "not an https link (or it has a password or spaces in it)"
        subprocess.run(["open", payload["url"]], check=False)
        return "opened", "link"
    if t == "fetch":
        return fetch(payload)
    if t == "open_file":
        att = event.get("attachment") or {}
        a = urllib.parse.urlsplit(att.get("url", ""))
        name = os.path.basename(payload.get("name", "")) or "file"
        if os.path.splitext(name)[1].lower() not in EXT:
            return "dropped", "file type not allowed (" + (os.path.splitext(name)[1].lower() or "no extension") + ")"
        if a.scheme != "https" or a.hostname != "ntfy.sh":
            return "dropped", "the file did not come from the message service"
        if int(att.get("size") or 0) > MAX_BYTES:
            return "dropped", "file too big (over 25 MB)"
        os.makedirs(DEST, exist_ok=True)
        path = os.path.join(DEST, f"{int(time.time())}-{name}")
        h, n = hashlib.sha256(), 0
        with urllib.request.urlopen(att["url"], timeout=60) as r, open(path, "wb") as f:
            while chunk := r.read(65536):
                n += len(chunk)
                if n > MAX_BYTES:
                    break
                h.update(chunk); f.write(chunk)
        if n > MAX_BYTES:
            os.remove(path)
            return "dropped", "file too big (over 25 MB)"
        if h.hexdigest() != payload.get("sha256"):
            os.remove(path)
            return "dropped", "the file did not match its checksum (changed on the way)"
        return "opened", "file saved in " + DEST + " (not opened): " + name
    return "dropped", "unknown kind of message"


def post(cfg, key, payload):
    """Send a signed reply to the Go on the SECOND private topic ("hello" when we start, "ack" after each message). Never prints the topic.
    An ack holds only the message id and "opened" or "dropped: <reason>": never the link or the file."""
    if not cfg.get("ack_topic"):
        print("Old pairing (no reply channel): run  python3 jarvis_inbox.py pair  again so the Go can see delivery.", flush=True)
        return
    try:
        req = urllib.request.Request(f"{SERVER}/{cfg['ack_topic']}", data=jp.sign(key, payload).encode(), method="POST", headers={"User-Agent": "jarvis-mac", "Tags": "jarvis-mac"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print("could not tell the Go:", type(e).__name__, flush=True)


def run():
    cfg = json.load(open(CFG))
    key, seen, delay = bytes.fromhex(cfg["key"]), set(), 2
    post(cfg, key, {"type": "hello"})
    print("Listening. Leave this window open (or run: python3 jarvis_inbox.py install).", flush=True)
    while True:
        try:
            with urllib.request.urlopen(f"{SERVER}/{cfg['topic']}/json", timeout=90) as r:
                delay = 2
                for line in r:
                    ev = json.loads(line)
                    if ev.get("event") != "message":
                        continue
                    p, why, mid = jp.check(key, ev.get("message", ""), seen)
                    when = time.strftime("%H:%M:%S")
                    if why:                                                   # refused: say so in plain words, and (only if the signature was genuine) tell the Go
                        print(when, "DROPPED a message:", why, flush=True)
                        if mid:
                            post(cfg, key, {"type": "ack", "id": mid, "result": "dropped: " + why})
                        continue
                    res, detail = handle(p, ev)
                    print(when, "opened" if res == "opened" else "DROPPED a message:", detail, flush=True)
                    post(cfg, key, {"type": "ack", "id": mid, "result": "opened" if res == "opened" else "dropped: " + detail})
        except Exception as e:
            print("reconnecting:", type(e).__name__, flush=True)
            time.sleep(delay); delay = min(delay * 2, 60)


def pair():
    code = getpass.getpass("Pairing code from the Go: ")
    topic, key = jp.derive(code)
    os.makedirs(os.path.dirname(CFG), exist_ok=True)
    fd = os.open(CFG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"topic": topic, "key": key.hex(), "ack_topic": jp.ack_topic(code)}, f)
    print("Paired. Now run: python3 jarvis_inbox.py run")


def to_go(kind, text):
    """Send something TO the Go (signed, on the private "up" topic). For ask: wait for the answer."""
    cfg = json.load(open(CFG))
    key = bytes.fromhex(cfg["key"])
    up = jp.up_topic(key)
    body = {"type": kind, "text": text} if kind != "link" else {"type": "link", "url": text}
    signed = jp.sign(key, body)
    mid = json.loads(signed)["nonce"]
    req = urllib.request.Request(f"{SERVER}/{up}", data=signed.encode(), method="POST", headers={"User-Agent": "jarvis-mac"})
    urllib.request.urlopen(req, timeout=15).read()
    if kind != "ask":
        print("Sent to the Go.")
        return
    t0, seen = time.time(), set()
    while time.time() - t0 < 75:                                  # the answer comes back on the main topic, signed, with our message id
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{SERVER}/{cfg['topic']}/json?poll=1&since={int(t0) - 2}", headers={"User-Agent": "jarvis-mac"}), timeout=15) as r:
                for ln in r:
                    ev = json.loads(ln)
                    if ev.get("event") != "message":
                        continue
                    p = jp.verify(key, ev.get("message", ""), seen)
                    if p and p.get("type") == "reply" and p.get("id") == mid:
                        print(p.get("text", ""))
                        return
        except Exception:
            pass
        time.sleep(2)
    print("(no answer: is the Go on and online?)")


def say():
    kind = sys.argv[1]
    if kind == "clip":
        text = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
    else:
        text = " ".join(sys.argv[2:])
    if not text.strip():
        sys.exit("nothing to send")
    to_go(kind, text.strip())


def install():
    """Start listening automatically at login (a launchd agent in your own account; it only needs the internet, it opens no ports)."""
    plist = os.path.expanduser("~/Library/LaunchAgents/com.jarvis.inbox.plist")
    here = os.path.abspath(__file__)
    os.makedirs(os.path.dirname(plist), exist_ok=True)
    open(plist, "w").write(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.jarvis.inbox</string>
<key>ProgramArguments</key><array><string>{sys.executable}</string><string>{here}</string><string>run</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>{os.path.expanduser("~/.jarvis-inbox/log.txt")}</string>
<key>StandardErrorPath</key><string>{os.path.expanduser("~/.jarvis-inbox/log.txt")}</string>
</dict></plist>
""")
    subprocess.run(["launchctl", "unload", plist], check=False, stderr=subprocess.DEVNULL)
    subprocess.run(["launchctl", "load", plist], check=False)
    print("Done. It now starts by itself when you log in. To stop it: launchctl unload", plist)


if __name__ == "__main__":
    {"pair": pair, "run": run, "install": install, "ask": say, "note": say, "link": say, "clip": say}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__))()
