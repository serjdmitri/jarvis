"""Use a Bluetooth speaker (for example an Echo Dot that you pair as a plain speaker: say "Alexa, pair") for Jarvis's voice. The Echo is NOT flashed or changed; Home Assistant is not installed.
Nothing about the audio setup changes until you press YES on the card. The microphone stays on the Go. If the speaker disconnects, PipeWire falls back to the Go's own speaker by itself."""
import re
import subprocess


def _run(argv, t=4):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=t).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def parse_command(text):
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    if re.fullmatch(r"(?:please )?(?:use|connect|pair|set up|setup) (?:my |the )?(?:echo|echo dot|alexa|bluetooth speaker|speaker)(?: as (?:a |my )?(?:speaker|bluetooth speaker))?(?: for (?:your voice|jarvis))?|(?:please )?(?:play|talk|speak) (?:through|on|from) (?:my |the )?(?:echo|echo dot|alexa|bluetooth speaker)", low):
        return {"kind": "setup"}
    if re.fullmatch(r"(?:please )?(?:go back to|use|switch to) (?:the )?(?:go'?s? |built.?in |own |normal )?speakers?(?: again)?|(?:please )?stop using (?:the |my )?(?:echo|bluetooth speaker)", low):
        return {"kind": "back"}
    return None


def sinks():
    """-> [(id, name, is_default)] from wpctl, Bluetooth ones flagged by name."""
    out, res, in_sinks = _run(["wpctl", "status"]), [], False
    for ln in out.splitlines():
        if "Sinks:" in ln:
            in_sinks = True
            continue
        if in_sinks and re.search(r"(Sources|Filters|Streams):", ln):
            break
        m = re.search(r"(\*)?\s+(\d+)\.\s+(.+?)\s+\[vol", ln) if in_sinks else None
        if m:
            res.append((int(m.group(2)), m.group(3).strip(), bool(m.group(1))))
    return res


def bluetooth_sinks():
    return [s for s in sinks() if re.search(r"bluez|bluetooth|echo|alexa", s[1], re.I)]


def card():
    bt = bluetooth_sinks()
    status = ("Connected: " + ", ".join(n for _, n, _ in bt)) if bt else "No Bluetooth speaker connected yet"
    steps = [{"n": 1, "text": 'Say to the Echo: "Alexa, pair". It then waits for a new device for about 5 minutes.'},
             {"n": 2, "text": "On the Go open Bluetooth (the Omarchy menu, or the bluetooth tool), find the Echo in the list and pair it. It appears as a speaker."},
             {"n": 3, "text": "Press YES below once it is connected: Jarvis's voice and sounds then play through it, and the Go's own speaker is used again if it disconnects. This only changes the default speaker; nothing else.",
              "buttons": ([{"label": f"YES, USE {n[:24].upper()}", "action": "bt_use", "payload": {"id": i}} for i, n, _ in bt[:2]] or [{"label": "CHECK AGAIN", "action": "bt_check", "payload": {}}]) +
                         [{"label": "BACK TO THE GO SPEAKER", "action": "bt_back", "payload": {}}]}]
    return {"type": "guide", "gid": "btspeaker", "title": "ECHO AS A SPEAKER", "steps": steps, "paged": False, "status": status}


def use(sink_id):
    ids = {i for i, _, _ in bluetooth_sinks()}
    if sink_id not in ids:
        return False
    return subprocess.run(["wpctl", "set-default", str(sink_id)], capture_output=True, timeout=4).returncode == 0


def back():
    builtin = [i for i, n, _ in sinks() if re.search(r"built.?in|analog|speaker", n, re.I) and not re.search(r"bluez|bluetooth", n, re.I)]
    return bool(builtin) and subprocess.run(["wpctl", "set-default", str(builtin[0])], capture_output=True, timeout=4).returncode == 0
