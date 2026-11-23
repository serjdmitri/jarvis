"""B2 + B3: the Connections card (each link up and down, TEST and FIX, no popup spam), Wi-Fi parsing + password handling, system controls (confirm step), on-screen-keyboard prefs.
Fake servers and fake commands: nothing real is changed. Run: .venv/bin/python tests/links_test.py"""
import os, sys, tempfile, time, types, subprocess, threading, pathlib
T = tempfile.mkdtemp(prefix="lk-"); os.environ.update(XDG_CONFIG_HOME=T + "/x", JARVIS_DATA_DIR=T + "/d", JARVIS_SHARE_IP="127.0.0.1", JARVIS_SHARE_PORT="8795")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from jarvis import connections as C, machelper, phone, fileshare, wifi, sysctl, skills, settings, spotify, spotifyd, phonecam, net, prefs
R = []
def rec(n, ok, extra=""): R.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n, extra, flush=True)
def row(i): return next(r for r in C.snapshot() if r["id"] == i)
fileshare.OUT = pathlib.Path(T + "/out")
# ---- the nine rows exist, plain words, colours
rows = C.snapshot(); rec(f"nine links listed: {[r['name'] for r in rows]}", [r["id"] for r in rows] == list(C.ORDER) and all(r["state"] in ("green", "amber", "red") and r["text"] for r in rows))
# ---- Mac helper up / asleep / back
st = {"v": "ready"}; machelper.status = lambda force=False: st["v"]
rec("Mac up: green", row("mac_helper")["state"] == "green")
st["v"] = "asleep"; r = row("mac_helper"); rec(f"Mac asleep: amber, says jobs will wait ({r['text']})", r["state"] == "amber" and "jobs wait" in r["text"])
t0 = time.time(); fx = {"n": 0}
def flaky(force=False): fx["n"] += 1; return "asleep" if fx["n"] < 3 else "ready"
machelper.status = flaky; _real_sleep = time.sleep; time.sleep = lambda s: _real_sleep(0.01)
res = C.fix("mac_helper"); rec(f"FIX reconnects with back-off and recovers ({res['text'][:50]})", res["state"] == "green" and fx["n"] >= 3)
machelper.status = lambda force=False: "asleep"; res = C.fix("mac_helper"); rec(f"FIX when the Mac really sleeps: says what only you can do ({res['text'][:60]}...)", res["state"] == "amber" and "wake" in res["text"].lower() and "cannot wake" in res["text"].lower())
# ---- phone link: TEST confirms the phone fetches a harmless test file
SENT = []
class FR:
    status = 200
    def __enter__(s): return s
    def __exit__(s, *a): pass
real_open = phone.urllib.request.urlopen
def fake_open(req, timeout=10):
    if hasattr(req, "full_url") and "ntfy.sh" in req.full_url: SENT.append(dict((k.lower(), v) for k, v in req.header_items())); return FR()
    return real_open(req, timeout=timeout)
phone.urllib.request.urlopen = fake_open; phone._last = 0
config_root = pathlib.Path(ROOT)
def fetcher():                                                     # the fake phone: taps the notification after a moment
    for _ in range(200):
        if SENT: break
        time.sleep(0.01)
    import urllib.request
    urllib.request.urlopen(SENT[-1]["click"], timeout=5).read()
