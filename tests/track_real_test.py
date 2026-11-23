"""A2: the tracker on a HARD, realistic scene: textured photo background, a LOOSE box (the vision step draws it bigger than the object), sensor noise, exposure drift, motion blur and a slightly turning object.
Run: .venv/bin/python tests/track_real_test.py"""
import os, subprocess, sys, glob
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from jarvis import track
W, H = 640, 480
rng = np.random.default_rng(7)
def load_bg():
    src = (glob.glob(ROOT + "/screenshots/*.jpg") + glob.glob(ROOT + "/screenshots/*.png"))[0]
    p = subprocess.run(["convert", src + "[0]", "-resize", f"{W}x{H}!", "-colorspace", "Gray", "-depth", "8", "gray:-"], capture_output=True)
    if len(p.stdout) == W * H: return np.frombuffer(p.stdout, np.uint8).reshape(H, W).astype(np.float32)
    return rng.integers(60, 160, (H // 6, W // 6)).astype(np.float32).repeat(6, 0).repeat(6, 1)
BG = load_bg() * 0.5 + 60                                                                                   # a calm, low-contrast desk
OB = np.kron(rng.integers(40, 230, (8, 8)), np.ones((7, 7))).astype(np.float32)[:52, :52]                   # a 52x52 object with its own texture
def box_blur(a, k):
    if k <= 1: return a
    c = np.cumsum(np.pad(a, ((0, 0), (k, 0)), mode="edge"), 1); return (c[:, k:] - c[:, :-k]) / k
def frame(pos, i, speed=0):
    f = BG.copy()
    if pos:
        x, y = pos; f[y:y + 52, x:x + 52] = OB
        if speed > 8: f[y - 4:y + 56, max(0, x - 6):x + 58] = box_blur(f[y - 4:y + 56, max(0, x - 6):x + 58], 3)    # motion blur on fast moves
    f = f * (1 + 0.08 * np.sin(i / 5.0)) + rng.normal(0, 4, f.shape)                                        # exposure drift + sensor noise
    p = subprocess.run(["cjpeg", "-grayscale", "-quality", "70"], input=b"P5\n%d %d\n255\n" % (W, H) + np.clip(f, 0, 255).astype(np.uint8).tobytes(), capture_output=True); return p.stdout
def loose(pos, pad=14):                                                                                    # the brain's box: bigger than the object, off-centre
    return {"name": "CUP", "x": (pos[0] - pad) / W, "y": (pos[1] - pad + 4) / H, "w": (52 + 2 * pad + 6) / W, "h": (52 + 2 * pad) / H}
R = []
def rec(n, ok): R.append(ok); print(("PASS " if ok else "FAIL ") + n, flush=True)
def run(pathf, n, speed, hide=range(0)):
    tr = track.Tracker(lambda m: None); tr.start([loose(pathf(0))], frame(pathf(0), 0), follow=False); errs, lost_n, back, seen_lost = [], 0, False, False
    for i in range(1, n):
        pos = None if i in hide else pathf(i)
        out = tr.step(frame(pos, i, speed))[0]
        if pos is None: seen_lost |= out["lost"]; continue
        cx = (out["x"] + out["w"] / 2) * W; cy = (out["y"] + out["h"] / 2) * H
        errs.append(abs(cx - (pos[0] + 26 + 3)) + abs(cy - (pos[1] + 26 + 4 - 0))); lost_n += out["lost"]
        if seen_lost and not out["lost"]: back = True
    return errs, lost_n, back, seen_lost
e, l, _, _ = run(lambda i: (120 + i * 4, 160 + i * 2), 40, 4)
rec(f"slow move, loose box, noise: follows (worst {max(e):.0f} px, frames marked lost {l})", max(e) <= 24 and l == 0)
e, l, _, _ = run(lambda i: (60 + i * 18, 200 + (i % 4) * 5), 28, 18)
rec(f"fast move with blur: follows (worst {max(e):.0f} px, lost frames {l})", max(e) <= 30 and l <= 1)
e, l, _, _ = run(lambda i: (200 + (i % 3) * 3, 180) if i < 12 else (430, 300), 40, 3, hide=range(12, 20))
rec(f"hide then reappear elsewhere: dims, then returns on it (last err {e[-1]:.0f} px)", e[-1] <= 24)
e, l, _, _ = run(lambda i: (100 + int(150 * abs(np.sin(i / 9))), 150 + int(90 * abs(np.cos(i / 7)))), 60, 10)
rec(f"wandering back and forth: follows (worst {max(e):.0f} px, lost frames {l})", max(e) <= 30 and l <= 2)
# the brain takes seconds: the object is somewhere else by the time the first live frame arrives (the real cause of "lost" right after asking)
tr = track.Tracker(lambda m: None); tr.start([loose((100, 120))], frame((100, 120), 0), follow=False); errs = []
for i in range(1, 25):
    pos = (330 + i * 3, 260 - i * 2); out = tr.step(frame(pos, i, 3))[0]; errs.append(abs((out["x"] + out["w"] / 2) * W - (pos[0] + 29)) + abs((out["y"] + out["h"] / 2) * H - (pos[1] + 30)))
rec(f"moved far during the brain's thinking time: first live frame finds it (frame 1 err {errs[0]:.0f} px, later worst {max(errs[3:]):.0f} px)", errs[0] <= 30 and max(errs[3:]) <= 24)
print("RESULT", "ALL PASS" if all(R) else "FAILURES")
