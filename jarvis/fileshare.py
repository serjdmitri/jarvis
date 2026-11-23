"""Send any file to the Mac (and later the phone) on the LOCAL network only. The Go serves ONE copy of the file from its own "outbox" folder at a one-time URL
(http://<Go LAN address>:8766/o/<long random token>) that works for the first download only and for 10 minutes at most. The signed message to the Mac carries only that URL, the name, the size
and the SHA-256; the Mac fetches it, checks the hash, saves it to "Jarvis Inbox" (never opens, runs or unzips it) and says "saved" or why not. Size cap 500 MB (setting share_cap_mb).
Files that look like secrets (keys, tokens, settings, ~/.ssh, ...) are refused. The listener runs only while a share is waiting. Nothing is opened to the internet or the router."""
import asyncio
import hashlib
import ipaddress
import os
import re
import secrets
import shutil
import socket
import threading
import time
import zipfile

from aiohttp import web

from . import config, settings

OUT = config.ROOT / "outbox"
PORT = int(os.environ.get("JARVIS_SHARE_PORT", "8766"))
TTL_S = 600
DEFAULT_CAP_MB = 500
SHARES = {}                                           # token -> {path, name, size, sha, exp, used}
_srv = {"loop": None, "runner": None, "thread": None, "ip": None}
_lock = threading.Lock()
SECRET_DIRS = (".ssh", ".gnupg", ".aws", ".kube", ".docker", ".config/jarvis", ".claude", ".password-store", ".local/share/keyrings", ".config/gcloud", ".config/chromium", ".cache/jarvis-ui", ".mozilla", ".config/gh", ".config/Mac", ".gitconfig-credentials", ".local/share/claude")
SECRET_NAMES = re.compile(r"(?:^|/)(?:id_[a-z0-9]+|.*\.pem|.*\.key|.*\.p12|\.env.*|.*token.*|.*secret.*|.*password.*|mac\.json|settings\.json|spotify.*|hook_token|\.netrc|\.npmrc|\.git-credentials|\.bash_history|\.zsh_history|\.pgpass|\.pypirc|.*\.kdbx|.*_history)$", re.I)


class Refused(Exception):
    """A plain-words reason a file is not shared."""


def cap_bytes():
    try:
        return max(1, int(settings.get("share_cap_mb") or DEFAULT_CAP_MB)) * 1024 * 1024
    except (TypeError, ValueError):
        return DEFAULT_CAP_MB * 1024 * 1024


def lan_ip():
    from . import voiceapi
    return voiceapi.lan_ip()


def check_source(path):
    """-> the real path of a file Jarvis may share, or raises Refused."""
    real = os.path.realpath(os.path.expanduser(str(path)))
    home = os.path.realpath(os.path.expanduser("~"))
    if not (real == home or real.startswith(home + os.sep)) and not (real == str(config.ROOT) or real.startswith(str(config.ROOT) + os.sep)):
        raise Refused("That file is outside your home folder.")
    rel = os.path.relpath(real, home)
    if any(rel == d or rel.startswith(d + os.sep) for d in SECRET_DIRS) or SECRET_NAMES.search(real):
        raise Refused("That file looks private (a key, token, password or setting), so I won't send it.")
    if not os.path.isfile(real):
        raise Refused("That is not a file.")
    if os.path.getsize(real) > cap_bytes():
        raise Refused(f"That file is over {cap_bytes() // 1024 // 1024} MB.")
    if os.path.getsize(real) == 0:
        raise Refused("That file is empty.")
    return real


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def find(name):
    """A file you named ("report.pdf", "my resume") in Downloads, Documents, Desktop, the exports and the screenshots: the newest one whose name contains every word. -> path or None."""
    words = [w for w in re.findall(r"[\w.-]+", name.lower()) if w not in ("the", "my", "file", "a", "called", "named")]
    if not words:
        return None
    home = os.path.expanduser("~")
    best = None
    for d in (os.path.join(home, "Downloads"), os.path.join(home, "Documents"), os.path.join(home, "Desktop"), str(config.ROOT / "exports"), str(config.SHOTS_DIR)):
        try:
            for f in os.scandir(d):
                if f.is_file() and all(w in f.name.lower() for w in words):
                    if best is None or f.stat().st_mtime > best[0]:
                        best = (f.stat().st_mtime, f.path)
        except OSError:
            continue
    return best[1] if best else None


