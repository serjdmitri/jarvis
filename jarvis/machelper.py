"""The Go's client for the silent Mac helper (mac/helper). Every request is signed with the SAME pairing key as the Mac inbox (never printed); the helper address comes from the signed hello the
helper posts when it starts (settings mac_helper_url, a private http address only). Nothing here ever hangs the UI: every call has a short timeout and the answer is a plain state.
States: "ready", "busy", "asleep" (unreachable) or "unset" (no helper known). Jobs asked while the Mac is asleep are queued in data/helper_queue.json and run when it is back."""
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config, mac, settings

QUEUE = config.DATA / "helper_queue.json"
ON_BACK = None                                          # set by the assistant: called (queued jobs) when an asleep Mac answers again
_st = {"state": "unset", "t": 0.0, "gens": []}


def url():
    u = settings.get("mac_helper_url") or ""
    return u if ok_url(u) else ""


def ok_url(u):
    p = urllib.parse.urlsplit(u or "")
    try:
        ip = ipaddress.ip_address(p.hostname or "")
    except ValueError:
        return False
    try:
        port = p.port or 80
    except ValueError:
        return False
    return p.scheme == "http" and ip.is_private and not p.username and port in range(1024, 65536) and p.path in ("", "/")


def remember(u):
    """From the signed hello: only a private http address is kept."""
    if ok_url(u):
        settings.update(mac_helper_url=u.rstrip("/"))
        return True
    return False


def _sign(k, method, path, ts, nonce, body):
    return hmac.new(k, f"{method}\n{path}\n{ts}\n{nonce}\n{hashlib.sha256(body).hexdigest()}".encode(), hashlib.sha256).hexdigest()


def request(method, path, body=b"", timeout=8, base=None):
    """-> (status, dict). status 0 = could not reach it. Never raises."""
    keys = mac._load()
    base = base or url()
    if not keys or not base:
        return 0, {"ok": False, "why": "no helper"}
    ts, nonce = int(time.time()), secrets.token_hex(8)
    h = {"X-Jarvis-Ts": str(ts), "X-Jarvis-Nonce": nonce, "X-Jarvis-Sig": _sign(keys[1], method, path, ts, nonce, body), "User-Agent": "jarvis-go", "Content-Length": str(len(body))}
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, data=body if method == "POST" else None, headers=h, method=method), timeout=timeout) as r:
            return r.status, json.loads(r.read(2_000_000) or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read(100_000))
        except ValueError:
            return e.code, {"ok": False}
    except (OSError, ValueError):
        return 0, {"ok": False, "why": "unreachable"}


def status(force=False):
    """-> "ready" | "busy" | "asleep" | "unset" (cached 20 s)."""
    if not url():
        return "unset"
    if not force and time.time() - _st["t"] < 20:
        return _st["state"]
    code, r = request("GET", "/health", timeout=3)
    was = _st["state"]
    if was == "asleep" and code == 200 and ON_BACK:
        try:
            n = len(_qload())
            threading.Thread(target=ON_BACK, args=(n,), daemon=True).start()          # "your Mac is back"
        except Exception:
            pass
    _st.update(state="asleep" if code != 200 else ("busy" if r.get("busy") else "ready"), t=time.time(), gens=r.get("generators", []) if code == 200 else [])
    if _st["state"] != "asleep" and _qload():
        threading.Thread(target=run_queue, daemon=True).start()      # queued saves go in the background: a status check never waits on them
    return _st["state"]


def save_file(path, name=None):
    """Save one file into ~/Downloads/Jarvis on the Mac. -> {"ok", "name", "quarantined"} ; "ok" is True ONLY after the helper confirms the saved file's size and hash (we check them again here).
    On any failure -> {"ok": False, "why": ...}: the caller falls back to the other path and says so."""
    try:
        if os.path.getsize(path) > 500 * 1024 * 1024:                # checked BEFORE reading, so a huge file never fills the memory
            return {"ok": False, "why": "over 500 MB"}
        data = open(path, "rb").read()
    except OSError:
        return {"ok": False, "why": "cannot read the file"}
    sha = hashlib.sha256(data).hexdigest()
    nm = name or os.path.basename(path)
    code, r = request("POST", "/save?" + urllib.parse.urlencode({"name": nm, "sha256": sha}), data, timeout=120)
    if code == 200 and r.get("ok") and r.get("size") == len(data) and r.get("sha256") == sha and r.get("name"):
        return {"ok": True, "name": r["name"], "quarantined": bool(r.get("quarantined"))}
    return {"ok": False, "why": (r.get("why") if isinstance(r, dict) else "") or ("the Mac helper is not reachable" if code == 0 else "the Mac did not confirm the save")}


STATES = {"received": "Mac helper: received it", "checking": "Mac helper: checking it", "working": "Mac helper: working on it", "optimizing": "Mac helper: optimizing", "ready": "Mac helper: ready", "failed": "Mac helper: failed"}


def job(kind, params, data, on_state=None, timeout=200):
    """Run one helper job and follow its states. -> {"ok", "state", "result" | "why", "cache"}. When the Mac is asleep the job is queued for later and the answer says so at once."""
    if status() in ("asleep", "unset"):
        queue_add(kind, params, data)
        return {"ok": False, "state": "queued", "why": "The Mac is asleep or unreachable. I queued it and will run it when it is back."}
    code, r = request("POST", "/job?" + urllib.parse.urlencode(dict(params, type=kind)), data, timeout=60)
    if code != 202:
        return {"ok": False, "state": "failed", "why": (r or {}).get("why", "the helper refused it")}
    jid, end, last = r["id"], time.time() + timeout, ""
    while time.time() < end:
        c, j = request("GET", f"/job/{jid}", timeout=6)
        if c != 200:
            return {"ok": False, "state": "failed", "why": "lost contact with the Mac helper"}
        if j["state"] != last and on_state:
            last = j["state"]
            on_state(j["state"], STATES.get(j["state"], j.get("text", "")))
        if j["state"] == "ready":
            return {"ok": True, "state": "ready", "result": j.get("result", {}), "cache": bool(j.get("cache"))}
        if j["state"] == "failed":
            return {"ok": False, "state": "failed", "why": j.get("text", "failed")}
        time.sleep(0.4)
    return {"ok": False, "state": "failed", "why": "timed out"}


# ----------------------------------------------------------------------------- queue for a sleeping Mac (paths only: the file stays where it is)
_qlock = threading.Lock()


def _qload():
    try:
        return json.loads(QUEUE.read_text())
    except (OSError, ValueError):
        return []


def queue_add(kind, params, data):
    if kind != "save-to-downloads":
        return                                           # model jobs carry photos: they are asked again, not stored
    q = _qload() + [{"path": params.get("path"), "name": params.get("name")}]
    config.DATA.mkdir(parents=True, exist_ok=True)
    QUEUE.write_text(json.dumps(q[-20:]))


def run_queue():
    q = _qload()
    if not q:
        return 0
    if not _qlock.acquire(blocking=False):
        return 0
    try:
        n, keep = 0, []
        for it in q:
            if not (it.get("path") and os.path.exists(it["path"])):
                continue                                              # the file is gone: nothing to save
            if save_file(it["path"], it.get("name"))["ok"]:
                n += 1
            else:
                keep.append(it)                                       # not saved: it stays queued for the next time the Mac is back
        QUEUE.write_text(json.dumps(keep[-20:]))
        return n
    finally:
        _qlock.release()
