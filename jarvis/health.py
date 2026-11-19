"""Machine health: "how's the machine" shows and speaks battery and charging, CPU and case temperature, RAM and swap, disk space and Wi-Fi strength.
check() is run once a minute in the background: high temperature, RAM above 85 percent or battery below 15 percent (discharging) gives ONE short warning and a card
(again only after the problem has cleared), and an offer to free memory (which only closes Jarvis's own extra windows; nothing is ever killed without asking)."""
import glob
import os
import re
import shutil
import time

TEMP_WARN_C, RAM_WARN, BAT_WARN = 82, 92, 15       # RAM: above 92 percent, and only if it STAYS there (this 4 GB machine sits at 80-90 all the time)
RAM_CRIT, SWAP_HEAVY, SUSTAIN_S, COOLDOWN_S = 97, 70, 60, 1800
_warned = set()
_since = {}                                         # problem -> when it started (must last SUSTAIN_S before it counts)
_last = {}                                          # problem -> when it was last announced (at most once per 30 minutes)


def _read(p, default=None):
    try:
        return open(p).read().strip()
    except OSError:
        return default


def battery():
    for d in glob.glob("/sys/class/power_supply/BAT*"):
        cap, st = _read(d + "/capacity"), (_read(d + "/status") or "").lower()
        if cap and cap.isdigit():
            return {"pct": int(cap), "state": st or "unknown", "charging": st in ("charging", "full")}
    return None


def temps():
    """-> {"cpu": C, "case": C or None} from the thermal zones."""
    cpu, case = None, []
    for z in glob.glob("/sys/class/thermal/thermal_zone*"):
        t, v = _read(z + "/type", ""), _read(z + "/temp")
        if not v or not v.lstrip("-").isdigit():
            continue
        c = int(v) / 1000
        if t in ("x86_pkg_temp", "coretemp"):
            cpu = c if cpu is None else max(cpu, c)
        elif t in ("acpitz", "SEN1", "SEN2", "SEN3"):
            case.append(c)
    return {"cpu": cpu, "case": (sum(case) / len(case)) if case else None}


def memory():
    m = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":")
        m[k] = int(v.split()[0])
    used = m["MemTotal"] - m["MemAvailable"]
    return {"ram_pct": round(100 * used / m["MemTotal"]), "ram_gb": used / 1048576, "swap_gb": (m["SwapTotal"] - m["SwapFree"]) / 1048576, "swap_total_gb": m["SwapTotal"] / 1048576}


def disk():
    u = shutil.disk_usage("/")
    return {"pct": round(100 * u.used / u.total), "free_gb": u.free / 1e9}


def wifi():
    """-> {"quality": 0-100, "word": strong|fair|weak} or None. From /proc/net/wireless (link quality out of 70)."""
    try:
        for line in open("/proc/net/wireless").read().splitlines()[2:]:
            parts = line.split()
            q = float(parts[2].rstrip("."))
            pct = max(0, min(100, round(q / 70 * 100)))
            return {"quality": pct, "word": "strong" if pct >= 66 else "fair" if pct >= 40 else "weak"}
    except (OSError, ValueError, IndexError):
        pass
    return None


def snapshot():
    return {"battery": battery(), "temps": temps(), "memory": memory(), "disk": disk(), "wifi": wifi()}


