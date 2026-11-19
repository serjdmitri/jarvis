"""Phone as a camera (optional, separate from the built-in cameras, which stay disabled; no ipu3/cio2 module is ever loaded).
You run a free app on the phone (IP Webcam on Android, DroidCam on Android/iPhone) that serves its camera over Wi-Fi, and type its address into SETTINGS > PHONE CAMERA.
Only that address is used, and only if it is on your own network (private IP range or a .local name). The address is stored in the settings file and never logged."""
import ipaddress
import os
import re
import subprocess
import time
import urllib.parse

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
        {"n": 1, "text": "On your phone install a free app that shares its camera over Wi-Fi.", "hint": "Android: IP Webcam. Android or iPhone: DroidCam. Start its server and note the address it shows."},
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
