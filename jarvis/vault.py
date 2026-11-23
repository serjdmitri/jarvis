"""The Go's side of the memory vault (Obsidian-style Markdown on the Mac; the Mac helper owns the folder). Three actions go over the signed helper link: search, read, append (plus forget/undo/graph).
If the Mac is asleep: reads come from a small local cache of the newest memories, writes wait in data/vault_queue.jsonl and are sent when the helper is back; the answer says so plainly.
Nothing secret is ever sent or cached: the same filters run here before anything leaves the Go. "Forget" moves a note to the vault's Trash with an undo."""
import json
import re
import threading
import time

from . import config, machelper, memory

CACHE = config.DATA / "vault_cache.json"
QUEUE = config.DATA / "vault_queue.jsonl"
_lock = threading.Lock()
LAST_FORGOTTEN = {"undo": None, "title": None, "t": 0.0}

SENSITIVE = [(re.compile(r"\b(diagnos\w*|medication|prescription|therapy|therapist|cancer|diabet\w*|depress\w*|anxiety|surgery|pregnan\w*|blood pressure|my doctor)\b", re.I), "health"),
             (re.compile(r"\b(salary|debt|bank balance|loan|mortgage|net worth|savings|owe[sd]? (?:him|her|them)|credit score|tax return|income)\b", re.I), "money"),
             (re.compile(r"\b(?:my )?(?:wife|husband|girlfriend|boyfriend|friend|boss|mum|mom|dad|brother|sister|neighbou?r|colleague)\b.*\b(cheat\w*|affair|divorc\w*|arrest\w*|sick|ill|fired|pregnan\w*|secret|drunk|addict\w*|lawsuit)\b", re.I), "someone else's private details")]


def sensitive_reason(text):
    for rx, why in SENSITIVE:
        if rx.search(text or ""):
            return why
    return None


def available():
    return machelper.status() in ("ready", "busy")


def _call(op, payload, timeout=10):
    """-> (ok, dict). status 0 = Mac unreachable."""
    code, r = machelper.request("POST", "/vault?op=" + op, json.dumps(payload).encode(), timeout=timeout)
    return code, r


def _cache_load():
    try:
        return json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return {"items": [], "t": 0}


def _cache_save(d):
    config.DATA.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(d, ensure_ascii=False))


def refresh_cache():
    code, r = _call("recent", {"n": 40}, timeout=6)
    if code == 200 and r.get("ok"):
        _cache_save({"items": r["items"], "t": time.time()})
        return True
    return False


