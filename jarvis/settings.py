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


def load():
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(PATH.read_text())
        except (OSError, ValueError):
            _cache = {}
    return _cache


def get(key, default=None):
    if key == "home_area" and os.environ.get("JARVIS_HOME_AREA"):
        return os.environ["JARVIS_HOME_AREA"]
    return load().get(key, default)


def update(**kw):
    s = dict(load()); s.update(kw)
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(s, indent=2))
    global _cache
    _cache = s
