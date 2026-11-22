"""Hey Jarvis from an iPhone (PROMPT18 part A). An iPhone Shortcut records 10 seconds of audio and POSTs it to  http://<this machine's home-network address>:8766/<secret>/voice ;
Jarvis transcribes it with the SAME Whisper as everywhere else, answers through the same brain, and returns ONE short plain-text sentence (a trailing "?" means: record again).
OFF until you switch it on in settings. When on, it listens only on the home-network address (never 0.0.0.0), never forwards anything to the internet, takes only audio up to 5 MB, rate-limits,
and answers 404 to everything except the exact secret path. The secret is long and random, lives in a mode-600 file outside git, and is shown only on the settings card. A phone request can do no more
than voice on the Go (no terminal, no phone control unless a separate setting is on, sends to Mac/phone follow the same 'you said send' rule)."""
import asyncio
import hmac
import json
import os
import re
import secrets
import subprocess
import time
from pathlib import Path

import numpy as np
from aiohttp import web

from . import settings

PORT = int(os.environ.get("JARVIS_VOICE_PORT", "8766"))
MAX_BYTES = 5 * 1024 * 1024
MAX_PHOTO = 8 * 1024 * 1024                   # one photo for the Look view (jpeg, or heic from an iPhone)
SECRET_FILE = Path(settings.PATH).parent / "voice_secret"
_runner = {"r": None, "ip": None}
_last = {"t": 0.0, "hour": []}
MIN_GAP_S, MAX_PER_HOUR = 3.0, 40


def secret(create=True):
    try:
        t = SECRET_FILE.read_text().strip()
        if len(t) >= 32:
            return t
    except OSError:
        pass
    if not create:
        return None
    return regenerate()


def regenerate():
    SECRET_FILE.parent.mkdir(mode=0o700, exist_ok=True)
    t = secrets.token_urlsafe(32)
    fd = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(t + "\n")
    os.chmod(SECRET_FILE, 0o600)
    return t


def photo_kind(head):
    """"jpeg" / "heic" / None from the first bytes (never trust a file name or a header)."""
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1", b"hevx"):
        return "heic"
    return None


def to_jpeg(data, kind, outdir):
    """The photo as a jpeg file (at most 1600 px on the long side, rotated upright) inside outdir -> path or None. HEIC goes through heif-convert, then ImageMagick resizes; nothing is kept."""
    import tempfile
    src = tempfile.mkstemp(suffix=".heic" if kind == "heic" else ".jpg", prefix="jarvis-sh-", dir=outdir)[1]
    out = src.rsplit(".", 1)[0] + "-o.jpg"
    try:
        with open(src, "wb") as f:
            f.write(data)
        os.chmod(src, 0o600)
        mid = src
        if kind == "heic":
            mid = src + ".conv.jpg"
            subprocess.run(["heif-convert", "-q", "88", src, mid], check=True, capture_output=True, timeout=30)
        cmd = ["magick", mid + "[0]", "-auto-orient", "-resize", "1600x1600>", "-strip", "-quality", "84", out]
        subprocess.run(cmd, check=True, capture_output=True, timeout=30)
        os.chmod(out, 0o600)
        return out
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        for f in (src, src + ".conv.jpg"):
            try:
                os.unlink(f)
            except OSError:
                pass


def lan_ip():
    """The home-network (Wi-Fi/Ethernet) IPv4 address, from the default route; None if offline."""
    try:
        out = subprocess.run(["ip", "-4", "route", "get", "1.1.1.1"], capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"src (\d+\.\d+\.\d+\.\d+)", out)
        return m.group(1) if m else None
    except Exception:
        return None


