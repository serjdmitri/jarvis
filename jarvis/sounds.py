"""Short, pleasant UI sounds in each persona's style: a wake chime (up), a "finished listening" sound (down) and a soft error tone.

Each is a couple of soft notes with a little harmonic, generated once per (style, event, volume) into run/ and played with pw-play without blocking.
The wake chime plays the instant the wake phrase matches, before anything slow runs. Settings: `chimes` (on/off) and `chime_volume` (0-100)."""
import subprocess
import threading
import wave

import numpy as np

from . import config, personas, settings

STYLES = {                                   # style -> (notes in Hz, note length s, harmonic mix, attack s)
    "bright": ((880, 1320), 0.075, 0.30, 0.004),
    "warm":   ((523, 659), 0.10, 0.18, 0.012),
    "soft":   ((660, 784), 0.11, 0.05, 0.030),
    "low":    ((392, 523), 0.11, 0.22, 0.015),
}
SFX = {"open": ((587, 880), 0.035), "close": ((659, 440), 0.035), "menu": ((988,), 0.03), "send": ((523, 784, 1047), 0.045), "delivered": ((784, 1047), 0.07), "reminder": ((784, 988, 1175), 0.09), "notify": ((1175,), 0.08)}
_state = {"persona": "jarvis"}
_lock = threading.Lock()


def set_persona(name):
    _state["persona"] = name


def enabled():
    from . import whisper_mode
    return settings.get("chimes") is not False and not whisper_mode.active()          # whisper mode: no sound effects


def volume():
    try:
        return max(0, min(100, int(settings.get("chime_volume", 60))))
    except (TypeError, ValueError):
        return 60


def render(style, event, vol):
    """-> float32 samples (48 kHz mono). wake = rising pair, done = falling pair, error = two low soft notes."""
    notes, ln, harm, att = STYLES.get(style, STYLES["bright"])
    if event == "done":
        notes = notes[::-1]
    elif event == "error":
        notes, ln, harm, att = (233, 175), 0.15, 0.10, 0.02
    elif event in SFX:                                           # tiny effects for cards, menus, send, delivered, reminders, notifications (generated here, nothing downloaded)
        notes, ln = SFX[event]
        harm = 0.12
    gain = (vol / 100.0) * 0.45
    out = []
    for f in notes:
        n = int(48000 * ln)
        t = np.arange(n) / 48000
        env = np.minimum(1, t / att) * np.exp(-t * (7 if event != "error" else 4)) * np.minimum(1, (t[-1] - t) / 0.012)
        out.append((np.sin(2 * np.pi * f * t) + harm * np.sin(4 * np.pi * f * t)) * env)
    out.append(np.zeros(int(48000 * 0.04)))
    y = np.concatenate(out)
    return (y / max(1e-6, np.abs(y).max()) * gain).astype(np.float32)


def path_for(style, event, vol):
    config.RUN_DIR.mkdir(exist_ok=True)
    p = config.RUN_DIR / f"snd-{style}-{event}-{vol // 5 * 5}.wav"
    if not p.exists():
        with _lock:
            y = render(style, event, vol)
            tmp = str(p) + ".tmp"
            with wave.open(tmp, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
                w.writeframes((y * 32767).astype(np.int16).tobytes())
            import os
            os.replace(tmp, p)
    return str(p)


_pl = {"p": None}
_pcm = {}


def _player():
    """One long-running pw-cat that stays connected to the speakers and reads raw audio from a pipe: a chime is then a single write (a few ms) instead of starting a new pw-play process (about 100 ms)."""
    p = _pl["p"]
    if p and p.poll() is None:
        return p
    p = subprocess.Popen(["pw-cat", "-p", "--raw", "--rate", "48000", "--channels", "1", "--format", "s16", "--latency", "30ms", "-"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _pl["p"] = p
    return p


def _data(style, event, vol):
    k = (style, event, vol // 5 * 5)
    if k not in _pcm:
        _pcm[k] = (render(style, event, vol) * 32767).astype(np.int16).tobytes()
    return _pcm[k]


def warm():
    """At start-up: open the player and render this persona's three sounds, so the first wake chime is as fast as every other."""
    style = personas.PERSONAS.get(_state["persona"], {}).get("chime", "bright")
    try:
        for ev in ("wake", "done", "error"):
            _data(style, ev, volume())
        pl = _player()
        pl.stdin.write(b"\0\0" * 480)
        pl.stdin.flush()
    except (OSError, ValueError):
        pass


def play(event="wake", force=False):
    """Fire and forget (a pipe write). Returns the style played (or None when sounds are off). Falls back to a pw-play process if the player cannot be used."""
    if not (enabled() or force) or volume() == 0:
        return None
    style = personas.PERSONAS.get(_state["persona"], {}).get("chime", "bright")
    try:
        data = _data(style, event, volume())
        try:
            pl = _player()
            pl.stdin.write(data)
            pl.stdin.flush()
            return style
        except (BrokenPipeError, OSError, ValueError):
            _pl["p"] = None
        p = path_for(style, event, volume())
        subprocess.Popen(["pw-play", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return style
    except OSError:
        return None
