#!/usr/bin/env python3
"""Jarvis Mac helper: a silent background service (no window, no Dock icon, no menu bar item, no notifications of its own). It answers ONLY the paired Go on the local network: every request is
signed with the pairing key from ~/.jarvis-inbox/config.json (never printed). Standard library only.
  python3 jarvis_helper.py run        listen (the LaunchAgent runs this)
Jobs (POST /job?type=...): make-model-from-photo, process-scan, make-printable, save-to-downloads (also POST /save), health (GET /health).
States the Go shows in plain words: received, checking, working, optimizing, ready (or failed, with a reason). Results are cached by content hash (~/.jarvis-helper/cache).
Every request has a size cap and every job a timeout. Files are SAVED only: never opened, run or unzipped; executables go to Quarantine."""
import hashlib, hmac, json, os, re, socket, sys, threading, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meshcheck
import vault

HOME = os.path.expanduser("~")
CFG = os.environ.get("JARVIS_HELPER_CFG", os.path.join(HOME, ".jarvis-inbox", "config.json"))
DEST = os.environ.get("JARVIS_HELPER_DEST", os.path.join(HOME, "Downloads", "Jarvis"))
CACHE = os.environ.get("JARVIS_HELPER_CACHE", os.path.join(HOME, ".jarvis-helper", "cache"))
TOKEN_FILE = os.path.join(HOME, ".jarvis-helper", "hf_token")                 # typed once on the Mac, mode 600, outside git
PORT = int(os.environ.get("JARVIS_HELPER_PORT", "8777"))
MAX_BODY = 500 * 1024 * 1024
JOB_TIMEOUT = float(os.environ.get("JARVIS_HELPER_TIMEOUT", "180"))
SKEW = 60
BLOCKED = {".app", ".pkg", ".dmg", ".command", ".sh", ".exe", ".bat", ".cmd", ".scr", ".jar", ".js", ".vbs", ".ps1", ".msi", ".com", ".py", ".pl", ".rb", ".workflow", ".terminal", ".dylib", ".so", ".apk", ".ipa"}
MODEL_EXT = {".glb", ".usdz", ".obj", ".ply", ".stl"}
JOBS, SEEN, LOCK = {}, {}, threading.Lock()
STATUS = {"busy": 0}


def key():
    try:
        return bytes.fromhex(json.load(open(CFG))["key"])
    except (OSError, ValueError, KeyError):
        return None


def sign(k, method, path, ts, nonce, body):
    return hmac.new(k, f"{method}\n{path}\n{ts}\n{nonce}\n{hashlib.sha256(body).hexdigest()}".encode(), hashlib.sha256).hexdigest()


def verify(h, method, path, body, now=None):
    """-> None when the request is genuine, fresh and not replayed, else a plain reason."""
    k = key()
    if not k:
        return "not paired"
    try:
        ts, nonce, sig = int(h.get("X-Jarvis-Ts", "")), h.get("X-Jarvis-Nonce", ""), h.get("X-Jarvis-Sig", "")
    except ValueError:
        return "bad request"
    now = now or time.time()
    if abs(now - ts) > SKEW or not 8 <= len(nonce) <= 64:
        return "too old"
    if not hmac.compare_digest(sig, sign(k, method, path, ts, nonce, body)):
        return "bad signature"
    with LOCK:
        for n in [n for n, t in SEEN.items() if now - t > SKEW * 2]:
            del SEEN[n]
        if nonce in SEEN:
            return "replayed"
        SEEN[nonce] = now
    return None


def unique(folder, name):
    os.makedirs(folder, exist_ok=True)
    base, ext = os.path.splitext(name)
    p, n = os.path.join(folder, name), 1
    while os.path.exists(p):                                   # never overwrite: "name (2).ext"
        n += 1
        p = os.path.join(folder, f"{base} ({n}){ext}")
    return p