def is_audio(b):
    """Magic bytes of the formats an iPhone Shortcut produces (m4a/mp4, wav, caf, mp3, aac). Anything else is refused."""
    return b[4:8] == b"ftyp" or b[:4] == b"RIFF" and b[8:12] == b"WAVE" or b[:4] == b"caff" or b[:3] == b"ID3" or b[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf1", b"\xff\xf9")


def to_pcm(data):
    """Any accepted audio -> 16 kHz mono int16 numpy, via ffmpeg (no file written)."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-t", "30", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"], input=data, capture_output=True, timeout=30)
    if r.returncode or len(r.stdout) < 3200:
        return None
    return np.frombuffer(r.stdout, np.int16)


CLOSING = re.compile(r"\b(?:anything else|is there anything|can i help|let me know if|hope (?:that|it) helps)\b", re.I)


def one_sentence(text):
    """The reply for the phone: the first real sentence (a very short one like "Sure." is joined with the next), plus a final question if the assistant needs your answer. Closing lines are dropped."""
    from . import moods
    t = re.sub(r"\s+", " ", re.sub(r"[*_`#>]+", "", moods.strip(text or ""))).strip()
    parts = [p for p in re.split(r"(?<=[.!?])\s+", t) if p]
    question = parts[-1] if len(parts) > 1 and parts[-1].endswith("?") and not CLOSING.search(parts[-1]) else None
    body = [p for p in parts if p != question and not CLOSING.search(p)]
    out = ""
    for p in body:
        out = (out + " " + p).strip()
        if len(out) >= 18:
            break
    if question:
        out = (out + " " + question).strip()
    return (out or "Sorry, I have nothing to say to that.")[:240]


class Api:
    def __init__(self, assistant, on_request):
        self.a = assistant
        self.on_request = on_request                 # (transcript, reply) -> shows the PHONE REQUEST chip on the Go

    def _gate(self, req):
        """Shared checks for every route: POST only, the exact secret, a rate limit. Raises a 404/429 response."""
        if req.method != "POST":
            raise web.HTTPNotFound()
        want = secret(create=False)
        if not want or not hmac.compare_digest(req.match_info.get("secret", ""), want):
            raise web.HTTPNotFound()
        now = time.time()
        _last["hour"] = [t for t in _last["hour"] if now - t < 3600]
        if now - _last["t"] < MIN_GAP_S or len(_last["hour"]) >= MAX_PER_HOUR:
            raise web.HTTPTooManyRequests(text="Too many requests, wait a moment.")
        _last["t"] = now
        _last["hour"].append(now)

    async def inbox(self, req):
        """The phone hands the Go a note or a link (Share Sheet shortcut): POST text, or JSON {"text":...}/{"url":...}. Shown as a card and kept in data/inbox.jsonl."""
        self._gate(req)
        raw = (await req.read())[:20000].decode("utf-8", "ignore").strip()
        try:
            j = json.loads(raw)
            text, url = str(j.get("text") or ""), str(j.get("url") or "")
        except ValueError:
            text, url = (raw, "") if not raw.startswith("https://") else ("", raw)
        from . import companion
        kind, body = ("link", url) if url.startswith("https://") else ("note", text or url)
        reply = await asyncio.get_running_loop().run_in_executor(None, companion.handle, self.a, "phone", {"type": kind, "text": body, "url": body})
        return web.Response(text=reply or "Nothing to save.", content_type="text/plain")

    async def look(self, req):
        """A photo from the phone camera (Shortcut: Take Photo, then Get Contents of URL with the photo as the request body): the normal Look view and brain run on it and ONE short
        sentence comes back. jpeg or heic up to 8 MB, converted on the Go. The photo is deleted after the answer unless "keep phone photos" is on in settings."""
        self._gate(req)
        if req.content_length is not None and req.content_length > MAX_PHOTO:
            return web.Response(status=413, text="Photo too big (8 MB at most).")
        data = await req.read()
        if len(data) > MAX_PHOTO:
            return web.Response(status=413, text="Photo too big (8 MB at most).")
        kind = photo_kind(data[:16])
        if not kind:
            return web.Response(status=415, text="A jpeg or heic photo only.")
        reply = await asyncio.get_running_loop().run_in_executor(None, self.a.look_photo, data, kind)
        try:
            self.on_request("[photo]", reply)
        except Exception:
            pass
        return web.Response(text=reply, content_type="text/plain", headers={"Cache-Control": "no-store"})

    async def location(self, req):
        """The phone's GPS fix (Shortcut: Get Current Location, then Get Contents of URL with JSON {lat, lon, acc, t}). Only the latest fix is kept, nothing is logged. -> "ok"."""
        self._gate(req)
        raw = (await req.read())[:2000].decode("utf-8", "ignore")
        try:
            j = json.loads(raw)
            lat, lon, acc, t = j.get("lat", j.get("latitude")), j.get("lon", j.get("longitude")), j.get("acc", j.get("accuracy")), j.get("t", j.get("time"))
        except (ValueError, AttributeError):
            import re as _re
            m = _re.findall(r"-?\d+(?:\.\d+)?", raw)                     # "lat,lon" plain text works too
            lat, lon, acc, t = (m + [None] * 4)[:4] if len(m) >= 2 else (None,) * 4
        from . import location
        if not location.save(lat, lon, acc, t):
            return web.Response(status=400, text="I need a real latitude and longitude.")
        return web.Response(text="ok", content_type="text/plain")

    async def ask(self, req):
        """A typed question from the phone (Shortcut "Ask for Input"): same sandboxed path as the voice route."""
        self._gate(req)
        text = (await req.read())[:600].decode("utf-8", "ignore").strip()
        if not text:
            return web.Response(text="Say or type something.")
        reply = await asyncio.get_running_loop().run_in_executor(None, self.a.phone_ask, text)
        return web.Response(text=one_sentence(reply), content_type="text/plain")

    async def handle(self, req):
        if req.method != "POST":
            raise web.HTTPNotFound()                  # even the right path says nothing to a GET
        want = secret(create=False)
        got = req.match_info.get("secret", "")
        if not want or not hmac.compare_digest(got, want):
            raise web.HTTPNotFound()                  # no hint that anything is here
        now = time.time()
        _last["hour"] = [t for t in _last["hour"] if now - t < 3600]
        if now - _last["t"] < MIN_GAP_S or len(_last["hour"]) >= MAX_PER_HOUR:
            return web.Response(status=429, text="Too many requests, wait a moment.")
        _last["t"] = now
        _last["hour"].append(now)
        if req.content_length is not None and req.content_length > MAX_BYTES:
            return web.Response(status=413, text="Recording too big.")
        data = await req.read()
        if len(data) > MAX_BYTES or not is_audio(data[:16]):
            return web.Response(status=415, text="Audio only.")
        loop = asyncio.get_running_loop()
        pcm = await loop.run_in_executor(None, to_pcm, data)
        if pcm is None:
            return web.Response(status=422, text="I could not read that recording.")
        from . import stt
        text = await loop.run_in_executor(None, stt.transcribe, pcm)
        if not text:
            return web.Response(text="I didn't catch that.")
        reply = await loop.run_in_executor(None, self.a.phone_ask, text)
        reply = one_sentence(reply)
        try:
            self.on_request(text, reply)
        except Exception:
            pass
        return web.Response(text=reply, content_type="text/plain", headers={"Cache-Control": "no-store"})


async def start(assistant, on_request):
    """Start listening (idempotent). -> (ok, message). Binds ONLY to the home-network address."""
    if _runner["r"]:
        return True, "already on"
    ip = lan_ip()
    if not ip:
        return False, "No home network address found: connect to Wi-Fi first."
    secret()                                         # make it now if it does not exist yet (mode 600)
    app = web.Application(client_max_size=MAX_PHOTO + 1024)
    api = Api(assistant, on_request)
    app.router.add_route("*", "/{secret}/voice", api.handle)
    app.router.add_route("*", "/{secret}/inbox", api.inbox)
    app.router.add_route("*", "/{secret}/ask", api.ask)
    app.router.add_route("*", "/{secret}/look", api.look)
    app.router.add_route("*", "/{secret}/location", api.location)
    r = web.AppRunner(app, access_log=None)
    await r.setup()
    try:
        await web.TCPSite(r, ip, PORT).start()
    except OSError as e:
        await r.cleanup()
        return False, "Port %d is busy (%s)." % (PORT, type(e).__name__)
    _runner.update(r=r, ip=ip)
    return True, f"Listening on {ip}:{PORT}"


async def stop():
    if _runner["r"]:
        await _runner["r"].cleanup()
        _runner.update(r=None, ip=None)


def is_on():
    return _runner["r"] is not None


def url():
    """The full address for the Shortcut (contains the secret: only ever shown on the settings card)."""
    ip = _runner["ip"] or lan_ip() or "<this machine's IP>"
    return f"http://{ip}:{PORT}/{secret()}/voice"


def mask(u):
    s = secret(create=False) or ""
    return u.replace(s, s[:4] + "…" + s[-4:]) if s else u


def guide_card(on):
    steps = [
        {"n": 1, "text": "Turn the phone endpoint on. It listens only on your home Wi-Fi address, only while it is on.", "buttons": [{"label": "TURN OFF" if on else "TURN ON", "action": "voice_toggle"}, {"label": "NEW SECRET", "action": "voice_regen"}],
         "hint": "The recording is always 10 seconds. It works only on your home Wi-Fi with the Go on. Keep your iPhone passcode on."},
        {"n": 2, "text": "On the iPhone open Shortcuts and make a new shortcut with these actions, in this order:", "hint": "1) Record Audio (stop after 10 seconds)   2) Get Contents of URL: paste the address below, Method POST, Request Body: File, the recorded audio   3) Show Notification with the result   4) Speak Text with the result   5) If the result ends with ?, Run Shortcut (this same one) again", "copy": {"label": "COPY ADDRESS", "mask": mask(url()), "action": "voice_copy"}},
        {"n": 3, "text": "Optional, the camera: a second shortcut so Jarvis can look at a photo, with no camera app open on the phone.", "hint": "1) Take Photo (turn Show Camera Preview off)   2) Get Contents of URL: the address below (it ends in /look), Method POST, Request Body: File, the photo   3) Show Notification with the result   4) Speak Text with the result. jpeg or heic up to 8 MB; the photo is deleted after the answer unless you turn on keep phone photos in settings.",
         "copy": {"label": "COPY THE PHOTO ADDRESS", "mask": mask(url()).replace("/voice", "/look"), "action": "voice_copy_look"}},
        {"n": 4, "text": "Say it hands-free: Settings > Accessibility > Vocal Shortcuts > Set Up Vocal Shortcuts. Choose your shortcut, phrase \"Hey Jarvis\", say it three times.", "hint": "Or add the shortcut to the Action Button or the Lock Screen."},
        {"n": 5, "text": "Location: a third shortcut \"Send location to Jarvis\" gives \"near me\", the map and the weather your real position (the Go has no GPS).", "hint": "1) Get Current Location   2) Get Contents of URL: the address below, Method POST, Request Body JSON: lat = Latitude, lon = Longitude, acc = Horizontal Accuracy.  Run it by tap, by Siri, or as an Automation (when you arrive home, or every hour). Only the latest fix is kept, with its age; no history.",
         "copy": {"label": "COPY THE LOCATION ADDRESS", "mask": mask(url()).replace("/voice", "/location"), "action": "voice_copy_location"}},
        {"n": 6, "text": "Check that the endpoint answers from here:", "buttons": [{"label": "TEST", "action": "voice_test"}]}]
    return {"type": "guide", "gid": "voice", "title": "Hey Jarvis from your iPhone", "full": True, "secret": True, "paged": True, "status": "ON: listening on your home network" if on else "OFF", "steps": steps}
