"""Phone as a camera (optional, separate from the built-in cameras, which stay disabled; no ipu3/cio2 module is ever loaded).
You run a free app on the phone (IP Webcam on Android, DroidCam on Android/iPhone) that serves its camera over Wi-Fi, and type its address into SETTINGS > PHONE CAMERA.
Only that address is used, and only if it is on your own network (private IP range or a .local name). The address is stored in the settings file and never logged."""
import ipaddress
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from . import config, settings


def validate(url):
    """-> the cleaned URL or None. http(s) only, a private/LAN host only, no user:password, a plain path."""
    u = (url or "").strip()
    if not re.fullmatch(r"https?://[^\s]+", u) or len(u) > 300:
        return None
    p = urllib.parse.urlsplit(u)
    if p.username or p.password or not p.hostname:
        return None
    h = p.hostname.lower()
    ok = False
    try:
        ip = ipaddress.ip_address(h)
        ok = ip.is_private or ip.is_link_local
    except ValueError:
        ok = h.endswith(".local") and re.fullmatch(r"[a-z0-9.-]+", h) is not None
    return u if ok else None


def get_url():
    return validate(settings.get("phone_cam_url") or "")


DEFAULTS = (4747, 8080)                       # DroidCam, IP Webcam
PATHS = {4747: ("/video", "/mjpegfeed"), 8080: ("/video", "/videofeed")}


def normalise(typed):
    """Whatever you type -> (list of full http addresses to try, [plain notes]) or (None, [reason]).
    No scheme: http. https: changed to http (phone cameras do not use it). No port: DroidCam 4747 then IP Webcam 8080. No path: the common ones in turn."""
    t = (typed or "").strip()
    notes = []
    if not t or len(t) > 200 or re.search(r"\s", t):
        return None, ["Type the phone's address, for example 192.168.1.50"]
    if re.match(r"https://", t, re.I):
        notes.append("https is not used here, trying http")
        t = "http://" + t[8:]
    elif not re.match(r"http://", t, re.I):
        t = "http://" + t
    p = urllib.parse.urlsplit(t)
    if p.username or p.password or not p.hostname:
        return None, ["Leave out any user name or password."]
    h = p.hostname.lower()
    try:
        ip = ipaddress.ip_address(h)
        okhost = ip.is_private or ip.is_link_local
    except ValueError:
        okhost = h.endswith(".local") and re.fullmatch(r"[a-z0-9.-]+", h) is not None
    if not okhost:
        return None, ["That is not an address on your own network. I only connect to 192.168.x.x, 10.x.x.x or 172.16-31.x.x (or a .local name)."]
    ports = [p.port] if p.port else list(DEFAULTS)
    out = []
    for port in ports:
        paths = [p.path] if p.path and p.path != "/" else list(PATHS.get(port, sum(PATHS.values(), ())))
        for path in dict.fromkeys(paths):
            out.append(f"http://{h}:{port}{path}")
    return out, notes


def reason(err):
    e = (err or "").lower()
    if "refused" in e:
        return "nothing is listening on that port: is the app open and showing its address?"
    if "timed out" in e or "timeout" in e or "no route" in e or "unreachable" in e:
        return "no answer: the phone must be on the same Wi-Fi as this laptop, and the app must stay open in front."
    return "the phone did not give a video there (" + (err or "error")[:60] + ")."


def probe(candidates):
    """Try every candidate at once (short timeouts, so a wrong address cannot freeze anything). -> (working url or None, plain reasons list)."""
    import concurrent.futures as cf
    reasons = []
    with cf.ThreadPoolExecutor(max(1, len(candidates))) as ex:
        futs = {ex.submit(grab, u, 4, 1): u for u in candidates}
        order = {u: i for i, u in enumerate(candidates)}
        results = {}
        for f in cf.as_completed(futs, timeout=14):
            u = futs[f]
            try:
                results[u] = (True, f.result())
            except RuntimeError as e:
                results[u] = (False, str(e))
            except Exception as e:
                results[u] = (False, type(e).__name__)
    for u in sorted(results, key=order.get):
        if results[u][0]:
            return u, results[u][1], reasons
    errs = [r[1] for r in results.values()]
    reasons.append(reason(errs[0] if errs else ""))
    return None, None, reasons


def save(url):
    """Normalise and store what you typed as the address TO TRY; TEST finds and saves the exact one that works. -> (ok, message with the full addresses)."""
    cands, notes = normalise(url)
    if not cands:
        return False, notes[0]
    settings.update(phone_cam_typed=url.strip()[:200])
    return True, (" ".join(n + "." for n in notes) + " " if notes else "") + "I will try: " + ", ".join(cands) + ". Press TEST."