def save(name, data, sha=None):
    name = re.sub(r"[^\w .()\[\]+-]", "_", os.path.basename(name))[:120] or "file"
    if sha and hashlib.sha256(data).hexdigest() != sha:
        return {"ok": False, "why": "the file did not match its checksum"}
    quarantined = os.path.splitext(name)[1].lower() in BLOCKED
    path = unique(os.path.join(DEST, "Quarantine") if quarantined else DEST, name)
    with open(path, "wb") as f:
        f.write(data)
    ok = os.path.isfile(path) and os.path.getsize(path) == len(data) and hashlib.sha256(open(path, "rb").read()).hexdigest() == hashlib.sha256(data).hexdigest()
    return {"ok": ok, "name": os.path.basename(path), "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "quarantined": quarantined, "why": "" if ok else "the saved file does not match"}


def cached(kind, sha, params=""):
    return os.path.join(CACHE, hashlib.sha256(f"{kind}|{sha}|{params}".encode()).hexdigest()[:32] + ".json")


# ----------------------------------------------------------------------------- generators (photo to 3D). Real ones need gradio_client and, for the best two, a Hugging Face token on THIS Mac.
def generators():
    order = []
    have_token = os.path.exists(TOKEN_FILE)
    if have_token:
        order += ["TRELLIS.2", "TRELLIS"]
    order.append("Stable Fast 3D")
    return order


def run_generator(name, photo_path):
    """-> (glb bytes, which, seconds). Raises RuntimeError with a plain reason. JARVIS_HELPER_FAKE=1 returns a tiny test model."""
    if os.environ.get("JARVIS_HELPER_FAKE"):
        t = time.time()
        time.sleep(float(os.environ.get("JARVIS_HELPER_FAKE_DELAY", "0.2")))
        if os.environ.get("JARVIS_HELPER_FAKE_HANG"):
            time.sleep(60)
        return b"glTF" + b"\x02\x00\x00\x00" + b"\x00" * 16, name, round(time.time() - t, 1)
    try:
        import gradio_client                                      # noqa: F401  (optional, not installed by default)
    except ImportError:
        raise RuntimeError("the photo-to-3D generator is not installed on the Mac")
    raise RuntimeError("generator not wired in this build (see README: needs your yes before it downloads anything)")


def do_job(jid, kind, params, body):
    j = JOBS[jid]
    def st(s, text=""): j.update(state=s, text=text, t=time.time())
    try:
        STATUS["busy"] += 1
        sha = hashlib.sha256(body).hexdigest()
        cp = cached(kind, sha, json.dumps(params, sort_keys=True))
        if os.path.exists(cp):
            j.update(state="ready", text="from the cache", result=json.load(open(cp)), cache=True)
            return
        st("checking", "checking the file")
        res = {}
        if kind == "save-to-downloads":
            st("working", "saving to Downloads")
            res = save(params.get("name", "file"), body, params.get("sha256"))
            if not res["ok"]:
                raise RuntimeError(res["why"])
        elif kind == "process-scan":
            ext = os.path.splitext(params.get("name", ""))[1].lower()
            if ext not in MODEL_EXT:
                s = save(params.get("name", "file"), body)
                res = {"saved": s["name"], "supported": False, "verdict": f"{ext or 'that'} files are not supported for scans (GLB, USDZ, OBJ, PLY, STL are); it is saved in Downloads."}
            else:
                st("working", "checking the scan")
                c = meshcheck.check(body, ext)
                c["bbox"] = None
                s = save(params.get("name", "scan" + ext), body)
                st("optimizing", "making lighter versions")
                res = {"check": c, "verdict": meshcheck.scan_verdict(dict(c, bbox=None)), "saved": s["name"], "supported": True, "versions": "not built: needs the mesh tools (see README)"}
        elif kind == "make-printable":
            c = meshcheck.check(body, ".stl")
            s = save(params.get("name", "part.stl"), body)
            res = {"watertight": c["watertight"], "triangles": c["triangles"], "saved": s["name"]}
        elif kind == "make-model-from-photo":
            st("working", "making the model")
            tmp = os.path.join(CACHE, sha[:16] + ".img")
            os.makedirs(CACHE, exist_ok=True)
            open(tmp, "wb").write(body)
            last = ""
            for g in generators():
                try:
                    glb, which, secs = run_generator(g, tmp)
                    s = save(f"model-{sha[:8]}.glb", glb)
                    res = {"generator": which, "seconds": secs, "saved": s["name"], "approximate": True, "note": "APPROXIMATE: the hidden sides are guessed"}
                    break
                except RuntimeError as e:
                    last = str(e)
            else:
                raise RuntimeError(last or "no generator worked")
            os.unlink(tmp)
        else:
            raise RuntimeError("unknown job")
        os.makedirs(CACHE, exist_ok=True)
        json.dump(res, open(cp, "w"))
        j.update(state="ready", text="ready", result=res)
    except Exception as e:
        j.update(state="failed", text=str(e)[:160])
    finally:
        STATUS["busy"] = max(0, STATUS["busy"] - 1)


def start_job(kind, params, body):
    jid = hashlib.sha256(os.urandom(16)).hexdigest()[:12]
    JOBS[jid] = {"state": "received", "text": "received", "t": time.time(), "kind": kind}
    th = threading.Thread(target=do_job, args=(jid, kind, params, body), daemon=True)
    th.start()
    def watchdog():                                              # a job timeout: it fails with a plain reason, the service stays up
        th.join(JOB_TIMEOUT)
        if th.is_alive() and JOBS[jid]["state"] not in ("ready", "failed"):
            JOBS[jid].update(state="failed", text="timed out")
            STATUS["busy"] = max(0, STATUS["busy"] - 1)
    threading.Thread(target=watchdog, daemon=True).start()
    return jid


class H(BaseHTTPRequestHandler):
    server_version = "x"
    def log_message(self, *a): pass
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return None
        return self.rfile.read(n) if n else b""
    def handle_any(self, method):
        body = self._body()
        if body is None:
            return self._send(413, {"ok": False, "why": "too big (over 500 MB)"})
        why = verify(self.headers, method, self.path, body)
        if why:
            return self._send(403, {"ok": False, "why": why})
        u = urllib.parse.urlsplit(self.path); q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path == "/health":
            return self._send(200, {"ok": True, "busy": STATUS["busy"], "version": 1, "generators": generators()})
        if u.path == "/save" and method == "POST":
            r = save(q.get("name", "file"), body, q.get("sha256"))
            return self._send(200 if r["ok"] else 500, r)
        if u.path == "/vault" and method == "POST":                      # the memory vault (search, read, append, forget, undo, recent, graph): small JSON bodies only
            if len(body) > 200_000:
                return self._send(413, {"ok": False, "why": "too big"})
            try:
                data = json.loads(body or b"{}")
            except ValueError:
                return self._send(400, {"ok": False, "why": "bad request"})
            return self._send(200, vault.handle(q.get("op"), data))
        if u.path == "/job" and method == "POST":
            if q.get("type") not in ("make-model-from-photo", "process-scan", "make-printable", "save-to-downloads"):
                return self._send(400, {"ok": False, "why": "unknown job"})
            return self._send(202, {"ok": True, "id": start_job(q["type"], q, body)})
        m = re.fullmatch(r"/job/([0-9a-f]{12})", u.path)
        if m and method == "GET":
            j = JOBS.get(m.group(1))
            return self._send(200 if j else 404, dict(j or {}, ok=bool(j)))
        self._send(404, {"ok": False})
    def do_GET(self): self.handle_any("GET")
    def do_POST(self): self.handle_any("POST")


def announce():
    """Tell the paired Go where this helper is (a signed hello on the private reply topic, with this Mac's LAN address). Never prints the topic."""
    try:
        import jarvis_pair as jp
        cfg = json.load(open(CFG)); k = bytes.fromhex(cfg["key"])
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("10.255.255.255", 1)); ip = s.getsockname()[0]; s.close()
        urllib.request.urlopen(urllib.request.Request("https://ntfy.sh/" + cfg["ack_topic"], data=jp.sign(k, {"type": "hello", "helper": f"http://{ip}:{PORT}"}).encode(), method="POST", headers={"User-Agent": "jarvis-mac-helper"}), timeout=10).read()
    except Exception:
        pass


def run():
    threading.Thread(target=announce, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()


if __name__ == "__main__":
    run() if (sys.argv[1:] or ["run"])[0] == "run" else print(__doc__)
