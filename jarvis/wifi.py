"""Wi-Fi by touch: list the networks around, pick one, type the password on the on-screen keyboard. Uses NetworkManager's nmcli with fixed argument lists (never a shell).
The password is passed on stdin (nmcli --ask), so it is not in any process list, and it is never printed, logged or stored by Jarvis (NetworkManager keeps its own saved connection).
Reading the list is harmless; connecting is only done when you press CONNECT for that network."""
import re
import subprocess
import time

_cache = {"t": 0.0, "rows": []}


def _nm(args, inp=None, timeout=20):
    return subprocess.run(["nmcli", *args], input=inp, capture_output=True, text=True, timeout=timeout)


def _split(line):
    """nmcli -t escapes ':' inside a field as '\\:'."""
    out, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur += ch; esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append(cur); cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def parse_list(text, known=()):
    """nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY output -> [{"ssid","signal","secured","connected","known"}], strongest first, one row per name, hidden names dropped."""
    best = {}
    for ln in (text or "").splitlines():
        f = _split(ln)
        if len(f) < 4 or not f[1].strip():
            continue
        ssid, sig = f[1].strip()[:64], int(f[2]) if f[2].isdigit() else 0
        row = {"ssid": ssid, "signal": sig, "secured": bool(f[3].strip()) and f[3].strip() != "--", "connected": f[0].strip() == "*", "known": ssid in known}
        old = best.get(ssid)
        if not old or row["connected"] or (not old["connected"] and sig > old["signal"]):
            best[ssid] = row
    return sorted(best.values(), key=lambda r: (not r["connected"], -r["signal"]))


def known_names():
    try:
        out = _nm(["-t", "-f", "NAME,TYPE", "con", "show"], timeout=6).stdout
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {_split(l)[0] for l in out.splitlines() if len(_split(l)) > 1 and "wireless" in _split(l)[1]}


def scan(rescan=False):
    """-> {"ok", "rows", "known_nearby": bool, "connected": ssid or ""} ; cached for 10 s."""
    if not rescan and time.time() - _cache["t"] < 10:
        rows = _cache["rows"]
    else:
        try:
            if rescan:
                _nm(["dev", "wifi", "rescan"], timeout=8)
            out = _nm(["-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list"], timeout=10).stdout
        except (OSError, subprocess.TimeoutExpired):
            return {"ok": False, "rows": [], "why": "I could not read the Wi-Fi list.", "connected": "", "known_nearby": False}
        rows = parse_list(out, known_names())
        _cache.update(t=time.time(), rows=rows)
    return {"ok": True, "rows": rows, "connected": next((r["ssid"] for r in rows if r["connected"]), ""), "known_nearby": any(r["known"] for r in rows)}


def connect(ssid, password=None):
    """-> (ok, plain words). Only a name from the last scan is accepted. A saved network connects without a password."""
    ssid = (ssid or "")[:64]
    if not any(r["ssid"] == ssid for r in _cache["rows"]):
        return False, "That network is not in the list: scan again."
    try:
        if password:
            r = _nm(["--ask", "dev", "wifi", "connect", ssid], inp=password + "\n", timeout=40)
        else:
            r = _nm(["dev", "wifi", "connect", ssid], timeout=40)
    except (OSError, subprocess.TimeoutExpired):
        return False, "The connection took too long. Check the password and try again."
    if r.returncode == 0:
        return True, f"Connected to {ssid}."
    err = (r.stderr or r.stdout or "").lower()
    if "secrets" in err or "password" in err or "no suitable" in err:
        return False, "That did not work. Check the password (it is case sensitive) and try again."
    return False, "I could not connect to that network."