def _sharpness(path):
    """Variance of the Laplacian on a small grey copy: higher is sharper."""
    import numpy as np
    try:
        raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path, "-vf", "scale=320:240,format=gray", "-f", "rawvideo", "-"], capture_output=True, timeout=6).stdout
        a = np.frombuffer(raw, np.uint8).astype(np.float32)
        if a.size != 320 * 240:
            return 0.0
        a = a.reshape(240, 320)
        lap = 4 * a[1:-1, 1:-1] - a[:-2, 1:-1] - a[2:, 1:-1] - a[1:-1, :-2] - a[1:-1, 2:]
        return float(lap.var())
    except Exception:
        return 0.0


def grab(url=None, timeout=4, frames=8):
    """A sharp frame from the phone's stream with ffmpeg -> path of a jpg in shots/ (shot-<ms>.jpg, so the page can show it). The first frames after connecting are dark or soft:
    8 are read, the first 3 skipped, and the sharpest of the rest is kept. Raises RuntimeError with a plain message. Short timeouts: a bad address never freezes anything."""
    import shutil
    import tempfile
    u = validate(url) if url else get_url()
    if not u:
        raise RuntimeError("No phone camera address is set. Open SETTINGS > PHONE CAMERA.")
    config.SHOTS_DIR.mkdir(exist_ok=True)
    if not url or u == get_url():                                      # the saved camera: one shared connection, never a second viewer
        data = RELAY.snapshot(wait=max(timeout, 6))[0] if RELAY.running() else RELAY.frame(wait=max(timeout, 6))     # live already: the frame of this moment
        path = str(config.SHOTS_DIR / f"shot-{int(time.time() * 1000)}.jpg")
        with open(path, "wb") as f:
            f.write(data)
        for f in sorted(config.SHOTS_DIR.glob("shot-*.jpg"))[:-9]:
            f.unlink()
        return path
    tmp = tempfile.mkdtemp(prefix="phonecam-")
    try:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-rw_timeout", str(int(timeout * 1000000)), "-i", u, "-frames:v", str(frames), "-vf", "scale='min(1280,iw)':-2", "-q:v", "3", os.path.join(tmp, "f%02d.jpg")]
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout + 6)
        except subprocess.TimeoutExpired:
            pass
        files = sorted(f for f in os.listdir(tmp) if f.endswith(".jpg"))
        if not files:
            r = subprocess.run(cmd[:-1] + [os.path.join(tmp, "x.jpg")], capture_output=True, timeout=timeout + 3) if False else None
            raise RuntimeError("timed out" if not files else "no picture")
        pool = [os.path.join(tmp, f) for f in (files[3:] if len(files) > 3 else files)]
        best = max(pool, key=_sharpness) if len(pool) > 1 else pool[0]
        if os.path.getsize(best) < 2000:
            raise RuntimeError("no picture")
        path = str(config.SHOTS_DIR / f"shot-{int(time.time() * 1000)}.jpg")
        shutil.copyfile(best, path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for f in sorted(config.SHOTS_DIR.glob("shot-*.jpg"))[:-9]:
        f.unlink()
    return path


def setup_card():
    cur = get_url()
    return {"type": "guide", "gid": "phonecam", "title": "Phone camera", "full": True, "paged": False, "status": "Saved: " + ("yes" if cur else "no address yet"), "steps": [
        {"n": 1, "text": "On your phone install a free app that shares its camera over Wi-Fi.", "hint": "Android: IP Webcam. Android or iPhone: DroidCam. Start its server and note the address it shows. Plainly: DroidCam on an iPhone only works while the app is open in front and the screen is awake (iOS pauses it otherwise). Set Auto-Lock to Never while it is plugged in, or use the photo Shortcut (no app needs to be open: see the iPhone voice card), or a USB webcam if you want it always on. The stream starts only when you ask me to look and stops afterwards."},
        {"n": 2, "text": "Type that address here (it must be on your own Wi-Fi):", "input": {"placeholder": "just the IP, e.g. 192.168.1.50", "label": "SAVE", "action": "phonecam_save"},
         "hint": "Type just the IP the app shows. I add http, the port (DroidCam 4747, IP Webcam 8080) and the video path for you, and show the full addresses I will try."},
        {"n": 3, "text": "Test it and look at it live:", "buttons": [{"label": "TEST", "action": "phonecam_test"}, {"label": "LIVE PREVIEW", "action": "phonecam_preview"}, {"label": "USE FOR LOOK", "action": "phonecam_use"}]}]}


def parse_command(text):
    import re as _re
    low = text.lower().strip(" .?!")
    if _re.fullmatch(r"(?:set ?up|setup|connect|configure|open) (?:my |the )?phone camera|phone camera (?:setup|settings)", low):
        return {"kind": "setup"}
    if _re.fullmatch(r"(?:use|switch to) (?:my |the )?phone camera|look (?:with|through|using) (?:my |the )?phone", low):
        return {"kind": "use"}
    return None



# ----------------------------------------------------------------------------- one reader for the phone (DroidCam allows ONE viewer at a time)
class Relay:
    """Reads the phone's MJPEG stream over ONE connection and keeps the newest JPEG; the LIVE view, the Phone Camera preview and a "what do you see" frame grab all take from it, so the
    phone never sees a second viewer (it refuses one). Starts on the first consumer and stops 6 s after the last one leaves: the camera is off until needed."""

    def __init__(self):
        self.lock = threading.Lock()
        self.latest, self.t, self.n, self.consumers, self.err = None, 0.0, 0, 0, ""
        self.thread, self.stop_at, self.url = None, 0.0, None

    def _run(self, url):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "jarvis-local"})
            with urllib.request.urlopen(req, timeout=6) as r:
                buf = b""
                while time.time() < self.stop_at or self.consumers > 0:
                    chunk = r.read(16384)
                    if not chunk:
                        self.err = "the phone closed the stream"
                        break
                    buf += chunk
                    while True:
                        a = buf.find(b"\xff\xd8")
                        b = buf.find(b"\xff\xd9", a + 2) if a >= 0 else -1
                        if a < 0 or b < 0:
                            break
                        with self.lock:
                            self.latest, self.t, self.n = buf[a:b + 2], time.time(), self.n + 1
                        buf = buf[b + 2:]
                    if len(buf) > 4_000_000:
                        buf = b""
                    if self.consumers <= 0 and time.time() > self.stop_at:
                        break
        except Exception as e:
            self.err = "could not read the phone's video (" + type(e).__name__ + ")"
        finally:
            if self.thread is threading.current_thread():              # only clear our own slot: a newer reader (after an address change) must not be orphaned
                self.thread = None

    def want(self, seconds=8):
        """Make sure the reader runs for at least `seconds` more (a consumer holds it open longer)."""
        url = get_url()
        if not url:
            raise RuntimeError("No phone camera address is set. Open SETTINGS > PHONE CAMERA.")
        self.stop_at = max(self.stop_at, time.time() + seconds)
        if self.thread is None or not self.thread.is_alive() or self.url != url:
            self.err, self.latest, self.n, self.url = "", None, 0, url
            self.thread = threading.Thread(target=self._run, args=(url,), daemon=True)
            self.thread.start()

    def frame(self, wait=8.0, skip=6, frames=6):
        """A sharp JPEG: wait for the connection, skip the first `skip` frames (dark or blurry while the phone's exposure settles), take the sharpest of the next few. -> bytes. RuntimeError with the reason."""
        self.want(wait + 4)
        t0, got = time.time(), []
        seen = -1
        while time.time() - t0 < wait:
            with self.lock:
                n, f = self.n, self.latest
            if f and n != seen:
                seen = n
                if n > skip:
                    got.append(f)
                    if len(got) >= frames:
                        break
            if self.err and not f:
                break
            time.sleep(0.04)
        if not got:
            raise RuntimeError(self.err or "timed out waiting for the phone's video")
        return max(got, key=lambda b: _sharp_bytes(b))

    def snapshot(self, wait=6.0, min_frames=3):
        """A COPY of the newest frame at this moment, no waiting for a sharper one, no second connection: (jpeg bytes, frame number, time). Frames are immutable bytes, so the live view never touches it."""
        self.want(wait + 4)
        t0 = time.time()
        while time.time() - t0 < wait:
            with self.lock:
                n, f, t = self.n, self.latest, self.t
            if f and n >= min_frames:
                return f, n, t
            if self.err and not f:
                break
            time.sleep(0.03)
        raise RuntimeError(self.err or "timed out waiting for the phone's video")

    def running(self):
        return bool(self.thread and self.thread.is_alive() and self.latest is not None and time.time() - self.t < 3)

    def hold(self):
        self.want(8)                                                   # may raise (no address): count the consumer only after it worked, or the camera would stay on
        self.consumers += 1

    def release(self):
        self.consumers = max(0, self.consumers - 1)
        self.stop_at = time.time() + 6


def _sharp_bytes(b):
    """A cheap sharpness score for a JPEG: bigger files at the same size carry more fine detail."""
    return len(b)


RELAY = Relay()


def explain(reason):
    """Plain words for why the phone camera could not be read."""
    r = (reason or "").lower()
    if "no phone camera address" in r:
        return "No phone camera is saved. Open settings and add its address."
    return ("I can't reach the phone camera. Check that DroidCam is open and in front on the iPhone with the screen awake, that the phone is on the same Wi-Fi, "
            "and that the address is right. Also close any other viewer of the stream.")