EXEC_EXT = {".app", ".pkg", ".dmg", ".command", ".sh", ".exe", ".bat", ".cmd", ".scr", ".jar", ".js", ".vbs", ".ps1", ".msi", ".com", ".py", ".pl", ".rb", ".workflow", ".terminal", ".dylib", ".so", ".apk", ".ipa"}


def stage(path, name=None, kind="mac", on_complete=None):
    """Copy a checked file into the outbox under a random name and register a one-time token. -> {"url","name","size","sha256","exp","sid"}.
    kind "phone": programs are refused outright (the phone gets documents, pictures, models, zips), and `on_complete(name, size)` runs ONLY when the whole file has been served."""
    real = check_source(path)
    if kind == "phone" and os.path.splitext(real)[1].lower() in EXEC_EXT:
        raise Refused("I don't send programs or scripts to the phone.")
    OUT.mkdir(parents=True, exist_ok=True)
    os.chmod(OUT, 0o700)
    tok = secrets.token_urlsafe(32)
    dst = OUT / secrets.token_hex(12)
    shutil.copyfile(real, dst)
    os.chmod(dst, 0o600)
    nm = re.sub(r"[^\w .()\[\]+-]", "_", name or os.path.basename(real))[:120] or "file"
    sid = hashlib.sha256(tok.encode()).hexdigest()[:10]                        # a handle for status lines: the token itself is never shown or logged
    s = {"path": str(dst), "name": nm, "size": os.path.getsize(dst), "sha": _sha(dst), "exp": time.time() + TTL_S, "used": False, "kind": kind, "sid": sid, "covered": [], "served": 0, "on_complete": on_complete, "t_first": 0.0}
    with _lock:
        SHARES[tok] = s
        PROOF[sid] = {"name": nm, "size": s["size"], "served": 0, "complete": False, "t": time.time(), "kind": kind}
    ip = ensure_server()
    return {"url": f"http://{ip}:{PORT}/o/{tok}", "name": nm, "size": s["size"], "sha256": s["sha"], "exp": s["exp"], "sid": sid}


PROOF = {}                                                                     # sid -> what the server SAW: bytes served and whether the whole file went out (the only proof a download is complete)


def proof(sid):
    return dict(PROOF.get(sid) or {})


def _merge(cov):
    cov.sort()
    out = []
    for a, b in cov:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def zip_paths(paths, name="jarvis-files.zip"):
    """Zip checked files into the outbox -> a path to stage (never unpacked by anything on receipt)."""
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / ("zip-" + secrets.token_hex(6) + ".zip")
    total = 0
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            real = check_source(p)
            total += os.path.getsize(real)
            if total > cap_bytes():
                tmp.unlink(missing_ok=True)
                raise Refused("Those files together are over the size limit.")
            z.write(real, os.path.basename(real))
    return str(tmp)


def _later(sec):
    t = threading.Timer(sec, _cleanup)
    t.daemon = True
    t.start()


def _cleanup():
    now = time.time()
    with _lock:
        for tok, s in list(SHARES.items()):
            if (s["used"] and now - s.get("t_used", 0) > s.get("hold", 120)) or s["exp"] < now:
                try:
                    os.unlink(s["path"])
                except OSError:
                    pass
                del SHARES[tok]
        left = bool(SHARES)
    if not left:
        stop_server()
    else:
        _later(30)                                                     # something is still waiting or downloading: look again (a second share never goes uncleaned)


