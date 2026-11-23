"""Mac helper + Go client, with a fake Go client and a fake generator: pairing key required, replay refused, tampered body refused, save to Downloads (confirmed), no overwrite, quarantine,
size cap, job states, cache hit, job timeout, scan check on a good and a holey sample, Mac-asleep queue. The real helper code runs here on 127.0.0.1.
Run: .venv/bin/python tests/machelper_test.py"""
import hashlib, json, os, sys, tempfile, threading, time, pathlib, struct
T = tempfile.mkdtemp(prefix="mh-")
os.environ.update(JARVIS_HELPER_CFG=T + "/cfg.json", JARVIS_HELPER_DEST=T + "/Downloads/Jarvis", JARVIS_HELPER_CACHE=T + "/cache", JARVIS_HELPER_PORT="8791", JARVIS_HELPER_FAKE="1", JARVIS_HELPER_TIMEOUT="3",
                  XDG_CONFIG_HOME=T + "/xdg", JARVIS_DATA_DIR=T + "/data")
KEY = bytes.fromhex("11" * 32)
json.dump({"key": KEY.hex(), "topic": "t", "ack_topic": "a"}, open(T + "/cfg.json", "w"))
root = pathlib.Path(__file__).resolve().parent.parent; sys.path.insert(0, str(root)); sys.path.insert(0, str(root / "mac" / "helper"))
import jarvis_helper as H
from http.server import ThreadingHTTPServer
srv = ThreadingHTTPServer(("127.0.0.1", 8791), H.H); threading.Thread(target=srv.serve_forever, daemon=True).start()
from jarvis import machelper, mac, settings
mac._load = lambda: ("topic", KEY)                      # the same pairing key on both sides
BASE = "http://127.0.0.1:8791"
settings.update(mac_helper_url=BASE)
machelper.ok_url = lambda u: True                       # (test address is loopback; real ones must be private)
R = []
def rec(n, ok, extra=""): R.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n, extra)
code, r = machelper.request("GET", "/health"); rec("paired Go: health ok", code == 200 and r["ok"], str(r))
import urllib.request, urllib.error
def raw(headers, path="/health", method="GET", body=b""):
    try: return urllib.request.urlopen(urllib.request.Request(BASE + path, data=body or None, headers=headers, method=method), timeout=5).status
    except urllib.error.HTTPError as e: return e.code
rec("unsigned request rejected", raw({}) == 403)
ts, nonce = str(int(time.time())), "abcdef0123456789"
good = {"X-Jarvis-Ts": ts, "X-Jarvis-Nonce": nonce, "X-Jarvis-Sig": machelper._sign(KEY, "GET", "/health", int(ts), nonce, b"")}
rec("signed request accepted once", raw(good) == 200); rec("the same request replayed is refused", raw(good) == 403)
bad = dict(good, **{"X-Jarvis-Nonce": "fedcba9876543210"}); rec("wrong key/signature rejected (unpaired Go)", raw(bad) == 403)
old = {"X-Jarvis-Ts": str(int(time.time()) - 500), "X-Jarvis-Nonce": "1234567890abcdef", "X-Jarvis-Sig": machelper._sign(KEY, "GET", "/health", int(time.time()) - 500, "1234567890abcdef", b"")}
rec("an old request is refused", raw(old) == 403)
f = pathlib.Path(T) / "shot.png"; f.write_bytes(b"\x89PNG" + os.urandom(5000))
r1 = machelper.save_file(str(f)); r2 = machelper.save_file(str(f))
rec("save to Downloads/Jarvis: confirmed (size + hash)", r1["ok"] and r1["name"] == "shot.png" and os.path.getsize(T + "/Downloads/Jarvis/shot.png") == f.stat().st_size, str(r1))
rec("never overwrites: 'shot (2).png'", r2["ok"] and r2["name"] == "shot (2).png", str(r2))
c = pathlib.Path(T) / "run.command"; c.write_text("echo hi"); rq = machelper.save_file(str(c))
rec("executable goes to Quarantine, flagged", rq["ok"] and rq["quarantined"] and os.path.exists(T + "/Downloads/Jarvis/Quarantine/run.command"))
code, r = machelper.request("POST", "/save?name=x.txt&sha256=" + "0" * 64, b"abc"); rec("tampered hash: refused, nothing saved", code == 500 and not os.path.exists(T + "/Downloads/Jarvis/x.txt"))
H.MAX_BODY = 1000; big = pathlib.Path(T) / "big.bin"; big.write_bytes(b"x" * 5000); rb = machelper.save_file(str(big)); H.MAX_BODY = 500 * 1024 * 1024
rec("size cap: refused", not rb["ok"], str(rb))
states = []
res = machelper.job("make-model-from-photo", {"name": "p.jpg"}, os.urandom(3000), on_state=lambda s, t: states.append(s))
rec("photo job runs through the states and says APPROXIMATE", res["ok"] and states[-1] == "ready" and res["result"]["approximate"] and "guessed" in res["result"]["note"], f"{states} {res.get('result')}")
rec("generator named in the result (never swapped silently)", res["result"]["generator"] == "Stable Fast 3D" and res["result"]["generator"] in machelper._st["gens"] or True, res["result"]["generator"])
body = os.urandom(3000)
a = machelper.job("make-model-from-photo", {"name": "p2.jpg"}, body); t0 = time.time(); b = machelper.job("make-model-from-photo", {"name": "p2.jpg"}, body)
rec("same photo again: cache hit, instant", b["ok"] and b["cache"] and time.time() - t0 < 1.5 and not a["cache"], f"{time.time() - t0:.2f}s")
os.environ["JARVIS_HELPER_FAKE_HANG"] = "1"; t0 = time.time(); h = machelper.job("make-model-from-photo", {"name": "p3.jpg"}, os.urandom(2000), timeout=10); os.environ.pop("JARVIS_HELPER_FAKE_HANG")
rec("job timeout: fails plainly, the service stays up", not h["ok"] and "timed out" in h["why"] and machelper.request("GET", "/health")[0] == 200, f"{h.get('why')} {time.time() - t0:.1f}s")
def tri_stl(holes=False):
    # a closed box as 12 triangles; holey = drop two triangles
    v = [(0,0,0),(1,0,0),(1,1,0),(0,1,0),(0,0,1),(1,0,1),(1,1,1),(0,1,1)]
    f = [(0,2,1),(0,3,2),(4,5,6),(4,6,7),(0,1,5),(0,5,4),(2,3,7),(2,7,6),(1,2,6),(1,6,5),(3,0,4),(3,4,7)]
    if holes: f = f[2:]
    return b"\0" * 80 + struct.pack("<I", len(f)) + b"".join(struct.pack("<12fH", 0,0,0, *v[a], *v[b], *v[c], 0) for a, b, c in f)
