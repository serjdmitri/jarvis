"""Look memory, reading, and watching. 
* "remember this shelf": the picture (and the names of what was boxed) is kept in data/look/ (local only, newest 40) so "where did I put the charger?" can find the picture it was in.
* "read this": local OCR (tesseract) of the picture, as a card you can copy, send to your Mac or turn into a list.
* "tell me when the printer light turns green": every 20 s (at most 10 minutes) the camera picture is checked by the brain for that one condition; only cameras (phone or USB), never your screen."""
import json
import re
import shutil
import time

from . import config

DIR = config.DATA / "look"
INDEX = DIR / "index.json"
WATCH_MAX_S, WATCH_EVERY_S = 600, 20


def _load():
    try:
        return json.loads(INDEX.read_text())
    except (OSError, ValueError):
        return []


def remember(path, label, names, desc):
    DIR.mkdir(parents=True, exist_ok=True)
    name = f"look-{int(time.time() * 1000)}.jpg"
    shutil.copyfile(path, DIR / name)
    rows = _load() + [{"t": int(time.time()), "file": name, "label": (label or "")[:60], "names": [n[:24] for n in names][:10], "desc": (desc or "")[:200]}]
    for old in rows[:-40]:
        try:
            (DIR / old["file"]).unlink()
        except OSError:
            pass
    INDEX.write_text(json.dumps(rows[-40:]))
    return rows[-1]


def find(query):
    """-> the best matching saved entry or None (words of the query against the label, the boxed names and the description)."""
    q = {w for w in re.findall(r"[a-z0-9']+", query.lower()) if len(w) > 2 and w not in ("the", "my", "did", "where", "put", "have", "you", "seen", "was", "see", "saw", "last", "and")}
    best, score = None, 0
    for r in _load():
        hay = " ".join([r["label"], " ".join(r["names"]), r["desc"]]).lower()
        s = sum(1 for w in q if w in hay) + (0.001 * r["t"] / 1e10)
        if s > score and any(w in hay for w in q):
            best, score = r, s
    return best


def when(t):
    d = time.time() - t
    if d < 90:
        return "just now"
    if d < 3600:
        return f"{int(d / 60)} minutes ago"
    if d < 86400:
        return f"{int(d / 3600)} hours ago"
    return time.strftime("on %A", time.localtime(t))


def parse_command(text):
    low = text.lower().strip(" .?!")
    m = re.fullmatch(r"(?:please )?remember (?:this|that|what you see|this (?:shelf|desk|drawer|room|table|cupboard|cabinet|bag|box|place|spot|corner))(?: (?:as|is|called) (.+))?", low)
    if m:
        return {"op": "remember", "label": (m.group(1) or re.sub(r"^(?:please )?remember (?:this )?", "", low)).strip()}
    m = re.fullmatch(r"(?:where (?:did i (?:put|leave|see)|is|are|was)|have you seen|did you see|do you remember (?:where|seeing)) (?:i (?:put|left) )?(?:my |the |a )?(.+)", low)
    if m and not re.search(r"\b(?:the nearest|a pharmacy|coffee|starbucks|airport|station|bank|hotel)\b", low):
        return {"op": "find", "what": m.group(1)}
    if re.fullmatch(r"(?:please )?(?:read (?:this|that|it)(?: (?:label|sign|text|page|document|paper|receipt|screen))?(?: to me| for me| out)?|what does (?:this|that|it) say)", low):
        return {"op": "read"}
    m = re.fullmatch(r"(?:please )?(?:tell me|let me know|alert me|watch for|notify me) (?:when|if|once) (.+)", low)
    if m:
        return {"op": "watch", "cond": m.group(1)}
    if re.fullmatch(r"(?:please )?(?:stop|cancel) watching(?: for (?:that|it))?", low):
        return {"op": "unwatch"}
    return None
