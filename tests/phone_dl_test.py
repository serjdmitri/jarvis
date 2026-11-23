"""B1: send to phone = a tap-to-download link on the Go (home Wi-Fi), served with Content-Disposition: attachment; "downloaded" is claimed ONLY when every byte was served.
Fake phone client on 127.0.0.1; ntfy is faked (no real notification is sent). Run: .venv/bin/python tests/phone_dl_test.py"""
import hashlib, io, os, sys, tempfile, threading, time, urllib.request, urllib.error, zipfile, socket
T = tempfile.mkdtemp(prefix="pd-"); os.environ.update(XDG_CONFIG_HOME=T + "/x", JARVIS_DATA_DIR=T + "/d", JARVIS_SHARE_IP="127.0.0.1", JARVIS_SHARE_PORT="8793")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from jarvis import phone, fileshare, config
fileshare.OUT = __import__("pathlib").Path(T + "/outbox")
R = []
def rec(n, ok, extra=""): R.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n, extra, flush=True)
SENT = []
class FakeResp:
    status = 200
    def __enter__(s): return s
    def __exit__(s, *a): pass
    def read(s, *a): return b""
_real = urllib.request.urlopen
def fake_open(req, timeout=10):
    if hasattr(req, "full_url") and "ntfy.sh" in req.full_url:
        SENT.append((req.data.decode() if req.data else "", dict((k.lower(), v) for k, v in req.header_items()), req.full_url)); return FakeResp()
    return _real(req, timeout=timeout)
phone.urllib.request.urlopen = fake_open; phone._last = 0
home = os.path.expanduser("~")                                                     # files must live under the home folder to be shareable
D = os.path.join(home, ".jarvis-pd-test"); os.makedirs(D, exist_ok=True)
def mk(name, data):
    p = os.path.join(D, name); open(p, "wb").write(data); return p
def send(path, cb=None):
    phone._last = 0; SENT.clear(); r = phone.send_download(path, on_complete=cb); return r, (SENT[-1] if SENT else None)
def get(url, headers=None, limit=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read(limit) if limit else r.read()
            return r.status, dict(r.headers), body
    except urllib.error.HTTPError as e: return e.code, dict(e.headers), b""
try:
    done = []
    jpg = mk("photo.jpg", b"\xff\xd8\xff" + os.urandom(40000)); pdf = mk("doc.pdf", b"%PDF-1.4\n" + os.urandom(30000)); note = mk("note.txt", b"buy resistors\n" * 50)
    r, (msg, hdr, url) = send(jpg, lambda n, s: done.append((n, s)))
    rec(f"photo: notification text is name, size and 'tap to save to Downloads' ({msg})", r["ok"] and "photo.jpg" in msg and "tap to save to Downloads" in msg)
    rec("the notification carries a Click link (the download), the message and title carry no link and no topic", hdr.get("click", "").startswith("http://127.0.0.1:8793/o/") and "http" not in msg and phone.get_topic() not in msg + hdr.get("title", ""))
    link = hdr["click"]; sid = r["sid"]
    rec("before any fetch the proof says nothing was downloaded", fileshare.proof(sid)["served"] == 0 and not fileshare.proof(sid)["complete"] and not done)
    st, h, body = get(link)
    rec(f"fake phone fetches the photo: attachment header, octet-stream (so Safari saves instead of previewing)", st == 200 and "attachment" in h.get("Content-Disposition", "") and h.get("Content-Type") == "application/octet-stream" and hashlib.sha256(body).hexdigest() == hashlib.sha256(open(jpg, "rb").read()).hexdigest())
    time.sleep(0.3); pr = fileshare.proof(sid)
    rec(f"proof: bytes served {pr['served']} = file size {r['size']} -> complete; the 'downloaded' callback ran once", pr["complete"] and pr["served"] == r["size"] and done == [("photo.jpg", r["size"])])
    st, _, _ = get(link); rec("replaying the used link is refused (404)", st == 404)
    r, (msg, hdr, url) = send(pdf); st, h, body = get(hdr["click"]); rec("PDF downloads (attachment), not previewed", st == 200 and "attachment" in h.get("Content-Disposition", "") and body.startswith(b"%PDF"))
    r, (msg, hdr, url) = send(note); st, h, body = get(hdr["click"]); rec("text note downloads", st == 200 and body.startswith(b"buy resistors"))
    zb = io.BytesIO(); z = zipfile.ZipFile(zb, "w"); z.writestr("a.bin", os.urandom(5 * 1024 * 1024)); z.close(); big = mk("pack.zip", zb.getvalue())
    r, (msg, hdr, url) = send(big); st, h, body = get(hdr["click"]); rec(f"a {len(body)/1e6:.1f} MB zip downloads whole and is never opened", st == 200 and len(body) == r["size"] and fileshare.proof(r["sid"])["complete"] and "MB" in msg)
    cmd = mk("run.command", b"echo hi"); r, sent = send(cmd); rec(f"a .command file is blocked ({r['message']})", not r["ok"] and "programs" in r["message"] and sent is None)
    # half-finished download
    done2 = []; r, (msg, hdr, url) = send(mk("half.bin", os.urandom(3 * 1024 * 1024)), lambda n, s: done2.append(n)); link = hdr["click"]
    s = socket.create_connection(("127.0.0.1", 8793)); s.sendall(f"GET {link[len('http://127.0.0.1:8793'):]} HTTP/1.1\r\nHost: x\r\n\r\n".encode()); s.recv(200000); s.close(); time.sleep(0.5)
    pr = fileshare.proof(r["sid"])
    rec(f"a half-finished download does NOT count as done (served {pr['served']} of {r['size']})", not pr["complete"] and 0 < pr["served"] < r["size"] and not done2)
    st, h, body = get(link, {"Range": f"bytes={pr['served']}-"}); time.sleep(0.4); pr = fileshare.proof(r["sid"])
    rec("finishing it with a resumed (Range) request completes it and only then it counts", st == 206 and pr["complete"] and done2 == ["half.bin"])
    # expiry
    r, (msg, hdr, url) = send(mk("old.txt", b"x" * 100)); tok = hdr["click"].rsplit("/", 1)[1]; fileshare.SHARES[tok]["exp"] = time.time() - 1
    st, _, _ = get(hdr["click"]); rec("an expired link is refused (404)", st == 404)
    # nothing secret in the share
    rec("secret-looking files are never shared", phone.send_download(os.path.expanduser("~/.ssh/id_rsa"))["ok"] is False)
finally:
    import shutil; shutil.rmtree(D, ignore_errors=True); fileshare.stop_server()
print("RESULT", "ALL PASS" if all(R) else "FAILURES"); sys.exit(0 if all(R) else 1)
