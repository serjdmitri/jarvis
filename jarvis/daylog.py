"""A private day log, ONLY when you ask for it ("keep a day log", settings daylog): what you asked and did today, so "what did I do today" and "what did I ask about yesterday" can answer.
data/daylog.jsonl (mode 600, outside git): one line per request with the time and the words you said; nothing from answers, cards, files or the camera. Viewable ("show my day log"),
deletable ("delete my day log"), and lines older than 30 days (setting memory_days) are removed automatically. Off by default."""
import json
import os
import re
import time

from . import config, settings

FILE = config.DATA / "daylog.jsonl"


def enabled():
    return bool(settings.get("daylog"))


def add(text):
    if not enabled() or not text:
        return
    config.DATA.mkdir(parents=True, exist_ok=True)
    fd = os.open(FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(json.dumps({"t": time.time(), "text": re.sub(r"\s+", " ", text).strip()[:200]}) + "\n")


def _rows():
    days = settings.get("memory_days", 30)
    cutoff = time.time() - (days * 86400 if isinstance(days, (int, float)) and days > 0 else 10 ** 9)
    out = []
    try:
        for ln in FILE.read_text().splitlines():
            try:
                r = json.loads(ln)
                if r["t"] >= cutoff:
                    out.append(r)
            except (ValueError, KeyError, TypeError):
                continue
    except OSError:
        return []
    return out


def purge():
    rows = _rows()
    if FILE.exists():
        FILE.write_text("".join(json.dumps(r) + "\n" for r in rows))
        os.chmod(FILE, 0o600)


def day(offset=0):
    d0 = time.strftime("%Y-%m-%d", time.localtime(time.time() - offset * 86400))
    return [r for r in _rows() if time.strftime("%Y-%m-%d", time.localtime(r["t"])) == d0]


def delete():
    try:
        FILE.unlink()
        return True
    except OSError:
        return False


def parse_command(text):
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    if re.fullmatch(r"(?:please )?(?:keep|start|turn on) (?:a |my )?day log|(?:please )?start logging my day", low):
        return {"kind": "on"}
    if re.fullmatch(r"(?:please )?(?:stop|turn off) (?:keeping )?(?:a |my |the )?day log|(?:please )?stop logging my day", low):
        return {"kind": "off"}
    if re.fullmatch(r"(?:please )?(?:delete|clear|erase) (?:my |the )?day log", low):
        return {"kind": "delete"}
    if re.fullmatch(r"(?:please )?(?:show|open) (?:me )?(?:my |the )?day log|what did i (?:do|ask(?: about)?) today|what have i (?:done|asked) today", low):
        return {"kind": "show", "offset": 0}
    if re.fullmatch(r"what did i (?:do|ask(?: about)?) yesterday", low):
        return {"kind": "show", "offset": 1}
    return None
