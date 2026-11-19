"""Memory across days: a short rolling log of what you asked and what Jarvis answered (data/threads.json, local only, secrets refused), so "where were we", "continue" and "what did we do yesterday" work.
Only the last few topics are offered to the brain, never the whole log."""
import json
import re
import time

from . import config, memory

PATH = config.ROOT / "data" / "threads.json"
RESUME = re.compile(r"\b(?:where were we|continue (?:where|from)|pick up where|resume|what (?:were we|did we) (?:doing|do|talk about)|last time|yesterday we|earlier (?:today|we))\b", re.I)


def _load():
    try:
        return json.loads(PATH.read_text())
    except (OSError, ValueError):
        return []


def add(q, a):
    if not q or memory.looks_secret(q) or memory.looks_secret(a or ""):
        return
    rows = _load()
    rows.append({"t": int(time.time()), "q": q[:140], "a": (a or "")[:180]})
    try:
        PATH.parent.mkdir(exist_ok=True)
        PATH.write_text(json.dumps(rows[-80:]))
    except OSError:
        pass


def is_resume(text):
    return bool(RESUME.search(text or ""))


def context(n=5):
    """A short note for the brain: the last few exchanges (any day), oldest first."""
    rows = _load()[-n:]
    if not rows:
        return ""
    day = lambda t: time.strftime("%a %H:%M", time.localtime(t))
    return "[earlier conversations (for 'where were we'): " + " | ".join(f"{day(r['t'])} you: {r['q']} -> {r['a'][:70]}" for r in rows) + "]\n"
