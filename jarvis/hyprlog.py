"""Window event logger for the "random window glitch" hunt: listens on Hyprland's event socket (socket2) and writes window open / close / move / resize / fullscreen / focus events
with a timestamp, class, title, PID and the parent process to logs/windows.log. No secrets are written (titles of browser tabs are cut to 60 characters). The log is rotated at 2 MB.
Run: .venv/bin/python -m jarvis.hyprlog   (started and kept alive by the supervisor). Summary of "something opened and closed within 3 s": python -m jarvis.hyprlog --short"""
import json
import os
import socket
import subprocess
import sys
import time

from . import config

LOG = config.ROOT / "logs" / "windows.log"
WATCH = {"openwindow", "closewindow", "movewindow", "movewindowv2", "windowtitle", "fullscreen", "changefloatingmode", "activewindowv2", "workspacev2", "urgent", "pin", "openlayer", "closelayer"}


def _hl(*a):
    try:
        return json.loads(subprocess.run(["hyprctl", "-j", *a], capture_output=True, text=True, timeout=3).stdout or "null")
    except Exception:
        return None


def _parent(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            ppid = int(f.read().rsplit(")", 1)[1].split()[1])
        with open(f"/proc/{ppid}/cmdline", "rb") as f:
            return f"{ppid}:{f.read().replace(chr(0), ' ').decode('utf-8', 'ignore')[:70].strip()}"
    except Exception:
        return "?"


def _write(line):
    LOG.parent.mkdir(exist_ok=True)
    try:
        if LOG.exists() and LOG.stat().st_size > 2_000_000:
            LOG.replace(LOG.with_suffix(".log.1"))
    except OSError:
        pass
    with open(LOG, "a") as f:
        f.write(time.strftime("%H:%M:%S ") + f"{time.time() % 1:.3f}"[1:] + " " + line + "\n")


def run():
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    path = os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "hypr", sig, ".socket2.sock")
    s = socket.socket(socket.AF_UNIX)
    s.connect(path)
    known = {}
    _write("logger started")
    buf = b""
    while True:
        d = s.recv(8192)
        if not d:
            break
        buf += d
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            ev, _, data = line.decode("utf-8", "ignore").partition(">>")
            if ev not in WATCH:
                continue
            extra = ""
            if ev == "openwindow":
                addr = "0x" + data.split(",")[0]
                for c in _hl("clients") or []:
                    if c.get("address") == addr:
                        known[addr] = (c["class"], c["pid"])
                        extra = f" pid={c['pid']} parent={_parent(c['pid'])} floating={c['floating']} fullscreen={c['fullscreen']} size={c['size']} ws={c['workspace']['name']}"
            elif ev in ("closewindow", "fullscreen", "movewindowv2", "windowtitle", "activewindowv2"):
                k = known.get("0x" + data.split(",")[0].strip())
                extra = f" ({k[0]} pid={k[1]})" if k else ""
            _write(f"{ev} {data[:90]}{extra}")


def short(seconds=3.0):
    """Windows that opened and closed within `seconds` (the glitch signature)."""
    opened, out = {}, []
    for ln in open(LOG, errors="ignore") if LOG.exists() else []:
        p = ln.split(" ", 2)
        if len(p) < 3:
            continue
        t = time.mktime(time.strptime(time.strftime("%Y-%m-%d ") + p[0], "%Y-%m-%d %H:%M:%S")) + float(p[1])
        kind, _, rest = p[2].partition(" ")
        addr = rest.split(",")[0]
        if kind == "openwindow":
            opened[addr] = (t, ln.strip())
        elif kind == "closewindow" and addr in opened and t - opened[addr][0] < seconds:
            out.append(f"{t - opened[addr][0]:.2f}s  {opened[addr][1]}")
    return out


if __name__ == "__main__":
    if "--short" in sys.argv:
        print("\n".join(short()) or "no window opened and closed within 3 s")
    else:
        while True:
            try:
                run()
            except Exception as e:
                try:
                    _write(f"logger error {type(e).__name__}")
                except Exception:
                    pass
            time.sleep(3)
