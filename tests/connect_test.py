"""PROMPT28 items 3 and 4: camera chips (voice routes) and the live connect guide: stages, lost-port message, confirm step, mains refusal. Fake moving scene (numpy -> cjpeg). No camera needed.
Run: .venv/bin/python tests/connect_test.py"""
import os, subprocess, sys, json
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from jarvis import connect, look, track
R = []
def rec(n, ok): R.append(ok); print(("PASS " if ok else "FAIL ") + n, flush=True)
# ---- chips: every helper is reachable by voice, and ordinary questions are NOT hijacked
for q, m in [("help me connect these", "connect"), ("what is this part", "read"), ("read the label", "read"), ("count the screws", "count"), ("how many screws are on the desk", "count"),
             ("measure this with a coin", "measure"), ("check my wiring", "wiring")]:
    rec(f"voice '{q}' -> {m}", (look.mode_of(q) or {}).get("mode") == m)
import jarvis.look as _l, jarvis.spotify_cli as _sp, jarvis.projects as _pr, jarvis.notifier as _nt
for q in ("find the nearest pharmacy", "circle back tomorrow", "highlight the key points of this article", "what is that supposed to mean", "what's that song", "what do you see in the news", "how long is it", "count the sheep", "what does it say about rain"):
    rec(f"audit: '{q}' is an ordinary question, no camera", not _l.is_fresh(q) and _l.parse_command(q, {"open": False}) is None)
for q in ("start a timer for ten minutes", "start the car", "put on my coat", "start recording"):
    rec(f"audit: '{q}' is not music", _sp.parse(q) is None)
for q in ("open my email", "show my calendar", "i have a pulled muscle", "i got called in sick"):
    rec(f"audit: '{q}' is not a project / parts-bin phrase", _pr.parse_command(q) is None)
rec("audit: 'give me the morning briefing' does not set up a daily push", _nt.parse_command("give me the morning briefing") is None)
for q in ("how many people live in france", "how big is the moon", "count the days until friday", "what time is it"):
    rec(f"not hijacked: '{q}'", look.mode_of(q) is None)
rec("every mode has a prompt", all(m in look.MODE_PROMPTS for m in ("read", "count", "measure", "wiring")) and "CONNECT" in connect.PROMPT)
rec("faces are never labelled: a 'face' box becomes 'PERSON'", look.validate_boxes([{"name": "face", "box": [.1, .1, .2, .2]}])[0]["name"] == "PERSON")
# ---- connect: validate
ok = connect.validate({"a": {"name": "USB-C plug", "box": [.1, .6, .08, .05]}, "b": {"name": "phone port", "box": [.6, .5, .05, .04]}, "say": "Plug the USB-C end into the port on the bottom edge of the phone"})
rec("valid plug + port accepted", ok[0] == "ok")
mi = connect.validate({"missing": "port", "say": "I can't see the port, turn the phone so the bottom edge faces the camera"})
rec(f"port hidden: says so, no guess ('{mi[1][:40]}...')", mi[0] == "missing" and "can't see the port" in mi[1])
rec("a box that is not usable -> 'I can't see both ends', no line", connect.validate({"a": {"name": "x", "box": [2, 2, 1, 1]}, "b": {"name": "y", "box": [.1, .1, .1, .1]}})[0] == "missing")
mn = connect.validate({"a": {"name": "plug", "box": [.1, .1, .1, .1]}, "b": {"name": "wall outlet", "box": [.5, .5, .1, .1]}})
rec(f"mains: refused, user plugs into the wall themselves ('{mn[1][:50]}')", mn[0] == "mains" and "wall yourself" in mn[1])
# ---- connect: stages on fake positions, speech only on a step change, text rate limit, lost message, confirm
said, sent = [], []
g = connect.Guide(said.append, sent.append, electronics=True)
def box(x, y, lost=False): return {"name": "PLUG", "x": x, "y": y, "w": .05, "h": .05, "lost": lost}
def port(lost=False): return {"name": "PORT", "x": .6, "y": .5, "w": .05, "h": .05, "lost": lost}
t = 100.0; stages = []
for x in (.1, .15, .25, .35, .45, .52, .55, .57, .58, .58, .58, .58, .58):
    t += 0.5; m = g.feed([box(x, .5), port()], now=t); stages.append(m["stage"])
print("  stages:", stages)
rec("stages go far -> close -> aligned -> touch", stages[0] == "far" and "close" in stages and "aligned" in stages and stages[-1] == "touch")
rec("the line follows both ends (a and b positions travel with the message)", sent[-1]["a"]["x"] == .58 and sent[-1]["b"]["x"] == .6)
rec(f"voice only on step changes ({len(said)} lines for {len(stages)} frames)", 1 <= len(said) <= 5)
rec("'connected?' asks for confirmation and does not claim success", sent[-1]["confirm"] is True and sent[-1]["text"].startswith("connected?") or "connected?" in sent[-1]["text"])
rec("electronics: the line 'verify before powering' is added", any("verify before powering" in m["text"] for m in sent if m["stage"] in ("aligned", "touch")))
g2 = connect.Guide(said.append, sent.append); n0 = len(said)
g2.feed([box(.3, .5, lost=True), port()], now=1000.0)
rec(f"lost port/plug: says it can't see it and to turn it ('{said[-1][:50]}')", len(said) == n0 + 1 and "can't see" in said[-1])
g3 = connect.Guide(lambda s: None, sent.append); texts = []
for i in range(10):
    texts.append(g3.feed([box(.1 + i * .001, .5), port()], now=2000 + i * 0.05)["text"])
rec("text changes at most twice a second (no flicker)", len(set(texts)) <= 2)
g.confirm(); rec("confirm ends the guide", g.feed([box(.5, .5), port()]) is None)
# ---- connect: the tracker follows both ends of a real (fake) moving scene and the guide reacts
W, H = 640, 480; rng = np.random.default_rng(5)
bg = rng.integers(60, 140, (H // 8, W // 8)).astype(np.uint8).repeat(8, 0).repeat(8, 1)
A = np.kron(rng.integers(0, 255, (6, 6)), np.ones((8, 8))).astype(np.uint8); B = np.kron(rng.integers(0, 255, (6, 6)), np.ones((8, 8))).astype(np.uint8)
def frame(ax, hide_b=False):
    f = bg.copy(); f[300:300 + 48, 400:400 + 48] = B if not hide_b else bg[300:348, 400:448]; f[300:348, ax:ax + 48] = A
    return subprocess.run(["cjpeg", "-grayscale", "-quality", "85"], input=b"P5\n%d %d\n255\n" % (W, H) + f.tobytes(), capture_output=True).stdout
sent2 = []; gd = connect.Guide(lambda s: None, sent2.append)
tr = track.Tracker(lambda m: None); tr.on_out = gd.feed
tr.start([{"name": "PLUG", "x": 40 / W, "y": 300 / H, "w": 48 / W, "h": 48 / H}, {"name": "PORT", "x": 400 / W, "y": 300 / H, "w": 48 / W, "h": 48 / H}], frame(40))
for i, ax in enumerate(range(40, 360, 24)): tr.on_out(tr.step(frame(ax)))
seq = [m["stage"] for m in sent2]; print("  scene stages:", seq)
rec("tracked scene: line stages far -> close", seq[0] == "far" and "close" in seq)
tr.on_out(tr.step(frame(352, hide_b=True))); [tr.on_out(tr.step(frame(352, hide_b=True))) for _ in range(4)]
rec("port hidden in the scene: the guide reports it lost", any(m["b"]["lost"] for m in sent2))
print("RESULT", "ALL PASS" if all(R) else "FAILURES")
