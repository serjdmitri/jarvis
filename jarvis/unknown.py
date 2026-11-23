"""Phrases Jarvis did not understand: a local list (data/unknown.jsonl, text only, nothing secret) so the PATTERNS get fixed, not single cases. Shown (last 20) in Settings > Advanced and by
"what couldn't you understand". When the model itself says it did not get it, the answer is one short question or a list of what Jarvis can do."""
import json
import re
import time

from . import config, memory

PATH = config.DATA / "unknown.jsonl"
UNSURE = re.compile(r"^(?:i(?:'m| am) not sure what you mean|i (?:didn't|did not|couldn't|could not) (?:catch|understand|get)|sorry, i (?:didn't|did not|couldn't)|i don't know (?:what you mean|how to)|could you (?:say|repeat|rephrase))", re.I)
CAN_DO = ("I can answer questions, show a map and tell you how long a trip takes, play music, look through the camera, remember things for you, and show 3D models. "
          "Try: how long to Starbucks, what do you see, or remember that I like jazz.")


def log(text, why=""):
    t = re.sub(r"\s+", " ", text or "").strip()
    if not t or memory.looks_secret(t):
        return
    try:
        config.DATA.mkdir(exist_ok=True)
        with open(PATH, "a") as f:
            f.write(json.dumps({"t": int(time.time()), "text": t[:140], "why": why[:40]}, ensure_ascii=False) + "\n")
        lines = PATH.read_text().splitlines()
        if len(lines) > 500:
            PATH.write_text("\n".join(lines[-300:]) + "\n")
    except OSError:
        pass


def last(n=20):
    try:
        rows = [json.loads(l) for l in PATH.read_text().splitlines() if l.strip()]
    except (OSError, ValueError):
        return []
    return rows[-n:][::-1]


def reply_is_unsure(reply):
    return bool(UNSURE.match((reply or "").strip()))


def card():
    rows = last(20)
    return {"type": "list", "title": "NOT UNDERSTOOD (LAST 20)", "items": [r["text"] for r in rows] or ["Nothing yet."]}
