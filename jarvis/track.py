"""Keeps the highlight boxes on their objects while the camera stays live. The vision step finds the objects (about once, or on demand); between detections a cheap tracker follows them:
template matching on a tiny grey picture (libjpeg decodes the phone's JPEG at 1/4 size), numpy only, nothing extra installed (OpenCV is not on this machine, and adding it needs a yes).
Each object keeps a small patch; every new frame the patch is searched for near where it was. A bad match = the object left or is hidden: the box dims and says "lost <thing>", and the whole picture is searched
now and then so it comes back when the object reappears. It also learns the patch slowly so a turning object keeps matching. No faces, no recognition, no frames stored (only the patches live in memory).
It reads the ONE shared phone reader (never a second viewer), runs at about 8 frames a second, and stops by itself when the LOOK view closes or the board is hot."""
import json
import os
import re
import subprocess
import threading
import time

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from . import perf, phonecam

FPS = 8
SCALE = "1/4"
LOST_AFTER = 4             # bad matches in a row before "lost"
REACQ_EVERY = 4            # a lost object is searched for in the whole picture every Nth frame
MAX_TRACKS = 6
HOT_C = 74.0
DEBUG_LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "track-debug.log")


def debug_on():
    """Per-frame tracker log (numbers only: no pictures, no names of people). Off unless JARVIS_DEBUG_TRACK=1 or the setting debug_track is on."""
    if os.environ.get("JARVIS_DEBUG_TRACK"):
        return True
    try:
        from . import settings
        return bool(settings.get("debug_track"))
    except Exception:
        return False


def dlog(rec):
    try:
        os.makedirs(os.path.dirname(DEBUG_LOG), exist_ok=True)
        if os.path.exists(DEBUG_LOG) and os.path.getsize(DEBUG_LOG) > 1_000_000:
            os.replace(DEBUG_LOG, DEBUG_LOG + ".1")
        with open(DEBUG_LOG, "a") as f:
            f.write(json.dumps(rec, separators=(",", ":"), default=float) + "\n")
    except Exception:                                                                 # a log problem must never stop the tracking
        pass


