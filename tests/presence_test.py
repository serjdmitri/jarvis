"""Desk presence with synthetic frames: still picture = no motion, changing picture = motion, away after the timeout, back when it moves; reminders wait and fire once; CPU while running.
Run: .venv/bin/python tests/presence_test.py   (needs /tmp/p14/pres/a.jpg and b.jpg: two different 640x480 JPEGs)"""
import os, sys, time, threading, tempfile, pathlib, resource
os.environ["JARVIS_DATA_DIR"] = tempfile.mkdtemp(prefix="prestest-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from jarvis import presence, phonecam
A, B = open("/tmp/p14/pres/a.jpg", "rb").read(), open("/tmp/p14/pres/b.jpg", "rb").read()
R = []
def rec(n, ok, extra=""): R.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n, extra)
phonecam.get_url = lambda: "http://127.0.0.1:1/video"
phonecam.RELAY.want = lambda s=8: None; phonecam.RELAY.hold = lambda: None; phonecam.RELAY.release = lambda: None
presence.INTERVAL = 0.2; presence.AWAY_AFTER_S = 3
events = []
cur = {"frame": A}
def feeder():
    n = 0
    while True:
        n += 1; phonecam.RELAY.latest, phonecam.RELAY.n = cur["frame"], n; time.sleep(0.1)
threading.Thread(target=feeder, daemon=True).start()
rec("score of identical grey = 0", presence._score(b"\x10" * 768, b"\x10" * 768) == 0)
rec("score of different grey is large", presence._score(b"\x00" * 768, b"\xff" * 768) > 200)
presence.start(lambda p, was: events.append((p, was)), lambda: False)
time.sleep(2.0); rec("still picture: no motion, not 'present' yet", presence.STATE["last_motion"] == 0 and presence.STATE["present"] is None, str(presence.STATE["score"]))
c0 = time.process_time(); t0 = time.time()
cur["frame"] = B; time.sleep(1.0); cur["frame"] = A; time.sleep(1.0)
rec("a change = motion -> present", presence.STATE["present"] is True and events and events[0] == (True, None), str(events))
time.sleep(5.0)
rec("no motion for the timeout -> away", presence.STATE["present"] is False, str(events))
cur["frame"] = B; time.sleep(1.0)
rec("motion again -> back (False -> True)", events[-1] == (True, False), str(events))
presence.pause(True); n = presence.STATE["frames"]; cur["frame"] = A; time.sleep(1.5)
rec("paused: no frames processed", presence.STATE["frames"] == n)
presence.pause(False)
cpu = (time.process_time() - c0) / (time.time() - t0) * 100
presence.stop()
rec("presence thread CPU (python side, 5 pictures/s here) low", cpu < 8, f"{cpu:.1f}% of one core")
presence.add_desk_reminder("call mom"); presence.add_desk_reminder("stretch")
got = presence.take_desk_reminders(); rec("waiting reminders returned", [g["label"] for g in got] == ["call mom", "stretch"])
rec("each fires exactly once", presence.take_desk_reminders() == [])
rec("no picture is kept on disk", not [p for p in pathlib.Path(os.environ["JARVIS_DATA_DIR"]).rglob("*") if p.suffix in (".jpg", ".png", ".raw")])
print(f"{sum(R)}/{len(R)} passed")
