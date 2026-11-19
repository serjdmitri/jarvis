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


def one_sentence(text):
    t = re.sub(r"\s+", " ", re.sub(r"[*_`#>]+", "", text or "")).strip()
    parts = re.split(r"(?<=[.!?])\s+", t)
    out = parts[0] if parts else t
    if len(parts) > 1 and parts[-1].endswith("?"):
        out = (out + " " + parts[-1]).strip() if out != parts[-1] else out
    return out[:240] or "Sorry, I have nothing to say to that."


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
    app = web.Application(client_max_size=MAX_BYTES + 1024)
    api = Api(assistant, on_request)
    app.router.add_route("*", "/{secret}/voice", api.handle)
    app.router.add_route("*", "/{secret}/inbox", api.inbox)
    app.router.add_route("*", "/{secret}/ask", api.ask)
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
        {"n": 3, "text": "Say it hands-free: Settings > Accessibility > Vocal Shortcuts > Set Up Vocal Shortcuts. Choose your shortcut, phrase \"Hey Jarvis\", say it three times.", "hint": "Or add the shortcut to the Action Button or the Lock Screen."},
        {"n": 4, "text": "Check that the endpoint answers from here:", "buttons": [{"label": "TEST", "action": "voice_test"}]}]
    return {"type": "guide", "gid": "voice", "title": "Hey Jarvis from your iPhone", "full": True, "secret": True, "paged": True, "status": "ON: listening on your home network" if on else "OFF", "steps": steps}