async def _get(req):
    """One-time download. The link stays valid until the WHOLE file has gone out (a half download can be finished within the 10 minutes) and then never again. Content-Disposition: attachment, so Safari
    saves it to Downloads (Files > Downloads) instead of previewing it. Byte ranges are honoured (an interrupted download resumes). Completion = every byte served."""
    tok = req.match_info["token"]
    with _lock:
        s = SHARES.get(tok)
        ok = bool(s) and not s["used"] and s["exp"] > time.time()
        if ok and not s["t_first"]:
            s["t_first"] = time.time()
    if not ok:
        raise web.HTTPNotFound()
    size = s["size"]
    a, b = 0, size - 1
    status = 200
    rng = req.headers.get("Range", "")
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip()) if rng else None
    if m and (m.group(1) or m.group(2)):
        if m.group(1):
            a = int(m.group(1))
            b = min(size - 1, int(m.group(2))) if m.group(2) else size - 1
        else:
            a = max(0, size - int(m.group(2)))
        if a > b or a >= size:
            raise web.HTTPRequestRangeNotSatisfiable(headers={"Content-Range": f"bytes */{size}"})
        status = 206
    headers = {"Content-Type": "application/octet-stream", "Content-Disposition": 'attachment; filename="%s"' % s["name"].replace('"', "_"), "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
               "Accept-Ranges": "bytes", "Content-Length": str(b - a + 1)}
    if status == 206:
        headers["Content-Range"] = f"bytes {a}-{b}/{size}"
    resp = web.StreamResponse(status=status, headers=headers)
    await resp.prepare(req)
    pos = a
    try:
        with open(s["path"], "rb") as f:
            f.seek(a)
            while pos <= b:
                chunk = f.read(min(262144, b - pos + 1))
                if not chunk:
                    break
                await resp.write(chunk)
                with _lock:
                    s["covered"] = _merge(s["covered"] + [[pos, pos + len(chunk)]])
                    s["served"] = sum(y - x for x, y in s["covered"])
                    PROOF[s["sid"]].update(served=s["served"], t=time.time())
                pos += len(chunk)
        await resp.write_eof()
    except (ConnectionResetError, asyncio.CancelledError, OSError):
        return resp                                                             # the phone stopped: what was served is counted, the link stays open for the rest
    with _lock:
        done = s["served"] >= size and s["covered"] == [[0, size]] and not s["used"]
        if done:
            s["used"] = True
            s["t_used"], s["hold"] = time.time(), 120
            PROOF[s["sid"]].update(complete=True, t=time.time())
    if done:
        cb = s.get("on_complete")
        if cb:
            threading.Thread(target=cb, args=(s["name"], size), daemon=True).start()
        _later(130)
    return resp


def _run_loop(ip, ready):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _srv["loop"] = loop
    app = web.Application()
    app.router.add_get("/o/{token}", _get)
    runner = web.AppRunner(app, access_log=None)

    async def go():
        await runner.setup()
        await web.TCPSite(runner, ip, PORT).start()
        _srv["runner"] = runner
        ready.set()
    try:
        loop.run_until_complete(go())
    except OSError:
        ready.set()
        return
    loop.run_forever()


def ensure_server():
    """Listen on the LAN address (and only that address) while a share is waiting. -> the address."""
    with _lock:
        if _srv["runner"]:
            return _srv["ip"]
    ip = os.environ.get("JARVIS_SHARE_IP") or lan_ip()
    if not ip or not ipaddress.ip_address(ip).is_private:
        raise Refused("No home network address: connect to Wi-Fi first.")
    ready = threading.Event()
    _srv.update(ip=ip)
    t = threading.Thread(target=_run_loop, args=(ip, ready), daemon=True)
    _srv["thread"] = t
    t.start()
    ready.wait(5)
    if not _srv["runner"]:
        raise Refused("Port %d is busy." % PORT)
    _later(TTL_S + 5)
    return ip


def stop_server():
    r, loop = _srv.get("runner"), _srv.get("loop")
    if r and loop:
        asyncio.run_coroutine_threadsafe(r.cleanup(), loop).add_done_callback(lambda f: loop.call_soon_threadsafe(loop.stop))
    _srv.update(runner=None, loop=None, thread=None)
    try:
        for f in OUT.glob("*"):
            if f.is_file() and time.time() - f.stat().st_mtime > TTL_S + 60:
                f.unlink()
    except OSError:
        pass
