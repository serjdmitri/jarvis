"""PROMPT28 item 1: "what do you see" never freezes the live camera. Fake MJPEG phone (tests/fake_phone.py single) + the real shared reader + the real Assistant._look_frame.
Run: python tests/live_snapshot.py"""
import os, subprocess, sys, time, types, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
PORT = 8794
ph = subprocess.Popen([sys.executable, ROOT + "/tests/fake_phone.py", str(PORT), "single"]); time.sleep(2.5)
from jarvis import phonecam, opener, core
phonecam.get_url = lambda: f"http://127.0.0.1:{PORT}/video"
events = []; opener.NOTIFY = lambda m: events.append(m)
R = []
def rec(n, ok): R.append(ok); print(("PASS " if ok else "FAIL ") + n, flush=True)
try:
    st = types.SimpleNamespace(look={"open": False, "path": None, "boxes": [], "sel": None, "source": "", "live": False}, last_source="", on_msg=lambda *a: None, _look_text="")
    def grab(explicit_screen=False, camera_only=False):
        st.last_source = "phone camera"; return phonecam.grab(timeout=8)
    st.grab = grab
    phonecam.RELAY.hold()                                   # the page's live view holds the one reader
    t0 = time.time()
    while phonecam.RELAY.n < 8 and time.time() - t0 < 10: time.sleep(0.1)
    rows = []; ok_live = ok_rising = ok_moment = True
    for i in range(5):
        before = phonecam.RELAY.n; events.clear()
        path = core.Assistant._look_frame(st, retake=True)
        during = phonecam.RELAY.n
        time.sleep(0.6); after = phonecam.RELAY.n
        ops = [(e.get("op"), e.get("live")) for e in events if e.get("t") == "look"]
        rows.append((i + 1, before, during, after, ops))
        ok_live &= st.look["live"] is True and not any(o[0] in ("frame", "freeze") for o in ops)
        ok_rising &= after > during >= before
        ok_moment &= os.path.exists(path)
    for r in rows: print(f"  ask {r[0]}: frames before={r[1]} during={r[2]} after={r[3]} ops={[o[0] for o in r[4]]}")
    rec("five asks in a row: live flag stays on, no still frame or freeze sent", ok_live)
    rec("live frame counter keeps rising through every ask", ok_rising)
    rec("snapshot file written for each ask", ok_moment)
    # snapshot is the frame from the moment asked: the number returned is <= the counter right after the call, and >= the counter just before
    b = phonecam.RELAY.n; data, n, t = phonecam.RELAY.snapshot(); a = phonecam.RELAY.n
    rec(f"snapshot is the newest frame at that moment (before {b}, snapshot {n}, after {a})", b <= n <= a)
    time.sleep(0.5); rec("snapshot copy is unchanged while the live reader moves on", phonecam.RELAY.n > n and len(data) > 1000)
    stats = __import__("json").load(__import__("urllib.request").request.urlopen(f"http://127.0.0.1:{PORT}/stats", timeout=3))
    rec(f"the phone only ever saw {stats['max']} viewer, refused {stats['refused']}", stats["max"] == 1 and stats["refused"] == 0)
finally:
    phonecam.RELAY.release(); ph.kill()
print("RESULT", "ALL PASS" if all(R) else "FAILURES"); os._exit(0 if all(R) else 1)
