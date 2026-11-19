"""jarvis-doctor: checks the setup and says in plain words what is missing and how to fix it. Exit code 1 if something REQUIRED is missing.
Run:  bin/jarvis-doctor      (it never prints your settings, location, tokens or topics; it only says whether they are set)"""
import importlib
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OK, WARN, FAIL = "ok  ", "WARN", "FAIL"


def have(cmd):
    return shutil.which(cmd) is not None


def mod(name):
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def run(cmd, t=6):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=t)
    except Exception:
        return None


def checks():
    rows = []                                                       # (status, what, hint)

    def add(ok, what, hint="", required=True):
        rows.append((OK if ok else (FAIL if required else WARN), what, "" if ok else hint))
    add(sys.version_info >= (3, 11), f"Python {sys.version.split()[0]}", "Python 3.11 or newer")
    for m, pkg in (("aiohttp", "aiohttp"), ("faster_whisper", "faster-whisper"), ("piper", "piper-tts"), ("sherpa_onnx", "sherpa-onnx"), ("numpy", "numpy")):
        add(mod(m), f"python package {pkg}", f".venv/bin/pip install {pkg}  (or run ./install.sh)")
    add(mod("gi"), "python-gobject / GTK4 (the orb)", "install python-gobject, gtk4 and gtk4-layer-shell with your package manager", required=False)
    for c, hint, req in (("claude", "install Claude Code and log in with `claude` (the brain; no API key is used)", True), ("ffmpeg", "install ffmpeg", True), ("pw-record", "install pipewire (pw-record / pw-play)", True),
                         ("pw-play", "install pipewire", True), ("wpctl", "install wireplumber", True), ("chromium", "install chromium (the full-screen page)", True), ("hyprctl", "this is built for Hyprland", True),
                         ("grim", "install grim (screenshots for look)", False), ("tesseract", "install tesseract (read this to me)", False), ("qrencode", "install qrencode (QR codes)", False),
                         ("wtype", "install wtype (dictation)", False), ("brightnessctl", "install brightnessctl (scenes, quiet hours)", False), ("yt-dlp", "install yt-dlp (video reader)", False),
                         ("notify-send", "install libnotify", False)):
        add(have(c), f"program {c}", hint, required=req)
    models = ROOT / "models"
    add(any(models.glob("en_*.onnx")), "a Piper voice in models/", "run ./install.sh (it downloads en_GB-alan-medium and en_US-lessac-medium)")
    add((models / "kws").is_dir() and any((models / "kws").glob("encoder-*.onnx")), "wake-word model in models/kws", "run ./install.sh", required=False)
    r = run(["claude", "--version"]) if have("claude") else None
    add(bool(r and r.returncode == 0), "claude runs", "run `claude` once in a terminal and log in")
    s = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "jarvis" / "settings.json"
    st = {}
    try:
        import json
        st = json.loads(s.read_text())
    except Exception:
        pass
    add(bool(st.get("home_lat")), "home location is set", "say \"set my location\" (type your address in the card)", required=False)
    add(bool(st.get("phone_cam_url")), "phone camera address is set", "SETTINGS > Camera > Phone camera (optional)", required=False)
    add((s.parent / "mac.json").exists(), "a Mac is paired", "say \"pair my Mac\" (optional)", required=False)
    add((s.parent / "spotify_tokens.json").exists(), "Spotify is connected", "say \"set up Spotify\" (optional, needs Premium)", required=False)
    out = run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"]) if have("wpctl") else None
    add(bool(out and out.stdout.strip()), "an audio output exists", "check `wpctl status`")
    out = run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"]) if have("wpctl") else None
    add(bool(out and out.stdout.strip()), "a microphone exists", "check `wpctl status`")
    try:
        sk = socket.socket()
        sk.settimeout(0.3)
        busy = sk.connect_ex(("127.0.0.1", int(os.environ.get("JARVIS_PORT", "8765")))) == 0
        sk.close()
        rows.append((OK, "port " + os.environ.get("JARVIS_PORT", "8765") + (" is in use (Jarvis is running)" if busy else " is free"), ""))
    except OSError:
        pass
    return rows


def main():
    rows = checks()
    w = max(len(r[1]) for r in rows)
    for status, what, hint in rows:
        print(f"[{status}] {what.ljust(w)}  {hint}")
    bad = [r for r in rows if r[0] == FAIL]
    print("\n" + (f"{len(bad)} required thing(s) missing: fix the FAIL lines above." if bad else "Everything required is in place. WARN lines are optional features."))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
