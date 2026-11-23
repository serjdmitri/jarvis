"""Keep the Go awake while Jarvis is running. Two layers, neither edits the idle config or any power setting:
1. the kiosk page holds the browser's Screen Wake Lock (Chromium turns it into a Wayland idle inhibitor that the desktop's idle service respects), see app.js;
2. this process holds a systemd idle inhibitor (`systemd-inhibit --what=idle`, mode block) for as long as Jarvis lives: a tiny helper that exits by itself when Jarvis is gone, so it is released when Jarvis closes
   (even after a crash). Only "idle" is held, never "sleep", so suspending or closing the lid by hand still works."""
import os
import shutil
import subprocess

from . import settings

_proc = {"p": None}


def enabled():
    return settings.get("keep_awake") is not False


def start():
    if not enabled() or _proc["p"] is not None or not shutil.which("systemd-inhibit"):
        return False
    try:
        _proc["p"] = subprocess.Popen(["systemd-inhibit", "--what=idle", "--who=Jarvis", "--why=Jarvis is open", "--mode=block", "sh", "-c",
                                       'while kill -0 "$1" 2>/dev/null; do sleep 3; done', "sh", str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def stop():
    p, _proc["p"] = _proc["p"], None
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()


def held():
    """True while the inhibitor is registered with logind (read from `systemd-inhibit --list`)."""
    try:
        out = subprocess.run(["systemd-inhibit", "--list", "--no-pager"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return any(ln.startswith("Jarvis") and " idle " in ln for ln in out.splitlines())
