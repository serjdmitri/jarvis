"""The Spotify Connect player on the Go: a user service (systemd --user) named exactly "Jarvis" that plays through PipeWire.
Spotify's app on the Mac or iPhone then shows "Jarvis" as a speaker; the Web API (spotify.py) searches and controls it.
Player: spotifyd (official Arch extra repo, GPL-3.0, about 3.7 MiB download). It is installed with: sudo pacman -S spotifyd   (needs your password: Jarvis never enters it).
No password or token is ever written here: the player signs in by Spotify's own zeroconf way (you tap "Jarvis" once in the Spotify app); its login cache stays in its own cache folder."""
import os
import re
import shutil
import subprocess

from . import settings

NAME = "Jarvis"
UNIT = "jarvis-spotifyd.service"
INSTALL_CMD = "sudo pacman -S spotifyd"


def _home():
    return os.environ.get("HOME") or os.path.expanduser("~")


def conf_path():
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(_home(), ".config"), "spotifyd", "spotifyd.conf")


def unit_path():
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(_home(), ".config"), "systemd", "user", UNIT)


def binary():
    return shutil.which("spotifyd")


def config_text(bitrate=160):
    return ("# written by Jarvis. No account details here: the first sign-in is a tap on \"Jarvis\" in your Spotify app (zeroconf).\n"
            "[global]\n"
            f"device_name = \"{NAME}\"\n"
            "device_type = \"speaker\"\n"
            "backend = \"pulseaudio\"\n"                      # PipeWire answers the PulseAudio protocol (pipewire-pulse)
            f"bitrate = {int(bitrate)}\n"
            "volume_controller = \"softvol\"\n"
            "initial_volume = \"60\"\n"
            "volume_normalisation = true\n"
            "autoplay = false\n"
            "use_mpris = false\n"
            "no_audio_cache = true\n")


def unit_text(exe):
    return ("[Unit]\nDescription=Jarvis Spotify Connect player\nAfter=pipewire-pulse.service network-online.target\n\n"
            f"[Service]\nExecStart={exe} --no-daemon --config-path %h/.config/spotifyd/spotifyd.conf\nRestart=always\nRestartSec=5\n\n"
            "[Install]\nWantedBy=default.target\n")


def _sc(*args, timeout=10):
    try:
        r = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def write_files():
    """Config and unit file in the user's own folders (nothing system-wide). Never overwrites a config you edited yourself."""
    exe = binary()
    if not exe:
        return False
    os.makedirs(os.path.dirname(conf_path()), exist_ok=True)
    if not os.path.exists(conf_path()):
        with open(conf_path(), "w") as f:
            f.write(config_text())
    os.makedirs(os.path.dirname(unit_path()), exist_ok=True)
    with open(unit_path(), "w") as f:
        f.write(unit_text(exe))
    return True


def active():
    return _sc("is-active", UNIT)[1] == "active"


def enabled():
    return _sc("is-enabled", UNIT)[1] == "enabled"


def start():
    """Write the files, enable at login, start now. -> (ok, plain words)."""
    if not binary():
        return False, not_installed()
    write_files()
    _sc("daemon-reload")
    rc, _ = _sc("enable", "--now", UNIT, timeout=20)
    return (rc == 0 and active()), ("The Jarvis player is running." if rc == 0 and active() else "I could not start the Jarvis player (look at: journalctl --user -u %s)." % UNIT)


def not_installed():
    return ("The Spotify player is not installed on the Go yet. Run this once, it asks for your password: " + INSTALL_CMD +
            "  (spotifyd, official Arch repository, GPL-3.0, about 4 MB). Then press CHECK again.")


def state():
    """-> {"installed", "configured", "active", "enabled", "plain"}"""
    inst, act = bool(binary()), active() if binary() else False
    st = {"installed": inst, "configured": os.path.exists(conf_path()) and os.path.exists(unit_path()), "active": act, "enabled": enabled() if inst else False}
    st["plain"] = ("Spotify player: running as Jarvis" if act else "Spotify player: installed but not running" if inst else "Spotify player: not installed")
    return st


def restart():
    if not binary():
        return False, not_installed()
    _sc("restart", UNIT, timeout=20)
    return active(), "Restarted the Jarvis player." if active() else "The Jarvis player did not start."


# ------------------------------------------------------------------------------ speakers and volume
def outputs():
    """The audio outputs PipeWire knows: [{"id", "name", "default": bool, "kind": "bluetooth"|"wired"|"other"}]. Read only: this never changes your default output."""
    try:
        txt = subprocess.run(["wpctl", "status"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    out, in_sinks = [], False
    for line in txt.splitlines():
        if re.search(r"Sinks:", line):
            in_sinks = True
            continue
        if in_sinks and re.search(r"(Sources|Filters|Streams):", line):
            break
        if in_sinks:
            m = re.search(r"(\*)?\s*(\d+)\.\s+(.+?)\s+\[vol:", line)
            if m:
                nm = m.group(3).strip()
                out.append({"id": int(m.group(2)), "name": nm, "default": bool(m.group(1)), "kind": "bluetooth" if re.search(r"bluez|bluetooth", nm, re.I) else "wired" if re.search(r"analog|headphone|usb|hdmi|speaker", nm, re.I) else "other"})
    return out


def max_volume():
    try:
        return max(10, min(100, int(settings.get("spotify_volume_max", 80))))
    except (TypeError, ValueError):
        return 80


def cap(pct):
    """The Jarvis volume never goes above the maximum you set."""
    return max(0, min(max_volume(), int(pct)))


def _stream_ids():
    try:
        txt = subprocess.run(["pactl", "list", "sink-inputs"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    ids = []
    for blk in re.split(r"\n(?=Sink Input #)", txt):
        m = re.match(r"Sink Input #(\d+)", blk)
        if m and re.search(r"application\.name = \"(?:spotifyd|librespot)", blk, re.I):
            ids.append(m.group(1))
    return ids


def duck(on, level=25):
    """Lower the player's own stream while Jarvis listens or speaks, bring it back after (works without the Web API, instant)."""
    for i in _stream_ids():
        try:
            subprocess.run(["pactl", "set-sink-input-volume", i, f"{level if on else 100}%"], capture_output=True, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return bool(_stream_ids())