def _queue_add(rec):
    with _lock:
        config.DATA.mkdir(exist_ok=True)
        with open(QUEUE, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def queued():
    try:
        return [json.loads(l) for l in QUEUE.read_text().splitlines() if l.strip()]
    except (OSError, ValueError):
        return []


def flush():
    """Send what was saved while the Mac slept. Called when the helper is back."""
    with _lock:
        items = queued()
        if not items:
            return 0
        left, sent = [], 0
        for it in items:
            code, r = _call(it["op"], it["payload"], timeout=8)
            if code == 200:
                sent += 1
            elif code == 0:
                left.append(it)
            # a refusal (secret, unknown) is dropped: it would be refused again
        QUEUE.write_text("".join(json.dumps(i) + "\n" for i in left))
    if sent:
        refresh_cache()
    return sent


def remember(text, title=None, kind="memory", tags=None, confirmed=False, source="voice"):
    """-> {"ok", "speech", "needs_confirm"?}. The local memory (memory.py) keeps working without a Mac; the vault gets the same note when the Mac is awake."""
    if memory.looks_secret(text):
        return {"ok": False, "speech": "I don't store passwords, card numbers, codes or keys, so I won't remember that one."}
    why = sensitive_reason(text)
    if why and not confirmed:
        return {"ok": False, "needs_confirm": why, "speech": f"That is about {why}. Do you want me to save it? Say yes or no."}
    ok, sp = memory.add(text)
    payload = {"kind": kind, "title": title or "", "text": text, "tags": tags or [], "source": source, "confirmed": bool(confirmed)}
    code, r = (0, {}) if not available() else _call("append", payload)
    if code == 200 and r.get("ok"):
        refresh_cache()
        return {"ok": True, "speech": sp, "path": r.get("path")}
    if code == 200:                                                           # the vault refused (its own secret/sensitive check): say why
        return {"ok": False, "speech": r.get("why", "I could not save that."), "needs_confirm": r.get("needs_confirm")}
    _queue_add({"op": "append", "payload": payload, "t": time.time()})
    return {"ok": True, "speech": sp.rstrip(".") + ". Your Mac is asleep, so the note goes in the vault when it is back.", "queued": True}


def search(q, limit=5):
    """-> {"ok", "hits": [{"title","snippet","path"}], "from": "vault"|"cache"}"""
    if available():
        code, r = _call("search", {"q": q, "limit": limit}, timeout=6)
        if code == 200 and r.get("ok"):
            return {"ok": True, "hits": r["hits"], "from": "vault"}
    toks = [t for t in re.findall(r"[\w']+", q.lower()) if len(t) > 2]
    hits = []
    for it in _cache_load().get("items", []):
        sc = sum((it["title"] + " " + it["text"]).lower().count(t) for t in toks)
        if sc:
            hits.append(dict(it, snippet=it["text"], score=sc))
    hits.sort(key=lambda h: -h["score"])
    return {"ok": True, "hits": hits[:limit], "from": "cache"}


def read(path):
    if available():
        code, r = _call("read", {"path": path}, timeout=6)
        if code == 200 and r.get("ok"):
            return r
    for it in _cache_load().get("items", []):
        if it["path"] == path:
            return {"ok": True, "title": it["title"], "text": it["text"]}
    return {"ok": False, "why": "The Mac is asleep and that note is not in my small cache."}


def forget(topic):
    """Move the best-matching memory note to the vault's Trash (undo kept). -> speech."""
    s = search(topic, 1)
    if not s["hits"]:
        return "I don't have a note about that."
    h = s["hits"][0]
    if not available():
        return "Your Mac is asleep, and I only move notes to the Trash when it is awake. Ask again then."
    code, r = _call("forget", {"path": h["path"]})
    if code == 200 and r.get("ok"):
        LAST_FORGOTTEN.update(undo=r["undo"], title=h["title"], t=time.time())
        refresh_cache()
        return f"I moved the note {h['title']} to the Trash. Say bring it back to undo."
    return (r or {}).get("why", "I could not move that note.")


def undo():
    if not LAST_FORGOTTEN["undo"] or time.time() - LAST_FORGOTTEN["t"] > 1800:
        return "There is nothing to bring back."
    code, r = _call("undo", {"undo": LAST_FORGOTTEN["undo"]})
    if code == 200 and r.get("ok"):
        t = LAST_FORGOTTEN["title"]
        LAST_FORGOTTEN.update(undo=None)
        refresh_cache()
        return f"Brought back {t}."
    return (r or {}).get("why", "I could not bring it back.")


def graph(focus=None, folder=None):
    if not available():
        return None, "Your Mac is asleep: the memory map needs the vault there."
    code, r = _call("graph", {"focus": focus or "", "folder": folder or "", "max": 300}, timeout=10)
    if code == 200 and r.get("ok"):
        return r, None
    return None, "I could not read the memory map."


def context_fast(question, limit=3):
    """Same as context_for but from the local cache only: no network on the path to the model. (The cache is refreshed whenever a note is written or the Mac wakes.)"""
    toks = [t for t in re.findall(r"[\w']+", question.lower()) if len(t) > 3]
    if not toks:
        return "", []
    hits = []
    for it in _cache_load().get("items", []):
        sc = sum((it["title"] + " " + it["text"]).lower().count(t) for t in toks)
        if sc:
            hits.append((sc, it))
    hits.sort(key=lambda h: -h[0])
    hits = [h[1] for h in hits[:limit]]
    if not hits:
        return "", []
    return "[from the user's notes (use only if relevant): " + " | ".join(f"{h['title']}: {h['text'][:200]}" for h in hits) + "]\n", [h["title"] for h in hits]


def context_for(question, limit=3):
    """"From your notes:" for the brain: a few short snippets (never whole notes) + the names to show. -> (text, [titles])"""
    s = search(question, limit)
    hits = [h for h in s["hits"] if h.get("score", 1) >= 1][:limit]
    if not hits:
        return "", []
    txt = "[from the user's notes (use only if relevant): " + " | ".join(f"{h['title']}: {h.get('snippet', '')[:200]}" for h in hits) + "]\n"
    return txt, [h["title"] for h in hits]
