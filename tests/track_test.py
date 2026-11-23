"""PROMPT28 item 2: highlights follow a moving object, dim when it is hidden, come back when it reappears. Fake moving-object video (numpy -> cjpeg), no camera needed.
Run: .venv/bin/python tests/track_test.py"""
import os, subprocess, sys, time
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from jarvis import track, perf
W, H = 640, 480
rng = np.random.default_rng(3)
bg = rng.integers(60, 140, (H // 8, W // 8)).astype(np.uint8).repeat(8, 0).repeat(8, 1)            # blocky texture so matching has something to hold
obj = rng.integers(0, 255, (60, 5)).astype(np.uint8)
OB = np.kron(rng.integers(0, 255, (10, 10)), np.ones((8, 8))).astype(np.uint8)                      # 80x80 distinctive patch
def frame(pos):
    f = bg.copy()
    if pos: x, y = pos; f[y:y + 80, x:x + 80] = OB
    p = subprocess.run(["cjpeg", "-grayscale", "-quality", "85"], input=b"P5\n%d %d\n255\n" % (W, H) + f.tobytes(), capture_output=True); return p.stdout
def path_slow(i): return (100 + i * 3, 150 + i * 2)
def path_fast(i): return (100 + i * 22, 200 + (i % 5) * 4)
R = []
def rec(n, ok): R.append(ok); print(("PASS " if ok else "FAIL ") + n, flush=True)
def run(pathf, n, label, hide=range(0)):
    tr = track.Tracker(lambda m: None); first = frame(pathf(0)); tr.start([{"name": "CUP", "x": pathf(0)[0] / W, "y": pathf(0)[1] / H, "w": 80 / W, "h": 80 / H}], first, follow=False)
    errs, lost_seen, back = [], False, False
    for i in range(1, n):
        pos = None if i in hide else pathf(i)
        out = tr.step(frame(pos))[0]
        if pos is None:
            lost_seen |= out["lost"]
        else:
            err = abs(out["x"] * W - pos[0]) + abs(out["y"] * H - pos[1]); errs.append(err)
            if lost_seen and not out["lost"]: back = True
    return tr, errs, lost_seen, back
tr, errs, _, _ = run(path_slow, 40, "slow")
rec(f"slow move: box stays on the object (worst error {max(errs):.0f} px of 640, avg compute {tr.stats['ms']:.1f} ms/frame)", max(errs) <= 12)
tr, errs, _, _ = run(path_fast, 20, "fast")
rec(f"fast move (22 px per frame): box keeps up (worst error {max(errs):.0f} px)", max(errs) <= 16)
def hid_path(i): return (200 + (i % 3) * 2, 180) if i < 12 else (420, 300)
tr, errs, lost_seen, back = run(hid_path, 40, "hide", hide=range(12, 22))
rec("hidden: the box dims and says lost", lost_seen)
rec("reappears somewhere else: the box comes back on it", back and errs and errs[-1] <= 12)
t = perf.cpu_temp_c(); fps_budget = tr.stats["ms"] * track.FPS / 1000
rec(f"budget: {tr.stats['ms']:.1f} ms avg ({tr.stats['max_ms']:.0f} ms worst) per frame at {track.FPS} fps = {fps_budget * 100:.0f}% of one core (match only); CPU temperature now {t}", tr.stats["ms"] < 60 and (t is None or t < track.HOT_C))
print("RESULT", "ALL PASS" if all(R) else "FAILURES")
