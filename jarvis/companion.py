"""Your other devices talking TO the Go (PROMPT: multi-device). The Mac sends signed messages on a private "up" topic (ask, note, link, clip); the phone uses the iPhone endpoint's /inbox and /ask routes.
Every message is verified (signature, freshness, no replay), rate limited and capped; an ask is answered by the same sandboxed path as the iPhone voice endpoint (no terminal, no phone control).
The answer goes back signed. Notes and links become cards on the Go and are saved in data/inbox.jsonl (local only)."""
import json
import threading
import time
import urllib.request

from . import config, jarvis_pair as jp, mac

INBOX = config.DATA / "inbox.jsonl"
CAPS = {"ask": 300, "note": 2000, "clip": 2000, "link": 500}
_seen = set()
_stamps = []
_stop = {"flag": False, "thread": None}


def allowed(kind, p):
    """-> cleaned message or None. Rate: at most 12 a minute."""
    now = time.time()
    _stamps[:] = [t for t in _stamps if now - t < 60]
    if len(_stamps) >= 12 or kind not in CAPS:
        return None
    _stamps.append(now)
    text = str(p.get("text") or p.get("url") or "").strip()[:CAPS[kind]]
    if not text:
        return None
    if kind == "link" and not text.startswith("https://"):
        return None
    return {"kind": kind, "text": text}


def save(source, m):
    try:
        INBOX.parent.mkdir(exist_ok=True)
        with open(INBOX, "a") as f:
            f.write(json.dumps({"t": int(time.time()), "from": source, **m}) + "\n")
    except OSError:
        pass


def handle(assistant, source, p):
    """-> reply text for an ask, else None. Runs on a worker thread."""
    m = allowed(p.get("type") or p.get("kind"), p)
    if not m:
        return None
    kind = m["kind"]
    who = "your Mac" if source == "mac" else "your phone"
    if kind == "ask":
        reply = assistant.phone_ask(m["text"])
        assistant.on_msg("sys", f"{source.upper()}: {m['text'][:80]}  ->  {reply[:80]}")
        try:
            from . import webapp_hook
        except ImportError:
            pass
        return reply
    save(source, m)
    if assistant.on_card:
        if kind == "link":
            assistant.on_card({"type": "guide", "gid": "inbox", "title": "From " + who, "full": False, "paged": False, "status": "", "buttons": [{"label": "OPEN IT HERE", "action": "open_link", "payload": {"url": m["text"]}}],
                               "steps": [{"n": 1, "text": m["text"][:120]}]})
        else:
            assistant.on_card({"type": "text", "title": "From " + who, "text": m["text"][:600]})
    return "Saved."


def _loop(assistant):
    """Stream the up topic while paired (reconnects quietly). The topic is used inside the request only."""
    delay = 2
    while not _stop["flag"]:
        keys = mac._load()
        if not keys:
            time.sleep(10)
            continue
        topic, key = keys
        up = jp.up_topic(key)
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{mac.SERVER}/{up}/json?since={int(time.time()) - 5}", headers={"User-Agent": "jarvis-local"}), timeout=95) as r:
                delay = 2
                for line in r:
                    if _stop["flag"]:
                        return
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if ev.get("event") != "message":
                        continue
                    p = jp.verify(key, ev.get("message", ""), _seen)
                    if not p or p.get("type") not in CAPS:
                        continue
                    threading.Thread(target=_dispatch, args=(assistant, key, p), daemon=True).start()
        except Exception as e:
            print("[companion] reconnecting:", type(e).__name__, flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 60)


def _dispatch(assistant, key, p):
    reply = handle(assistant, "mac", p)
    if reply is not None and p.get("type") == "ask":
        mac.post_main({"type": "reply", "id": p.get("nonce"), "text": reply[:400]})


def start(assistant):
    if _stop["thread"] and _stop["thread"].is_alive():
        return
    _stop["flag"] = False
    t = threading.Thread(target=_loop, args=(assistant,), daemon=True)
    _stop["thread"] = t
    t.start()
