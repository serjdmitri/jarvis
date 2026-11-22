"""Where you really are: the latest GPS fix your iPhone posted ("Send location to Jarvis" Shortcut). The Go has no GPS and an IP lookup is often a city off, so a phone fix is used first.
Only the LATEST fix is kept (data/location.json, mode 600, outside git) with its accuracy and age: no history, nothing in logs, notifications or git. Older than 12 hours it is ignored."""
import json
import os
import time

from . import config

FILE = config.DATA / "location.json"
MAX_AGE_S = 12 * 3600


def save(lat, lon, accuracy=None, when=None):
    """-> True if stored. Rejects impossible coordinates, the 0,0 point and bad accuracy."""
    try:
        lat, lon = float(lat), float(lon)
        acc = float(accuracy) if accuracy not in (None, "") else None
        when = float(when) if when not in (None, "") else time.time()
    except (TypeError, ValueError):
        return False
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (abs(lat) < 0.5 and abs(lon) < 0.5):
        return False
    if acc is not None and not (0 <= acc <= 100000):
        acc = None
    if when > time.time() + 300 or when < time.time() - 7 * 86400:      # a clock that is wrong by a lot: take "now"
        when = time.time()
    config.DATA.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"lat": round(lat, 6), "lon": round(lon, 6), "acc": acc, "t": when}, f)
    os.replace(tmp, FILE)
    return True


def latest():
    """-> {"lat","lon","acc","t"} or None (none stored, or too old)."""
    try:
        d = json.loads(FILE.read_text())
        if time.time() - d["t"] <= MAX_AGE_S:
            return d
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def age_words(t):
    s = max(0, int(time.time() - t))
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{s // 60} min ago"
    return f"{s // 3600} h ago"


def forget():
    try:
        FILE.unlink()
        return True
    except OSError:
        return False
