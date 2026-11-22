"""User settings, kept outside the repo: ~/.config/jarvis/settings.json
   home_area : free text ("Canberra, Australia") used for 'near me', weather and local questions
   home_lat / home_lon : coordinates of the home area (filled in when it is set)
   units     : "metric" | "imperial"
Env JARVIS_HOME_AREA overrides home_area. Set it with:  bin/jarvisctl home "Canberra, Australia"
or by voice: "set my home to Canberra"."""
import json
import os
from pathlib import Path

PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "jarvis" / "settings.json"
_cache = None


_mtime = 0.0


def load():
    """The settings dict. Re-read whenever the file changed on disk (another process, or you editing it), so a stale copy can never overwrite newer values."""
    global _cache, _mtime
    try:
        m = PATH.stat().st_mtime
    except OSError:
        m = 0.0
    if _cache is None or m != _mtime:
        try:
            _cache = json.loads(PATH.read_text())
        except (OSError, ValueError):
            _cache = {}
        _mtime = m
    return _cache


def get(key, default=None):
    if key == "home_area" and os.environ.get("JARVIS_HOME_AREA"):
        return os.environ["JARVIS_HOME_AREA"]
    return load().get(key, default)


def update(**kw):
    """Read-modify-write under a file lock, starting from what is on disk RIGHT NOW (not from a copy in memory), written atomically."""
    import fcntl
    global _cache, _mtime
    PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(str(PATH) + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            cur = json.loads(PATH.read_text())
        except (OSError, ValueError):
            cur = {}
        cur.update(kw)
        tmp = str(PATH) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cur, f, indent=2)
        os.replace(tmp, PATH)
        _cache = cur
        try:
            _mtime = PATH.stat().st_mtime
        except OSError:
            _mtime = 0.0