def report():
    """-> (spoken text, list of card lines)."""
    s = snapshot()
    sp, lines = [], []
    b = s["battery"]
    if b:
        lines.append(f"Battery  {b['pct']}%  {'charging' if b['charging'] else 'on battery'}")
        sp.append(f"Battery {b['pct']} percent, {'charging' if b['charging'] else 'not charging'}.")
    t = s["temps"]
    if t["cpu"] is not None:
        lines.append(f"CPU  {t['cpu']:.0f} °C" + (f"   case  {t['case']:.0f} °C" if t["case"] is not None else ""))
        sp.append(f"The processor is at {t['cpu']:.0f} degrees" + (f" and the case about {t['case']:.0f}." if t["case"] is not None else "."))
    m = s["memory"]
    lines.append(f"RAM  {m['ram_pct']}%  ({m['ram_gb']:.1f} GB)   swap  {m['swap_gb']:.1f} GB")
    sp.append(f"Memory is {m['ram_pct']} percent used, and swap is {m['swap_gb']:.1f} gigabytes.")
    d = s["disk"]
    lines.append(f"Disk  {d['pct']}% used  ({d['free_gb']:.0f} GB free)")
    sp.append(f"The disk is {d['pct']} percent full, {d['free_gb']:.0f} gigabytes free.")
    w = s["wifi"]
    if w:
        lines.append(f"Wi-Fi  {w['word']}  ({w['quality']}%)")
        sp.append(f"Wi-Fi is {w['word']}.")
    return " ".join(sp), lines


def problems():
    """-> {key: short spoken warning} for what is wrong right now (before the sustain and cooldown rules)."""
    out = {}
    t, m, b = temps(), memory(), battery()
    hot = max([x for x in (t["cpu"], t["case"]) if x is not None] or [0])
    if hot >= TEMP_WARN_C:
        out["temp"] = f"The machine is running hot, {hot:.0f} degrees."
    swap_pct = round(100 * m["swap_gb"] / m["swap_total_gb"]) if m["swap_total_gb"] else 0
    if m["ram_pct"] >= RAM_WARN:
        out["ram"] = f"Memory is {m['ram_pct']} percent full."
    elif swap_pct >= SWAP_HEAVY and m["ram_pct"] >= 85:
        out["ram"] = f"Memory is busy and swap is {swap_pct} percent used."
    if m["ram_pct"] >= RAM_CRIT:
        out["critical"] = f"Memory is almost gone, {m['ram_pct']} percent. Something may be about to fail."
    if b and b["pct"] <= BAT_WARN and not b["charging"]:
        out["battery"] = f"Battery is low, {b['pct']} percent."
    if b and b["pct"] <= 7 and not b["charging"]:
        out["critical"] = f"Battery is almost empty, {b['pct']} percent."
    return out


def check(now=None):
    """-> [(key, spoken)] for problems that have lasted a minute (immediately for "critical") and were not announced in the last 30 minutes."""
    import time as _t
    now = now or _t.time()
    p = problems()
    for k in list(_since):
        if k not in p:
            _since.pop(k, None)
    new = []
    for k, v in p.items():
        _since.setdefault(k, now)
        if now - _since[k] < (0 if k == "critical" else SUSTAIN_S):
            continue
        if now - _last.get(k, -1e12) < COOLDOWN_S:
            continue
        _last[k] = now
        new.append((k, v))
    return new


def top_memory(n=3):
    """The biggest processes by memory, as (name, MB). Read-only: used to tell you what is using memory, never to kill anything."""
    rows = []
    for d in glob.glob("/proc/[0-9]*"):
        try:
            rss = int(re.search(r"VmRSS:\s+(\d+)", open(d + "/status").read()).group(1)) / 1024
            name = open(d + "/comm").read().strip()
            rows.append((name, rss))
        except (OSError, AttributeError):
            continue
    agg = {}
    for name, mb in rows:
        agg[name] = agg.get(name, 0) + mb
    return sorted(agg.items(), key=lambda x: -x[1])[:n]


def parse_command(text):
    low = text.lower().strip(" .?!")
    if re.fullmatch(r"how(?:'s| is) the (?:machine|computer|laptop|system|tablet|surface)(?: doing)?|machine (?:health|status)|system (?:health|status)|(?:how'?s|what'?s) (?:my )?battery(?: level)?|how much battery (?:do i have|is left)(?: left)?|check the machine", low):
        return {"kind": "report"}
    if re.fullmatch(r"(?:please )?free (?:up )?(?:some )?(?:the )?memory|(?:please )?free up (?:some )?ram", low):
        return {"kind": "free_ask"}
    if re.fullmatch(r"yes[, ]*(?:please )?free (?:up )?(?:the )?memory", low):
        return {"kind": "free_do"}
    return None
