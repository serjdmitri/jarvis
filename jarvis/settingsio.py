"""Settings that stick: a schema version, a backup before any migration, validation that reports (never silently drops) a bad value, export / import and "reset one setting".
Export has NO secrets: keys whose name contains key, token, secret, topic, password, code or contact are left out, and the ntfy topic, Mac pairing, Spotify tokens and the phone endpoint
secret live in their own private files that are never part of it. Import accepts only known settings and checks every value."""
import json
import os
import re
import shutil
import time

from . import config, prefs, settings

SCHEMA = 2
SECRET = re.compile(r"key|token|secret|topic|password|passcode|code|contact|spotify_client", re.I)
BOOLS = {"osk", "morning_brief_wake", "auto_visuals", "chimes", "feelings", "auto_promises", "auto_3d", "markets_desk", "boot_anim", "camera_warnings", "keep_phone_photos", "stay_out", "clock24", "clock_seconds", "machine_warnings",
         "term_wake", "phone_voice", "show_heard", "large_text", "high_contrast", "whisper", "whisper_auto", "auto_persona", "say_switching", "desk_presence", "welcome_speak", "welcome_card", "daylog",
         "silent", "lite"}
CHOICES = {"units": ("metric", "imperial"), "listen_patience": ("fast", "normal", "patient"), "typebox_key": ("alt", "ctrl", "both", "off"), "music_pref": ("youtube", "spotify"),
           "orb_avoid": ("top-right", "top-left", "bottom-left", "bottom-right", "none")}
NUMS = {"spotify_volume_max": (10, 100), "listen_window": (0, 30), "volume": (0, 100), "chime_volume": (0, 100), "idle_minutes": (0, 60), "memory_days": (0, 3650), "share_cap_mb": (1, 5000)}
STRS = {"home_area", "persona", "theme", "whisper_from", "whisper_to", "notify_quiet_from", "notify_quiet_to", "brief_morning", "brief_afternoon", "brief_night"}
EXPORT_DIR = config.ROOT / "exports"


def known(key):
    return key in BOOLS or key in CHOICES or key in NUMS or key in STRS or key == "home_lat" or key == "home_lon" or bool(re.fullmatch(r"speed_[a-z0-9_]+", key)) or bool(re.fullmatch(r"notify_[a-z]+", key))


def check(key, value):
    """-> (ok, plain reason)."""
    if key in BOOLS or re.fullmatch(r"notify_[a-z]+", key):
        return (isinstance(value, bool), "must be on or off")
    if key in CHOICES:
        return (value in CHOICES[key], "must be one of " + ", ".join(CHOICES[key]))
    if key in NUMS:
        lo, hi = NUMS[key]
        return (isinstance(value, (int, float)) and not isinstance(value, bool) and lo <= value <= hi, f"must be a number from {lo} to {hi}")
    if re.fullmatch(r"speed_[a-z0-9_]+", key):
        return (isinstance(value, (int, float)) and not isinstance(value, bool) and prefs.SPEED_MIN <= value <= prefs.SPEED_MAX, f"speed must be {prefs.SPEED_MIN} to {prefs.SPEED_MAX}")
    if key in ("home_lat", "home_lon"):
        return (isinstance(value, (int, float)) and abs(value) <= 180, "must be a coordinate")
    if key in STRS:
        return (isinstance(value, str) and len(value) <= 160, "must be short text")
    return (False, "not a known setting")


def ensure_schema():
    """At start-up: version the file and keep a backup first (the newest 5). -> a note to show (or "")."""
    cur = settings.load()
    if cur.get("schema") == SCHEMA or not settings.PATH.exists():
        return ""
    bak = settings.PATH.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        shutil.copyfile(settings.PATH, bak)
        os.chmod(bak, 0o600)
        for old in sorted(settings.PATH.parent.glob("settings.json.bak-*"))[:-5]:
            old.unlink()
        settings.update(schema=SCHEMA)
        return "Settings upgraded; a backup was kept."
    except OSError:
        return ""


def problems():
    """Values on disk that are invalid, in plain words. They are LEFT as they are (nothing is lost silently); the app tells you."""
    out = []
    for k, v in settings.load().items():
        if k in ("schema",) or SECRET.search(k) or not known(k):
            continue
        ok, why = check(k, v)
        if not ok:
            out.append(f"{k}: {why}")
    return out


def export(path=None):
    """-> the file path written (no secrets inside)."""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    p = path or str(EXPORT_DIR / f"settings-export-{time.strftime('%Y%m%d')}.json")
    data = {k: v for k, v in settings.load().items() if not SECRET.search(k) and known(k)}
    data["schema"] = SCHEMA
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    return p


def import_file(path):
    """-> (applied: list of keys, rejected: list of 'key: reason'). Unknown keys, secrets and bad values are rejected, one by one."""
    try:
        raw = open(path, "rb").read(100_000)
        data = json.loads(raw)
    except (OSError, ValueError):
        return [], ["the file is not a settings export"]
    if not isinstance(data, dict):
        return [], ["the file is not a settings export"]
    good, bad = {}, []
    for k, v in data.items():
        if k == "schema":
            continue
        if SECRET.search(k):
            bad.append(f"{k}: secrets are never imported")
            continue
        ok, why = check(k, v)
        (good.__setitem__(k, v) if ok else bad.append(f"{k}: {why}"))
    if good:
        settings.update(**good)
    return sorted(good), bad


def reset_one(name):
    """Forget one setting (it returns to its default). `name` is a spoken word ("speed", "volume") or a key. -> the keys removed."""
    n = re.sub(r"[^a-z0-9_ ]", "", name.lower()).strip().replace(" ", "_")
    cur = settings.load()
    keys = [k for k in cur if k == n or (n == "speed" and k.startswith("speed_")) or k == n + "_minutes"]
    if not keys:
        return []
    import fcntl
    with open(str(settings.PATH) + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        d = json.loads(settings.PATH.read_text())
        for k in keys:
            d.pop(k, None)
        tmp = str(settings.PATH) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, settings.PATH)
    settings.load()
    return keys


def parse_command(text):
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    if re.fullmatch(r"(?:please )?(?:export|save|back up) my settings(?: to a file)?", low):
        return {"kind": "export"}
    m = re.fullmatch(r"(?:please )?import my settings(?: from (?:the )?(?:file )?(.+))?", low)
    if m:
        return {"kind": "import", "path": m.group(1)}
    m = re.fullmatch(r"(?:please )?reset (?:the |my )?([a-z ]{2,30}?) setting", low)
    if m:
        return {"kind": "reset", "name": m.group(1)}
    return None
