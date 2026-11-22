"""The self-test (voice: "run a self test", settings > Advanced > SELF-TEST): sound, mic, cameras, network, map tiles, music, disk, memory and the saved settings. Each line is PASS or FAIL
with a plain fix. Nothing is changed; the cameras are not opened (only the saved phone address is probed with a TCP connect), and nothing is recorded."""
import glob
import re
import shutil
import socket
import subprocess
import urllib.parse
import urllib.request

from . import config, settings


def _run(argv, t=4):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=t).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def checks():
    out = []
    def add(name, ok, fix=""):
        out.append({"name": name, "ok": bool(ok), "fix": "" if ok else fix})
    st = _run(["wpctl", "status"])
    add("sound output", bool(re.search(r"Sinks:.*?\d+\.\s", st, re.S)) and "MUTED" not in _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"]), "Check the speaker is selected and not muted (wpctl status).")
    add("microphone", bool(re.search(r"Sources:.*?\d+\.\s", st, re.S)) and "MUTED" not in _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"]), "Check the microphone is on and not muted.")
    from . import phonecam
    u = phonecam.get_url()
    if u:
        p = urllib.parse.urlsplit(u)
        try:
            socket.create_connection((p.hostname, p.port or 80), timeout=2).close()
            add("phone camera", True)
        except OSError:
            add("phone camera", False, "Open DroidCam in front on the iPhone, awake, on the same Wi-Fi.")
    else:
        add("phone camera", True, "(none saved)")
    usb = [d for d in glob.glob("/dev/video*") if "ipu3" not in _run(["udevadm", "info", "-q", "property", "-n", d]).lower() and "intel" not in _run(["udevadm", "info", "-q", "property", "-n", d]).lower()]
    add("USB webcam", True, "(none plugged in)" if not usb else "")
    try:
        urllib.request.urlopen(urllib.request.Request("https://api.open-meteo.com/v1/forecast?latitude=0&longitude=0&current=temperature_2m", headers={"User-Agent": "jarvis-local"}), timeout=5).read(100)
        add("network", True)
    except Exception:
        add("network", False, "No internet: check the Wi-Fi. Timers, notes, lists, the clock and cached maps still work.")
    try:
        from . import tilecache
        data, _, how = tilecache.get("osm", "3/2/3.png")
        add("map tiles", len(data) > 100, "Map tiles are not loading: the map shows a plain background and cached places.")
    except Exception:
        add("map tiles", False, "Map tiles are not loading: the map shows a plain background and cached places.")
    from . import spotify
    add("music", spotify.connected() or bool(shutil.which("yt-dlp")), "Install yt-dlp, or connect Spotify.")
    du = shutil.disk_usage(str(config.ROOT))
    add("disk space", du.free > 1_000_000_000, f"Only {du.free // 1_000_000} MB free: clear the cache (settings) or delete old downloads.")
    try:
        avail = int(re.search(r"MemAvailable:\s+(\d+)", open("/proc/meminfo").read()).group(1)) // 1024
    except (OSError, AttributeError):
        avail = 9999
    add("memory", avail > 300, f"Only {avail} MB of memory free: close big windows.")
    from . import settingsio
    pr = settingsio.problems()
    add("saved settings", not pr, "; ".join(pr[:2]))
    return out


def summary(res):
    bad = [r["name"] for r in res if not r["ok"]]
    return "Everything checks out." if not bad else f"{len(bad)} thing{'s' if len(bad) != 1 else ''} to fix: " + ", ".join(bad[:4]) + "."


def card(res):
    return {"type": "list", "gid": "selftest", "title": "Self-test", "items": [("PASS  " if r["ok"] else "FAIL  ") + r["name"] + (("  —  " + r["fix"]) if r["fix"] else "") for r in res]}