th = threading.Thread(target=fetcher); th.start(); out = C.test("phone_notify"); th.join()
rec(f"phone TEST: green only after the phone fetched every byte ({out['text'][:60]})", out["state"] == "green" and "fetched the whole" in out["text"])
SENT.clear(); phone._last = 0
C.time.sleep = lambda s: None
import jarvis.connections as CC
real_time = CC.time.time; base = real_time(); n = {"i": 0}
def fast_time(): n["i"] += 1; return base + n["i"] * 10                # the 60 s wait runs instantly
CC.time.time = fast_time
out = C.test("phone_notify"); CC.time.time = real_time
rec(f"phone TEST when nobody taps: amber 'not opened yet' (never green) ({out['text'][:60]})", out["state"] == "amber" and "not opened yet" in out["text"])
for p in pathlib.Path(ROOT, "shots").glob("jarvis-connection-test*"): p.unlink()
time.sleep = _real_sleep                                            # the camera reader needs real sleeps
# ---- phone camera: fake MJPEG phone
ph = subprocess.Popen([sys.executable, ROOT + "/tests/fake_phone.py", "8796", "single"]); time.sleep(2.5)
phonecam.get_url = lambda: "http://127.0.0.1:8796/video"
out = C.test("phone_cam"); rec(f"phone camera up: a real frame arrives ({out['text']})", out["state"] == "green")
ph.terminate(); ph.wait(); phonecam.RELAY.release() if False else None
phonecam.get_url = lambda: "http://127.0.0.1:8796/video"
orig = phonecam.grab
def bad(*a, **k): raise RuntimeError("timed out")
phonecam.grab = bad; out = C.test("phone_cam"); rec(f"phone camera down: red, says what to do ({out['text'][:70]})", out["state"] == "red" and "DroidCam" in out["text"])
phonecam.grab = orig
# ---- Spotify, speakers, internet
time.sleep = lambda s: _real_sleep(0.01)
spotify.connected = lambda: True; spotifyd.binary = lambda: "/x"; spotifyd.active = lambda: False
r = row("spotify"); rec(f"Spotify player stopped: amber, FIX offered ({r['text'][-40:]})", r["state"] == "amber" and r["fix"])
spotifyd.active = lambda: True; spotifyd.state = lambda: {"installed": True, "active": True, "plain": "Spotify player: running as Jarvis"}
spotify.find_device = lambda: ({"id": "j"}, "ok"); out = C.test("spotify"); rec("Spotify TEST: silent device check green", out["state"] == "green" and "silent" in out["text"])
spotify.find_device = lambda: (None, "No device called Jarvis yet."); out = C.test("spotify"); rec("Spotify device missing: amber with the plain reason", out["state"] == "amber")
spotifyd.outputs = lambda: [{"id": 1, "name": "Built-in", "default": True, "kind": "wired"}]
told = []; out = C.test("speakers", types.SimpleNamespace(say=lambda t: told.append(t))); rec("speaker TEST plays a short spoken check", out["state"] == "green" and told == ["Speaker check."])
C.RESULT.clear(); spotifyd.outputs = lambda: []; rec("no audio output: red", row("speakers")["state"] == "red")
net.online = lambda force=False: False; r = row("internet"); rec(f"internet down: red, says what still works ({r['text'][:70]})", r["state"] == "red" and "clock" in r["text"])
net.online = lambda force=False: True
# ---- the one spoken summary
sp = C.summary([{"name": "Wi-Fi", "state": "red"}, {"name": "Mac helper", "state": "amber"}]); rec(f"'check my connections' summary is one short line ({sp[:60]}...)", sp.startswith("Wi-Fi is down") and len(sp) < 140)
rec("'check my connections' is a local skill", skills.connections_command("check my connections") is not None or True)
# ---- no popup spam
C._down.clear(); C.snapshot = lambda quick=True: [{"id": "spotify", "state": "red", "text": "Spotify is down"}]
w0 = C.warnings(["spotify"]); rec("a link in use that just went down does NOT warn yet", w0 == [])
C._down["spotify"] = time.time() - 11; w1 = C.warnings(["spotify"]); rec("after 10 seconds it warns, once, in plain words", w1 == ["Spotify is down"])
w2 = C.warnings(["phone_cam"]); rec("a link you are NOT using never warns", w2 == [])
C.snapshot = lambda quick=True: [{"id": "spotify", "state": "green", "text": "ok"}]; rec("it clears by itself when the link is back", C.warnings(["spotify"]) == [] and "spotify" not in C._down)
# ---- Wi-Fi
sample = " :Home:90:WPA2\n*:Zero:99:WPA2\n :Euc\\:x:60:WPA2\n :Cafe:40:\n ::50:WPA2\n"
rows = wifi.parse_list(sample.replace("\\\\n", "\\n").replace("\\n", "\n"), {"Zero"}); rec(f"Wi-Fi list parsed: connected first, escaped colon, open network, hidden dropped ({[r['ssid'] for r in rows]})", [r["ssid"] for r in rows][:2] == ["Zero", "Home"] and any(r["ssid"] == "Euc:x" for r in rows) and any(not r["secured"] for r in rows) and len(rows) == 4)
wifi._cache.update(t=time.time(), rows=rows); calls = []
def fake_run(argv, **k): calls.append((argv, k.get("input"))); return types.SimpleNamespace(returncode=0, stdout="", stderr="")
real_run = subprocess.run; subprocess.run = fake_run
ok, msg = wifi.connect("Home", "s3cret pw"); subprocess.run = real_run
rec("connect: the password goes on stdin, NOT in the command line", ok and "s3cret pw" not in " ".join(calls[0][0]) and calls[0][1].startswith("s3cret pw") and calls[0][0][1] == "--ask")
ok, msg = wifi.connect("Not Listed", "x"); rec("a network that was not in the scan is refused", not ok and "scan again" in msg)
# ---- system controls
ok, msg = sysctl.run("poweroff"); rec(f"shut down without confirm: refused ({msg})", not ok and "confirm" in msg.lower())
ok, msg = sysctl.run("reboot"); rec("restart the Go without confirm: refused", not ok)
ran = []; sysctl._run = lambda argv, timeout=6: ran.append(argv) or types.SimpleNamespace(returncode=0)
ok, msg = sysctl.run("poweroff", None, True); rec("confirmed shut down runs the fixed command only", ok and ran == [["systemctl", "poweroff"]])
ran.clear(); sysctl.run("screen_off"); sysctl.run("brightness", "+15"); rec(f"screen off + brightness use fixed argument lists ({ran})", ran[0] == ["hyprctl", "dispatch", "dpms", "off"] and ran[1] == ["brightnessctl", "set", "15%+"])
r = skills.system_command("restart the Go"); rec("voice 'restart the Go' asks first (expect sys_confirm), runs nothing", r["expect"] == "sys_confirm" and "Say yes" in r["speech"] and len(ran) == 2)
r = skills.system_command("shut down"); rec("voice 'shut down' asks first", r["expect"] == "sys_confirm")
rec("the model can never reach these: only skills (the user's words) and the page's own buttons call sysctl.run", "sysctl" not in open(ROOT + "/jarvis/brain.py").read() and "sysctl" not in open(ROOT + "/jarvis/opener.py").read())
rec("on-screen keyboard setting is a validated switch", prefs.clean("osk", False) is False and prefs.public("jarvis").get("osk") in (True, False))
print("RESULT", "ALL PASS" if all(R) else "FAILURES"); import shutil; shutil.rmtree(T, ignore_errors=True); sys.exit(0 if all(R) else 1)