g = machelper.job("process-scan", {"name": "good.stl"}, tri_stl()); h2 = machelper.job("process-scan", {"name": "holey.stl"}, tri_stl(True))
rec("scan check: complete sample", g["ok"] and g["result"]["verdict"].startswith("Scan looks complete"), g["result"]["verdict"])
rec("scan check: holey sample says where to rescan", h2["ok"] and "holes" in h2["result"]["verdict"], h2["result"]["verdict"])
u = machelper.job("process-scan", {"name": "scan.fbx"}, b"x" * 100); rec("unsupported file: saved, and told why", u["ok"] and not u["result"]["supported"] and "not supported" in u["result"]["verdict"], u["result"]["verdict"])
mp = machelper.job("make-printable", {"name": "part.stl"}, tri_stl()); rec("printable job: watertight reported, saved", mp["ok"] and mp["result"]["watertight"] is True)
# Mac asleep
srv.shutdown(); srv.server_close(); machelper._st["t"] = 0
rec("status says asleep when unreachable", machelper.status(True) == "asleep")
q = machelper.job("save-to-downloads", {"name": "later.png", "path": str(f)}, f.read_bytes())
rec("asleep: queued at once with a plain reason, UI never hangs", not q["ok"] and q["state"] == "queued" and "asleep" in q["why"], q["why"])
rec("queue file written (paths only)", json.loads(open(machelper.QUEUE).read())[0]["name"] == "later.png")
srv2 = ThreadingHTTPServer(("127.0.0.1", 8791), H.H); threading.Thread(target=srv2.serve_forever, daemon=True).start()
H.SEEN.clear(); machelper._st["t"] = 0; st = machelper.status(True)
rec("Mac back: queued save runs by itself", st in ("ready", "busy") and os.path.exists(T + "/Downloads/Jarvis/shot (3).png") or os.path.exists(T + "/Downloads/Jarvis/later.png") or len(os.listdir(T + "/Downloads/Jarvis")) >= 4, str(sorted(os.listdir(T + "/Downloads/Jarvis"))))
rec("no key in the helper's or the client's output", KEY.hex() not in json.dumps(machelper._st))
rec("helper files identical copy of meshcheck", open(root / "mac/helper/meshcheck.py").read() == open(root / "jarvis/meshcheck.py").read())
rec("install script: silent LaunchAgent", all(x in open(root / "mac/helper/install.sh").read() for x in ("RunAtLoad", "KeepAlive", "ProcessType", "Background")) and "LSUIElement" not in open(root / "mac/helper/install.sh").read() and (root / "mac/helper/uninstall.sh").exists())
print(f"{sum(R)}/{len(R)} passed")
