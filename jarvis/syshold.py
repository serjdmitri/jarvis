"""While Jarvis runs, the system screensaver must not start (Jarvis has its own clock screen). Omarchy's idle.screensaver (seconds, in ~/.config/omarchy/shell.json)
is raised to 24 h and the original value is kept in ~/.config/jarvis/screensaver_orig.json and put back when Jarvis exits. The lock timer (idle.lock) is not touched."""
import json
import os

SHELL = os.path.expanduser("~/.config/omarchy/shell.json")
BACKUP = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "jarvis", "screensaver_orig.json")
HOLD = 86400


def _rw(mutate):
    try:
        data = json.load(open(SHELL))
    except (OSError, ValueError):
        return False
    if not mutate(data):
        return False
    tmp = SHELL + ".jarvis-tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, SHELL)
    return True


def hold():
    def m(d):
        idle = d.setdefault("idle", {})
        cur = idle.get("screensaver")
        if cur is None or cur >= HOLD:
            return False
        if not os.path.exists(BACKUP):                       # never overwrite the real original after a crash and restart
            os.makedirs(os.path.dirname(BACKUP), exist_ok=True)
            json.dump({"screensaver": cur}, open(BACKUP, "w"))
        idle["screensaver"] = HOLD
        return True
    return _rw(m)


def release():
    try:
        orig = json.load(open(BACKUP))["screensaver"]
    except (OSError, ValueError, KeyError):
        return False

    def m(d):
        d.setdefault("idle", {})["screensaver"] = orig
        return True
    ok = _rw(m)
    if ok:
        os.unlink(BACKUP)
    return ok
