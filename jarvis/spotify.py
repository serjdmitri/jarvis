"""Spotify setup and login (PKCE: no Client Secret is ever needed). Guided by a card on the Go.
Files (mode 600, in the user's config dir, never in git): spotify_client_id, spotify_tokens.json. Nothing here prints or logs the client id, a code or a token;
errors are turned into plain words. If Spotify refuses, the card says why and music stays on YouTube."""
import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

REDIRECT = "http://127.0.0.1:8888/callback"
SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing streaming"
AUTH, TOKEN, API = "https://accounts.spotify.com/authorize", "https://accounts.spotify.com/api/token", "https://api.spotify.com/v1"
DEVICE_NAME = "Jarvis"
TEST_TRACK = "spotify:track:11dFghVXANMlKmJXsNCbNl"
_login = {"verifier": None, "state": None, "server": None}


def _dir():
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "jarvis")


def _write600(name, text):
    os.makedirs(_dir(), mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(_dir(), name), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(os.path.join(_dir(), name), 0o600)


def _read(name):
    try:
        return open(os.path.join(_dir(), name)).read().strip()
    except OSError:
        return None


def save_client_id(value):
    v = re.sub(r"\s+", "", value or "")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", v):
        return False, "That does not look like a Client ID (it is 32 letters and numbers). Copy it from the app's Settings page. Do not paste the Client Secret."
    _write600("spotify_client_id", v + "\n")
    return True, "Client ID saved. It is stored privately and will not be shown again."


def have_client_id():
    return bool(_read("spotify_client_id"))


def connected():
    t = _tokens()
    return bool(t and t.get("refresh_token"))


def _tokens():
    try:
        return json.loads(_read("spotify_tokens.json") or "")
    except ValueError:
        return None


def pkce():
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def explain(err, status=None):
    """Spotify error -> plain words (never includes ids or tokens)."""
    e = (err or "").lower()
    table = [("invalid client", "Spotify does not know that Client ID. Check it was copied from your app's Settings."), ("invalid_client", "Spotify does not know that Client ID. Check it was copied from your app's Settings."),
             ("redirect", "Spotify says the redirect address does not match. In your app's settings the redirect must be exactly http://127.0.0.1:8888/callback (numbers, not the word localhost)."),
             ("premium_required", "Spotify says playing from other apps needs Premium on this account."),
             ("no_active_device", "Spotify has no player running to play on: the Jarvis player (spotifyd or librespot) is not running on the Go."),
             ("access_denied", "You pressed Cancel on Spotify's page, so nothing was connected."),
             ("invalid_grant", "Spotify refused the login code (it may have expired). Press the login button again."),
             ("user may not be registered", "Spotify says this account is not allowed on the app yet: in the app's Settings > User Management add your own Spotify email, or turn on the Web API.")]
    for k, v in table:
        if k in e:
            return v
    if status in (401, 403):
        return "Spotify refused the request (sign-in or app rules, code %s). Spotify has been changing its developer rules." % status
    if status == 429:
        return "Spotify says too many requests right now. Try again in a minute."
    return "Spotify did not accept that (%s). Music stays on YouTube." % (status or "network problem")


def auth_url():
    cid = _read("spotify_client_id")
    if not cid:
        return None
    verifier, challenge = pkce()
    _login["verifier"], _login["state"] = verifier, secrets.token_urlsafe(16)
    q = {"client_id": cid, "response_type": "code", "redirect_uri": REDIRECT, "code_challenge_method": "S256", "code_challenge": challenge, "scope": SCOPES, "state": _login["state"]}
    return AUTH + "?" + urllib.parse.urlencode(q)


def _post_token(data):
    req = urllib.request.Request(TOKEN, data=urllib.parse.urlencode(data).encode(), headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "jarvis-local"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def exchange(code):
    """-> (ok, plain-words message). Saves the tokens (mode 600)."""
    try:
        j = _post_token({"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "client_id": _read("spotify_client_id"), "code_verifier": _login["verifier"]})
    except urllib.error.HTTPError as e:
        try:
            j = json.load(e)
            body = f"{j.get('error', '')} {j.get('error_description', '')}"
        except Exception:
            body = ""
        return False, explain(body or "", e.code)
    except Exception as e:
        return False, "Could not reach Spotify (" + type(e).__name__ + ")."
    j["expires_at"] = time.time() + int(j.get("expires_in", 3600)) - 60
    _write600("spotify_tokens.json", json.dumps({k: j[k] for k in ("access_token", "refresh_token", "expires_at") if k in j}))
    return True, "Spotify connected."


def listen_for_callback(done, timeout=180):
    """Catch http://127.0.0.1:8888/callback once (loopback only), then call done(ok, message)."""
    if _login["server"]:
        return
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if urllib.parse.urlsplit(self.path).path != "/callback":
                self.send_response(404); self.end_headers(); return
            if q.get("state", [""])[0] != _login["state"]:
                ok, msg = False, "That login did not match this request, so I ignored it."
            elif "error" in q:
                ok, msg = False, explain(q["error"][0])
            else:
                ok, msg = exchange(q.get("code", [""])[0])
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
            self.wfile.write(("<body style='font-family:sans-serif;background:#070b1a;color:#fff;padding:40px'><h2>%s</h2><p>You can close this window and go back to Jarvis.</p></body>" % ("Spotify connected" if ok else "Not connected")).encode())
            self.server.result = (ok, msg)

    def serve():
        try:
            srv = HTTPServer(("127.0.0.1", 8888), H)
        except OSError:
            done(False, "Port 8888 is busy, so I cannot catch Spotify's answer. Close whatever uses it and press login again.")
            return
        _login["server"] = srv
        srv.result = None
        srv.timeout = 1
        t0 = time.time()
        while srv.result is None and time.time() - t0 < timeout:
            srv.handle_request()
        res = srv.result or (False, "No answer from Spotify's page in 3 minutes. Press login to try again.")
        srv.server_close()
        _login["server"] = None
        done(*res)
    threading.Thread(target=serve, daemon=True).start()


def _access():
    t = _tokens()
    if not t:
        return None
    if time.time() < t.get("expires_at", 0):
        return t["access_token"]
    try:
        j = _post_token({"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": _read("spotify_client_id")})
    except Exception:
        return None
    t["access_token"] = j["access_token"]; t["expires_at"] = time.time() + int(j.get("expires_in", 3600)) - 60
    if j.get("refresh_token"):
        t["refresh_token"] = j["refresh_token"]
    _write600("spotify_tokens.json", json.dumps(t))
    return t["access_token"]


def _api(method, path, body=None):
    tok = _access()
    if not tok:
        return None, "not connected"
    req = urllib.request.Request(API + path, data=(json.dumps(body).encode() if body is not None else None), method=method,
                                 headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json", "User-Agent": "jarvis-local"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return (json.loads(raw) if raw else {}), None
    except urllib.error.HTTPError as e:
        try:
            msg = (json.load(e).get("error") or {})
            msg = (msg.get("reason") or msg.get("message") or "") if isinstance(msg, dict) else str(msg)
        except Exception:
            msg = ""
        return None, explain(msg, e.code)
    except Exception as e:
        return None, "Could not reach Spotify (" + type(e).__name__ + ")."


def find_device():
    """-> (device dict or None, message)."""
    j, err = _api("GET", "/me/player/devices")
    if err:
        return None, err
    for d in j.get("devices", []):
        if d.get("name") == DEVICE_NAME:
            return d, "Found the device called Jarvis."
    return None, ("No device called Jarvis yet. Spotify plays through a small player program on the Go (spotifyd or librespot) named Jarvis, and it is not running. "
                  "It is not installed by me without your OK. Once installed and named Jarvis, press this again. Until then music stays on YouTube.")


def play_test():
    d, msg = find_device()
    if not d:
        return False, msg
    _, err = _api("PUT", "/me/player/play?device_id=" + urllib.parse.quote(d["id"]), {"uris": [TEST_TRACK]})
    return (False, err) if err else (True, "Playing a test song on Jarvis.")


def parse_command(text):
    low = text.lower().strip(" .?!")
    if re.fullmatch(r"(?:please )?(?:set ?up|setup|connect|link|pair|configure) (?:my )?spotify(?: (?:account|premium))?", low) or re.fullmatch(r"spotify (?:setup|set up)", low):
        return {"kind": "setup"}
    return None


def guide_card():
    st = "Spotify is connected." if connected() else ("Client ID saved." if have_client_id() else "")
    return {"type": "guide", "gid": "spotify", "title": "Set up Spotify", "full": True, "paged": True, "status": st, "steps": [
        {"text": "Do you have Spotify Premium? Spotify only lets other apps play music for Premium accounts.",
         "buttons": [{"label": "YES, PREMIUM", "action": "spotify_premium", "payload": {"yes": True}}, {"label": "NO, USE YOUTUBE MUSIC", "action": "spotify_premium", "payload": {"yes": False}}]},
        {"text": "Go to developer.spotify.com/dashboard on any device, log in, and press Create app.", "hint": "You log in on Spotify's own page. Never type your Spotify password anywhere else."},
        {"text": "Name it Jarvis (any description). For the redirect address add exactly:", "cmd": REDIRECT, "hint": "Use the numbers 127.0.0.1, not the word localhost. Tick Web API. Save."},
        {"text": "Open the app's Settings and copy the Client ID. Paste it here:", "input": {"placeholder": "Client ID (32 letters and numbers)", "label": "SAVE", "action": "spotify_client_id"},
         "hint": "Do NOT paste the Client Secret: it is not needed. The ID is saved privately and never shown again."},
        {"text": "Press the button: Spotify's login page opens. Log in there and press Agree.", "buttons": [{"label": "OPEN SPOTIFY LOGIN", "action": "spotify_login"}]},
        {"text": "Last: the player. A device called Jarvis must appear in Spotify.", "buttons": [{"label": "CHECK FOR JARVIS", "action": "spotify_check"}, {"label": "PLAY TEST SONG", "action": "spotify_test"}]}]}