def grey(jpeg):
    """JPEG bytes -> a float32 grey array (rows, cols) at 1/4 size, or None."""
    try:
        r = subprocess.run(["djpeg", "-grayscale", "-scale", SCALE, "-pnm"], input=jpeg, capture_output=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.match(rb"P5\s+(\d+)\s+(\d+)\s+255\s", r.stdout)
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    d = r.stdout[m.end():]
    return np.frombuffer(d, dtype=np.uint8).reshape(h, w).astype(np.float32) if len(d) == w * h else None


LAST_PEAK = 1.0


def _set_peak(v):
    global LAST_PEAK
    LAST_PEAK = v


def _match(img, tpl, x0, y0, x1, y1, step=1):
    """Best place for `tpl` inside img[y0:y1, x0:x1] -> (x, y, d). d is the mean difference between the CONTRAST-NORMALISED patch and window (brightness AND contrast removed, so exposure drift,
    a shadow or a passing light do not matter): about 0 = identical, 1 = unrelated. LAST_PEAK says how much better the best place is than a typical place."""
    th, tw = tpl.shape
    reg = img[y0:y1, x0:x1]
    if reg.shape[0] < th or reg.shape[1] < tw:
        return None
    win = sliding_window_view(reg, (th, tw))[::step, ::step]
    wm = win.mean(axis=(2, 3), keepdims=True)
    ws = np.maximum(win.std(axis=(2, 3), keepdims=True), 2.0)
    t = (tpl - tpl.mean()) / max(float(tpl.std()), 2.0)
    d = np.abs((win - wm) / ws - t).mean(axis=(2, 3))
    j = np.unravel_index(int(np.argmin(d)), d.shape)
    global LAST_PEAK
    LAST_PEAK = float(d[j]) / (float(np.median(d)) + 1e-6)
    return x0 + j[1] * step, y0 + j[0] * step, float(d[j])


class Track:
    INSET = 0.2                                                                   # the brain's box is loose: only its middle 60% is the patch that is followed (less background in it), the box keeeps its own size around it

    def __init__(self, name, box, img):
        h, w = img.shape
        self.name = name
        self.x, self.y = int(round(box["x"] * w)), int(round(box["y"] * h))
        self.w, self.h = max(6, int(round(box["w"] * w))), max(6, int(round(box["h"] * h)))
        self.x, self.y = min(max(0, self.x), w - self.w), min(max(0, self.y), h - self.h)
        self.ox, self.oy = (int(self.w * self.INSET), int(self.h * self.INSET)) if min(self.w, self.h) >= 16 else (0, 0)
        self.tpl = img[self.y + self.oy:self.y + self.h - self.oy, self.x + self.ox:self.x + self.w - self.ox].copy()
        self.th, self.tw = self.tpl.shape
        self.k = max(1, -(-max(self.tw, self.th) // 24))                        # big objects are matched on a thinned picture (patch about 24 px): memory and time stay small whatever the box size
        self.limit = 0.62 if float(self.tpl.std()) >= 4 else 0.5                # what counts as "still the same thing" (normalised difference; a nearly flat patch is held to a stricter rule)
        self.bad, self.lost, self.n = 0, False, 0
        self.score, self.why, self.catchup = 0.0, "start", True               # catchup: the first live frame is searched in the WHOLE picture too (the brain took seconds, the object may have moved a lot since)

    def _m(self, img, x0, y0, x1, y1, step):
        """Search for the patch inside the window (patch coordinates)."""
        k = self.k
        if k == 1:
            return _match(img, self.tpl, x0, y0, x1, y1, step)
        r = _match(img[::k, ::k], self.tpl[::k, ::k], x0 // k, y0 // k, -(-x1 // k), -(-y1 // k), step)
        if r is None:
            return None
        return min(r[0] * k, img.shape[1] - self.tw), min(r[1] * k, img.shape[0] - self.th), r[2]

    def _whole(self, img):
        H, W = img.shape
        r = self._m(img, 0, 0, W, H, 1)
        if r is not None and LAST_PEAK > 0.6:
            r = (r[0], r[1], 1e9)                                                # not distinctive (many places look the same): do not jump anywhere
        return r

    def update(self, img):
        H, W = img.shape
        self.n += 1
        if self.lost and not self.catchup:
            if self.n % REACQ_EVERY:
                self.why = "lost: waiting for the next whole-picture search"
                return
            r, mode = self._whole(img), "whole picture (lost)"
        else:
            g = 1.0 + 0.5 * self.bad                                              # a miss widens the search a little each time
            px, py = self.x + self.ox, self.y + self.oy
            mx, my = max(8, int(self.tw * 0.8 * g)), max(8, int(self.th * 0.8 * g))
            r, mode = self._m(img, max(0, px - mx), max(0, py - my), min(W, px + self.tw + mx), min(H, py + self.th + my), 1), "near the last place"
            if self.catchup:                                                      # the first live frame after the brain's seconds: take the better of near and whole picture
                w = self._whole(img)
                if w is not None and (r is None or w[2] < r[2] * 0.85):
                    r, mode = w, "whole picture (catch-up to the live frame)"
        if r is None:
            self.why = "no search area"
            return
        x, y, d = r
        self.score = d
        self.catchup = False
        if d <= self.limit:
            self.x, self.y, self.bad = min(max(0, x - self.ox), W - self.w), min(max(0, y - self.oy), H - self.h), 0
            self.why = f"matched {mode} (d={d:.2f} <= {self.limit:.2f})"
            if self.lost:
                self.lost = False
            elif d < self.limit * 0.6:                                            # learn only from clearly good matches, or a wrong match would pollute the patch
                self.tpl = 0.9 * self.tpl + 0.1 * img[y:y + self.th, x:x + self.tw]
        else:
            self.bad += 1
            self.why = f"bad match {mode} (d={d:.2f} > {self.limit:.2f}) x{self.bad}"
            if self.bad >= LOST_AFTER:
                self.lost = True

    def out(self, W, H):
        return {"name": self.name, "x": round(self.x / W, 4), "y": round(self.y / H, 4), "w": round(self.w / W, 4), "h": round(self.h / H, 4), "lost": self.lost}


class Tracker:
    def __init__(self, notify):
        self.notify = notify
        self.on_out = None
        self.quiet = False
        self.tracks, self.size = [], (1, 1)
        self.lock = threading.Lock()
        self.stop_ev = threading.Event()
        self.thread = None
        self.debug = debug_on()
        self.stats = {"frames": 0, "ms": 0.0, "max_ms": 0.0, "temp": None, "off_hot": False}

    def start(self, boxes, jpeg, active=lambda: True, quiet=False, follow=True):
        """Begin following `boxes` (found on the frame `jpeg`), then catch up to the live view straight away."""
        img = grey(jpeg)
        self.debug = debug_on()
        if img is None or not boxes:
            if self.debug:
                dlog({"t": round(time.time(), 2), "start": False, "why": "no picture" if img is None else "no boxes"})
            return False
        if self.debug:
            dlog({"t": round(time.time(), 2), "start": True, "frame": [img.shape[1], img.shape[0]], "boxes": [[b["name"], b["x"], b["y"], b["w"], b["h"]] for b in boxes[:MAX_TRACKS]]})
        with self.lock:
            H, W = img.shape
            self.size = (W, H)
            self.tracks = [Track(b["name"], b, img) for b in boxes[:MAX_TRACKS]]
        self.quiet = quiet
        self.active = active                                                       # the newest caller's rule for when to stop (a finished connect guide no longer keeps it alive)
        self.stop_ev.clear()
        if follow and not (self.thread and self.thread.is_alive()):
            self.thread = threading.Thread(target=self._run, args=(), daemon=True)
            self.thread.start()
        return True

    def current(self):
        """The boxes where the tracker has them right now (same order as they were started)."""
        with self.lock:
            return [t.out(*self.size) for t in self.tracks]

    def retain(self, idx):
        """Keep only the tracks at these positions (selective highlights)."""
        with self.lock:
            self.tracks = [self.tracks[i] for i in idx if 0 <= i < len(self.tracks)]

    def stop(self):
        self.stop_ev.set()
        with self.lock:
            self.tracks = []

    def step(self, jpeg):
        """One frame in -> boxes out (also used by tests)."""
        img = grey(jpeg)
        if img is None:
            return None
        t0 = time.time()
        with self.lock:
            for t in self.tracks:
                t.update(img)
            out = [t.out(*self.size) for t in self.tracks]
            if self.debug:
                dlog({"t": round(time.time(), 2), "n": self.stats["frames"], "frame": list(self.size), "tracks": [{"name": t.name, "px": [t.x, t.y, t.w, t.h], "d": round(t.score, 2), "limit": round(t.limit, 2), "bad": t.bad, "lost": t.lost, "why": t.why, "out": [o["x"], o["y"], o["w"], o["h"]]} for t, o in zip(self.tracks, out)]})
        ms = (time.time() - t0) * 1000
        s = self.stats
        s["frames"] += 1
        s["ms"] += (ms - s["ms"]) / min(s["frames"], 30)
        s["max_ms"] = max(s["max_ms"], ms)
        return out

    def _run(self):
        last_n, tick = -1, 0
        try:
            phonecam.RELAY.hold()
        except RuntimeError:
            return
        try:
            while not self.stop_ev.is_set() and self.active():
                f, n = phonecam.RELAY.latest, phonecam.RELAY.n
                tick += 1
                if tick % 40 == 0:
                    t = perf.cpu_temp_c()
                    self.stats["temp"] = t
                    if t and t >= HOT_C:                                         # over budget: stop following, say so once
                        self.stats["off_hot"] = True
                        self.notify({"t": "look", "op": "track", "boxes": [], "off": "The board is warm, so I stopped following the objects."})
                        return
                if f and n != last_n:
                    last_n = n
                    out = self.step(f)
                    if out is not None:
                        if self.on_out:
                            self.on_out(out)
                        if not self.quiet:
                            self.notify({"t": "look", "op": "track", "boxes": out})
                time.sleep(1.0 / FPS)
        finally:
            phonecam.RELAY.release()
