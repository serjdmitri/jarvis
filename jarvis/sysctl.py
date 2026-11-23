"""System controls by touch and voice, for a desk with no keyboard: restart Jarvis, restart the Go, shut down, screen off and on, brightness, volume, mute.
Fixed commands only (argument lists, no shell), chosen from this list by the user's own touch or words; the model never runs any of them. Restart and shut down always ask for a confirmation first
(touch: a CONFIRM button; voice: say yes). Nothing here changes the keyboard, the power-button setting or the login."""
import re
import subprocess

CONFIRM = {"restart_jarvis": "Restart Jarvis?", "reboot": "Restart the Go?", "poweroff": "Shut down the Go?"}
DONE = {"restart_jarvis": "Restarting Jarvis. I will be back in a few seconds.", "reboot": "Restarting the Go. See you in a minute.", "poweroff": "Shutting down. Goodbye."}
ALLOWED = set(CONFIRM) | {"screen_off", "screen_on", "brightness"}


def _run(argv, timeout=6):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def run(op, arg=None, confirmed=False):
    """-> (ok, plain words). A restart or shut down without `confirmed` is refused."""
    if op not in ALLOWED:
        return False, "I can't do that."
    if op in CONFIRM and not confirmed:
        return False, CONFIRM[op] + " Please confirm."
    try:
        if op == "restart_jarvis":
            from . import updater
            ok, msg = updater.restart_server()
            return ok, DONE[op] if ok else msg
        if op == "reboot":
            _run(["systemctl", "reboot"], 10)
        elif op == "poweroff":
            _run(["systemctl", "poweroff"], 10)
        elif op == "screen_off":
            _run(["hyprctl", "dispatch", "dpms", "off"])
            return True, "Screen off. Touch it to wake it."
        elif op == "screen_on":
            _run(["hyprctl", "dispatch", "dpms", "on"])
            return True, "Screen on."
        elif op == "brightness":
            if str(arg).lstrip("+-").isdigit():
                v = int(arg)
                tgt = f"{max(5, min(100, v))}%" if not str(arg).startswith(("+", "-")) else f"{abs(v)}%{'+' if v > 0 else '-'}"
            else:
                return False, "How bright?"
            r = _run(["brightnessctl", "set", tgt])
            return r.returncode == 0, "Brightness changed." if r.returncode == 0 else "I could not change the brightness."
    except (OSError, subprocess.TimeoutExpired):
        return False, "I could not do that."
    return True, DONE.get(op, "Done.")


def parse_command(text):
    """-> {"op", "arg"?, "confirm": bool} or None. Whole-sentence matches only."""
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!,"))
    low = re.sub(r"^(?:please |hey jarvis |jarvis )+", "", low)
    if re.fullmatch(r"restart (?:jarvis|yourself|the assistant)|reboot jarvis", low):
        return {"op": "restart_jarvis", "confirm": True}
    if re.fullmatch(r"(?:restart|reboot)(?: the)?(?: go| computer| machine| surface| tablet| device)?|restart the go", low):
        return {"op": "reboot", "confirm": True}
    if re.fullmatch(r"(?:shut ?down|power off|turn off|switch off)(?: the)?(?: go| computer| machine| surface| tablet| device)?|shut it down", low):
        return {"op": "poweroff", "confirm": True}
    if re.fullmatch(r"(?:turn |switch )?(?:the )?screen off|(?:turn |switch )?off the screen|screen off|sleep the screen|blank the screen", low):
        return {"op": "screen_off", "confirm": False}
    if re.fullmatch(r"(?:turn |switch )?(?:the )?screen on|(?:turn |switch )?on the screen|wake the screen", low):
        return {"op": "screen_on", "confirm": False}
    m = re.fullmatch(r"(?:set )?(?:the )?brightness (?:to )?(\d{1,3})(?: ?%| percent)?", low)
    if m:
        return {"op": "brightness", "arg": str(min(100, int(m.group(1)))), "confirm": False}
    if re.fullmatch(r"(?:turn )?(?:the )?brightness up|brighter|(?:make it |make the screen )?brighter", low):
        return {"op": "brightness", "arg": "+15", "confirm": False}
    if re.fullmatch(r"(?:turn )?(?:the )?brightness down|dimmer|(?:make it |make the screen )?dimmer", low):
        return {"op": "brightness", "arg": "-15", "confirm": False}
    return None
