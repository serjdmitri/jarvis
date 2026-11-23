"""A speed budget with a log that flags anything over its target, and the heat governor. Targets on this Go (PROMPT27 item 11):
  local_cmd      simple local command answered            < 300 ms
  card_open      a screen / card shown                    < 150 ms (server side: time to the first visual after the words)
  first_word     first spoken word of a normal answer     < 1500 ms
  wake_listen    wake word to listening                   < 200 ms
  idle_wake      idle to wake animation starts            < 100 ms
  model_light    3D view opens with the light model       < 500 ms
The last 50 measurements are kept (data/perf.jsonl, one line each, no private text) and shown in Settings > Advanced > PERF.
Heat: this Go has no fan. Above 78 C the page goes to lite mode by itself (said once) and comes back below 68 C."""
import json
import os
import time
from collections import deque

from . import config

TARGETS = {"local_cmd": 300, "card_open": 150, "first_word": 1500, "wake_listen": 200, "idle_wake": 100, "model_light": 500}
LOG = deque(maxlen=50)
FILE = config.DATA / "perf.jsonl"


def record(name, ms, note=""):
    t = TARGETS.get(name)
    row = {"t": time.time(), "name": name, "ms": round(ms), "target": t, "over": bool(t and ms > t), "note": str(note)[:40]}
    LOG.append(row)
    try:
        config.DATA.mkdir(parents=True, exist_ok=True)
        with open(FILE, "a") as f:
            f.write(json.dumps(row) + "\n")
        if FILE.stat().st_size > 200_000:
            lines = FILE.read_text().splitlines()[-200:]
            FILE.write_text("\n".join(lines) + "\n")
    except OSError:
        pass
    return row


def from_timing(marks, local):
    """Called with a finished turn's marks (seconds). Records what the budget covers."""
    if local and "intent" in marks:
        record("local_cmd", marks["intent"] * 1000)
    if "first_visual" in marks:
        record("card_open", marks["first_visual"] * 1000)
    if "first_audio" in marks:
        record("first_word", marks["first_audio"] * 1000, "local" if local else "brain")


def card():
    rows = list(LOG)[::-1]
    items = [f"{'OVER ' if r['over'] else 'ok   '}{r['name']:<12} {r['ms']:>5} ms" + (f"  (target {r['target']})" if r["target"] else "") for r in rows[:50]]
    over = sum(1 for r in rows if r["over"])
    return {"type": "list", "gid": "perf", "title": f"PERF · {over} OVER of {len(rows)}", "items": items or ["No measurements yet. Ask me a few things."]}


def cpu_temp_c():
    """The hottest of the CPU package / ACPI zones, in C, or None."""
    best = None
    try:
        for z in os.scandir("/sys/class/thermal"):
            if z.name.startswith("thermal_zone"):
                t = open(z.path + "/type").read().strip()
                if t in ("x86_pkg_temp", "acpitz"):
                    v = int(open(z.path + "/temp").read()) / 1000.0
                    best = v if best is None else max(best, v)
    except (OSError, ValueError):
        return None
    return best


HOT, COOL = 78.0, 68.0
_hot = {"on": False}


def governor():
    """-> "hot" when it just crossed the limit, "cool" when it just recovered, else None."""
    t = cpu_temp_c()
    if t is None:
        return None
    if not _hot["on"] and t >= HOT:
        _hot["on"] = True
        return "hot"
    if _hot["on"] and t <= COOL:
        _hot["on"] = False
        return "cool"
    return None
