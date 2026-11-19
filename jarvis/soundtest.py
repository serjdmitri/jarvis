"""Sound test and "No sound" diagnosis. Reads the CURRENT default output every time (never a saved device name), so a sink that changed after a restart cannot be the cause.
diagnose() -> {"device","port","volume","muted","ok","reason"}; reason is plain words when something is wrong."""
import re
import subprocess
import time

_last_warn = 0.0


def _run(cmd, t=3):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=t).stdout
    except Exception:
        return ""


def diagnose():
    d = {"device": "", "port": "", "volume": None, "muted": None, "ok": True, "reason": ""}
    if not _run(["wpctl", "status"]).strip():
        d.update(ok=False, reason="the audio system (PipeWire) is not answering")
        return d
    vol = _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
    m = re.search(r"Volume:\s*([\d.]+)", vol)
    if not m:
        d.update(ok=False, reason="there is no output device")
        return d
    d["volume"], d["muted"] = round(float(m.group(1)) * 100), "MUTED" in vol
    insp = _run(["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"])
    g = lambda k: (re.search(k + r'\s*=\s*"([^"]*)"', insp) or [None, ""])[1]
    d["device"] = g("node.description") or g("node.name")
    name = g("node.name")
    port = re.search(r"Active Port:\s*(\S+)", _run(["pactl", "list", "sinks"]))
    d["port"] = port.group(1).replace("analog-output-", "") if port else ""
    if d["muted"]:
        d.update(ok=False, reason=f"the output {d['device']} is muted")
    elif d["volume"] < 5:
        d.update(ok=False, reason=f"the output volume is {d['volume']} percent")
    elif re.search(r"dummy|null|auto_null", name, re.I):
        d.update(ok=False, reason="the output is a dummy device, no speakers are connected to the audio system")
    return d


def describe(d):
    if not d["device"]:
        return "No output device"
    s = d["device"] + (f" ({d['port']})" if d["port"] else "") + f", volume {d['volume']}%" + (", MUTED" if d["muted"] else "")
    return s


def warn_card(reason):
    return {"type": "text", "title": "No sound", "text": "No sound: " + reason}


def check_failed_play():
    """Called when pw-play failed. -> a card to show (at most once a minute), or None."""
    global _last_warn
    if time.time() - _last_warn < 60:
        return None
    _last_warn = time.time()
    d = diagnose()
    return warn_card(d["reason"] or "the player could not open the output device (is the audio session running for this user?)")


def parse_command(text):
    low = text.lower().strip(" .?!")
    if re.fullmatch(r"(?:run )?(?:a )?(?:sound|audio|speaker|volume) (?:test|check)|test (?:the |my )?(?:sound|audio|speakers?)|can you hear me|(?:is|check if) (?:the )?sound (?:working|on)|(?:do )?(?:i|you) have sound", low):
        return {"kind": "test"}
    return None


def run_test(play_chime):
    """Plays the chime (play_chime() must block until done and return the exit code), -> (diagnosis, chime_exit_code)."""
    d = diagnose()
    code = play_chime()
    return d, code
