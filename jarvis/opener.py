"""Opening things for the user: websites, searches, YouTube, maps, apps, and 'back to Jarvis'.

Safety rules:
  * The brain never produces shell text. It returns a STRUCTURED action ({"url": ...} or {"app": ...}); this module validates it
    against an allowlist / strict URL rules and builds the command itself.
  * A brain-originated action only runs if the user's OWN words contained an open intent (so text found on a web page cannot make
    the assistant open something).
  * Commands go through `hyprctl dispatch exec` with every argument shell-quoted; apps come from a small allowlist plus the
    installed .desktop entries (launched with gtk-launch, never by running their Exec line ourselves).
  * The Jarvis kiosk window is never closed, moved or un-fullscreened: new windows are opened on their own workspace and
    brought to the front; 'back to Jarvis' focuses the kiosk's workspace and window again."""
import difflib
import glob
import json
import os
import re
import shlex
import socket
import subprocess
import time
import urllib.parse
import urllib.request

DOCK_CLASS = "chrome-127.0.0.1__dock-Default"
KIOSK_CLASSES = ("jarvis-ui", "local.jarvis")
OWN_CLASSES = KIOSK_CLASSES + (DOCK_CLASS,)
BROWSER_CLASSES = ("chromium", "google-chrome", "firefox", "brave-browser", "zen", "vivaldi")

SITES = {
    "youtube": "https://www.youtube.com", "you tube": "https://www.youtube.com", "google": "https://www.google.com", "gmail": "https://mail.google.com",
    "google maps": "https://www.google.com/maps", "maps": "https://www.google.com/maps", "github": "https://github.com", "wikipedia": "https://www.wikipedia.org",
    "reddit": "https://www.reddit.com", "netflix": "https://www.netflix.com", "spotify": "https://open.spotify.com", "amazon": "https://www.amazon.com",
    "twitter": "https://x.com", "x": "https://x.com", "facebook": "https://www.facebook.com", "instagram": "https://www.instagram.com", "linkedin": "https://www.linkedin.com",
    "news": "https://news.google.com", "google news": "https://news.google.com", "drive": "https://drive.google.com", "google drive": "https://drive.google.com",
    "calendar": "https://calendar.google.com", "google calendar": "https://calendar.google.com", "chatgpt": "https://chatgpt.com", "claude": "https://claude.ai",
}
# name the user says -> (label, argv). Fixed commands only.
APPS = {
    "browser": ("the browser", ["omarchy-launch-browser"]), "web browser": ("the browser", ["omarchy-launch-browser"]),
    "chromium": ("Chromium", ["chromium", "--new-window"]), "chrome": ("Chromium", ["chromium", "--new-window"]),
    "terminal": ("the terminal", ["omarchy-launch-terminal"]), "command line": ("the terminal", ["omarchy-launch-terminal"]), "console": ("the terminal", ["omarchy-launch-terminal"]),
    "files": ("your files", ["omarchy-launch-nautilus"]), "my files": ("your files", ["omarchy-launch-nautilus"]), "file manager": ("your files", ["omarchy-launch-nautilus"]),
    "file explorer": ("your files", ["omarchy-launch-nautilus"]), "folders": ("your files", ["omarchy-launch-nautilus"]),
    "settings": ("settings", ["omarchy", "menu"]), "system settings": ("settings", ["omarchy", "menu"]), "menu": ("the menu", ["omarchy", "menu"]),
}
OPEN_INTENT = re.compile(r"\b(open|launch|start|go to|take me to|pull up|visit|bring up|play|show me|look up|search|navigate|back to|return to)\b", re.I)
PRETTY = {"youtube": "YouTube", "you tube": "YouTube", "github": "GitHub", "chatgpt": "ChatGPT", "linkedin": "LinkedIn", "gmail": "Gmail", "x": "X", "netflix": "Netflix"}
_URL_SAFE = ":/?#=&%+@,.-_~[]!*()$;"


# ----------------------------------------------------------------------------- hyprctl
_IPC = None


def _ipc(cmd):
    """Talk to Hyprland over its socket (about 1 ms) instead of spawning hyprctl (about 15 ms)."""
    global _IPC
    if _IPC is None:
        _IPC = f"{os.environ['XDG_RUNTIME_DIR']}/hypr/{os.environ['HYPRLAND_INSTANCE_SIGNATURE']}/.socket.sock"
    c = socket.socket(socket.AF_UNIX)
    try:
        c.settimeout(3)
        c.connect(_IPC)
        c.sendall(cmd.encode())
        out = b""
        while True:
            chunk = c.recv(65536)
            if not chunk:
                break
            out += chunk
        return out.decode(errors="ignore")
    finally:
        c.close()


def _hypr_json(*args):
    try:
        return json.loads(_ipc("j/" + args[0]))
    except Exception:
        try:
            return json.loads(subprocess.run(["hyprctl", *args, "-j"], capture_output=True, text=True, timeout=3).stdout)
        except Exception:
            return []


def _dispatch(lua):
    try:
        return _ipc("dispatch " + lua).strip() == "ok"
    except Exception:
        r = subprocess.run(["hyprctl", "dispatch", lua], capture_output=True, text=True, timeout=4)
        return r.stdout.strip().startswith("ok")


def clients():
    c = _hypr_json("clients")
    return c if isinstance(c, list) else []


def kiosk_client():
    return next((c for c in clients() if c.get("class") in KIOSK_CLASSES), None)


def focus_window(address):
    return _dispatch('hl.dsp.focus({ window = "address:%s" })' % address)


def focus_workspace(ws):
    return _dispatch('hl.dsp.focus({ workspace = "%s" })' % ws)


def hypr_exec(argv):
    """Launch through Hyprland (so the window is a normal child of the session). Every argument is shell-quoted."""
    cmd = " ".join(shlex.quote(a) for a in argv)
    if any(ch in cmd for ch in ('"', "\\", "\n", "\r", "\0")):
        raise ValueError("refusing a command with quote/escape characters")
    return _dispatch('hl.dsp.exec_cmd("%s")' % cmd)


# ----------------------------------------------------------------------------- validation
def clean_url(url):
    u = (url or "").strip()
    if not u or len(u) > 500 or any(ch in u for ch in " \t\r\n\"'`<>\\{}|^"):
        u = urllib.parse.quote(u, safe=_URL_SAFE) if u and len(u) <= 500 else ""
    p = urllib.parse.urlsplit(u)
    if p.scheme not in ("http", "https") or not p.hostname or "@" in (p.netloc or ""):
        return None
    if not re.fullmatch(r"[A-Za-z0-9.-]+", p.hostname) or "." not in p.hostname:
        return None
    return urllib.parse.urlunsplit((p.scheme, p.netloc, urllib.parse.quote(p.path, safe=_URL_SAFE), urllib.parse.quote(p.query, safe=_URL_SAFE), urllib.parse.quote(p.fragment, safe=_URL_SAFE)))


_desktop_cache = None


def desktop_apps():
    """name(lowercase) -> desktop id, for visible Application entries."""
    global _desktop_cache
    if _desktop_cache is not None:
        return _desktop_cache
    out = {}
    for d in ("/usr/share/applications", os.path.expanduser("~/.local/share/applications")):
        for f in glob.glob(d + "/*.desktop"):
            try:
                name, ok = None, True
                for line in open(f, encoding="utf-8", errors="ignore"):
                    if line.startswith("[") and not line.startswith("[Desktop Entry]"):
                        break
                    if line.startswith("Name=") and name is None:
                        name = line[5:].strip()
                    if line.strip() in ("NoDisplay=true", "Hidden=true") or line.startswith("Type=") and "Application" not in line:
                        ok = False
                if name and ok:
                    out[name.lower()] = os.path.basename(f)[:-8]
            except OSError:
                pass
    _desktop_cache = out
    return out


def find_desktop_app(spoken):
    s = re.sub(r"[^a-z0-9 ]+", " ", spoken.lower()).strip()
    if not s:
        return None
    apps = desktop_apps()
    if s in apps:
        return s, apps[s]
    cands = [(n, i) for n, i in apps.items() if n.startswith(s) or all(t in n.split() for t in s.split())]
    if len(cands) == 1:
        return cands[0]
    best = max(apps, key=lambda n: difflib.SequenceMatcher(None, s, n).ratio(), default=None)
    if best and difflib.SequenceMatcher(None, s, best).ratio() >= 0.86:
        return best, apps[best]
    return None


def validate(action, user_text=None):
    """-> a normalised action dict, or None. `user_text` given = the action came from the brain, so the user must have asked."""
    if not isinstance(action, dict):
        return None
    if user_text is not None and not OPEN_INTENT.search(user_text):
        return None
    if action.get("action") in ("focus_jarvis", "close_last", "to_orb", "toggle_orb"):
        return {"action": action["action"]}
    if action.get("action") == "focus_other":
        return {"action": "focus_other", "address": str(action.get("address", ""))[:20]} if user_text is None else None
    if action.get("action") == "win_cmd":
        return {"action": "win_cmd", "cmd": str(action.get("cmd")), "type": action.get("type")} if action.get("cmd") in ("min", "max", "smaller", "bigger", "restore", "close", "left", "right", "top", "bottom", "center", "ypause", "yplay", "duck", "unduck") else None
    if action.get("action") == "show_map":
        from . import maps
        sid = str(action.get("id", ""))
        return {"action": "show_map", "id": sid, "label": maps.SPECS[sid].get("title", "map")[:60]} if sid in maps.SPECS else None
    if action.get("action") == "fullscreen_embed":
        return {"action": "fullscreen_embed", "on": bool(action.get("on", True))}
    if action.get("action") == "open_map":
        q = re.sub(r"\s+", " ", str(action.get("query", ""))).strip()[:120]
        return {"action": "open_map", "query": q, "label": q} if q else None
    if "url" in action:
        u = clean_url(str(action["url"]))
        out = {"action": "open_url", "url": u, "label": str(action.get("label") or urllib.parse.urlsplit(u).hostname)[:60]} if u else None
        if out and action.get("page_url"):
            pu = clean_url(str(action["page_url"]))
            if pu:
                out["page_url"] = pu                           # what "send that to my Mac/phone" should send (e.g. the directions page for an embedded map)
        return out
    if "app" in action:
        name = re.sub(r"\s+", " ", str(action["app"]).lower().strip())[:40]
        if name in APPS:
            return {"action": "open_app", "app": name, "label": APPS[name][0]}
        hit = find_desktop_app(name)
        return {"action": "open_desktop", "id": hit[1], "label": hit[0].title()} if hit else None
    return None


# ----------------------------------------------------------------------------- execute
def _free_workspace(kiosk):
    used = {c["workspace"]["id"] for c in clients()}
    active = _hypr_json("activeworkspace")
    active_id = active.get("id") if isinstance(active, dict) else None
    if active_id is not None and active_id != kiosk["workspace"]["id"]:
        return active_id                                   # the user is already looking at another workspace
    return next(i for i in range(2, 10) if i not in used and i != kiosk["workspace"]["id"])


RECENT = []            # addresses of windows Jarvis opened (newest last): the only windows "close that" may close
NO_FULLSCREEN = ("menu", "settings", "system settings")


def _events_socket():
    sig, rt = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"), os.environ.get("XDG_RUNTIME_DIR")
    s = socket.socket(socket.AF_UNIX)
    s.connect(f"{rt}/hypr/{sig}/.socket2.sock")
    return s


def _wait_openwindow(sock, timeout):
    """Block until Hyprland reports a new window that is not one of ours -> (address, workspace id, class), or None."""
    end, buf = time.time() + timeout, b""
    while True:
        left = end - time.time()
        if left <= 0:
            return None
        sock.settimeout(left)
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            return None
        if not chunk:
            return None
        buf += chunk
        lines = buf.split(b"\n"); buf = lines.pop()
        for ln in lines:
            if ln.startswith(b"openwindow>>"):
                f = ln[len(b"openwindow>>"):].decode(errors="ignore").split(",", 3)
                if len(f) >= 3 and f[2] not in OWN_CLASSES:
                    return "0x" + f[0], f[1], f[2]


def _fullscreen_active():
    return _dispatch('hl.dsp.window.fullscreen({ mode = "fullscreen", action = "set" })')


PANEL = (0.06, 0.12, 0.88, 0.74)        # x, y, w, h as fractions of the screen: MUST match --px/--py/--pw/--ph in web/style.css
EMBED = {"kind": None, "addr": None, "full": False, "page_url": None}
NOTIFY = lambda msg: None                # set by the web server: sends {"t": "embed", ...} to the Jarvis page


def set_notifier(fn):
    global NOTIFY
    NOTIFY = fn


def monitor_size():
    m = _hypr_json("monitors")
    if isinstance(m, list) and m:
        mon = next((x for x in m if x.get("focused")), m[0])
        return int(mon["width"] / mon.get("scale", 1)), int(mon["height"] / mon.get("scale", 1))
    return 900, 600


def panel_rect(full=False):
    w, h = monitor_size()
    if full:
        return 0, 0, w, h
    return int(PANEL[0] * w), int(PANEL[1] * h), int(PANEL[2] * w), int(PANEL[3] * h)


def _win(addr):
    return 'window = "address:%s"' % addr


def place_window(addr, rect, pin=True):
    """Float it, pin it (so it stays above the fullscreen Jarvis kiosk) and put it exactly over `rect`. pin=False: an ordinary movable window (orb mode)."""
    x, y, w, h = rect
    _dispatch('hl.dsp.window.fullscreen({ mode = "fullscreen", action = "unset", %s })' % _win(addr))
    _dispatch('hl.dsp.window.float({ action = "on", %s })' % _win(addr))
    pinned = any(c["address"] == addr and c.get("pinned") for c in clients())
    if pinned != pin:
        _dispatch('hl.dsp.window.pin({ %s })' % _win(addr))
    _dispatch('hl.dsp.window.resize({ %s, x = %d, y = %d })' % (_win(addr), w, h))
    _dispatch('hl.dsp.window.move({ %s, x = %d, y = %d })' % (_win(addr), x, y))


def dock_to_top(later=0.0):
    """Kept for callers: the orb is an overlay-layer surface (jarvis/orb.py) and is always above every window, so there is nothing to raise."""
    return


YT_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be")
IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def embed_for(url):
    """Things that can live inside the Jarvis page (fast: no new window). -> {"kind", "src"} or None."""
    u = urllib.parse.urlsplit(url)
    host, path, q = (u.hostname or "").lower(), u.path, urllib.parse.parse_qs(u.query)
    if host in YT_HOSTS:
        vid = None
        if host.endswith("youtu.be"):
            vid = path.strip("/").split("/")[0]
        elif path == "/watch":
            vid = (q.get("v") or [None])[0]
        elif path.startswith(("/shorts/", "/embed/", "/live/")):
            vid = path.split("/")[2] if len(path.split("/")) > 2 else None
        if vid and re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
            return {"kind": "iframe", "src": f"https://www.youtube.com/embed/{vid}?autoplay=1&rel=0&playsinline=1&fs=0&enablejsapi=1"}
    if host == "www.openstreetmap.org" and path.startswith("/export/embed.html"):
        return {"kind": "iframe", "src": url}
    if path.lower().endswith(IMG_EXT) and u.scheme == "https":
        return {"kind": "image", "src": url}
    return None


_GEO = {}


def osm_embed(query):
    """Geocode with OpenStreetMap Nominatim and return an OSM embed URL (or None)."""
    key = query.strip().lower()
    if key in _GEO:
        return _GEO[key]
    try:
        req = urllib.request.Request("https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" + urllib.parse.quote(query), headers={"User-Agent": "jarvis-local-assistant/0.1"})
        with urllib.request.urlopen(req, timeout=3) as r:
            hit = json.load(r)
        if not hit:
            return None
        lat, lon = float(hit[0]["lat"]), float(hit[0]["lon"])
        s_, n_, w_, e_ = (float(v) for v in hit[0]["boundingbox"])
        pad = 0.003
        url = ("https://www.openstreetmap.org/export/embed.html?bbox=%f%%2C%f%%2C%f%%2C%f&layer=mapnik&marker=%f%%2C%f"
               % (w_ - pad, s_ - pad, e_ + pad, n_ + pad, lat, lon))
        _GEO[key] = url
        return url
    except Exception:
        return None


def clear_embed(close_window=True, keep_pages=False):
    """Remove whatever is in the frame: a real window over it, and (unless keep_pages) all the page windows (video, map, image) inside the Jarvis page.
    keep_pages=True is for opening something new: page windows stay, the new one is added next to them. The kiosk and the orb are never touched."""
    kind, addr = EMBED["kind"], EMBED["addr"]
    if kind in ("iframe", "image") and keep_pages:
        return kind
    EMBED.update(kind=None, addr=None, full=False)
    if kind == "window" and addr and close_window:
        if addr == POOL["addr"]:
            pool_park()                                    # the pooled window is kept warm, not closed
        else:
            _dispatch('hl.dsp.window.close({ %s })' % _win(addr))
    if kind == "window":
        NOTIFY({"t": "embed", "kind": "close", "frame": True})
    elif kind:
        NOTIFY({"t": "embed", "kind": "close", "all": True})
    return kind


def page_windows_changed(n):
    """The page reports how many windows it has. When the last one is gone the frame is empty: reset, and go back to where you were."""
    if n == 0:
        from . import music
        music.STATE["active"] = False                      # the video window is gone: the music is over
    if n == 0 and EMBED["kind"] in ("iframe", "image"):
        EMBED.update(kind=None, addr=None, full=False)
        if PREV["ws"] is not None:
            go_to_orb()
    elif n > 0 and EMBED["kind"] is None:
        EMBED["kind"] = "iframe"


def set_full(on):
    """'make it full screen': the content covers everything except the orb (the dock stays on top)."""
    if not EMBED["kind"]:                                  # nothing in the frame: "full screen" means the orb expands to the full Jarvis
        return go_to_kiosk() if on else go_to_orb()
    EMBED["full"] = bool(on)
    if EMBED["kind"] == "window":
        if not any(c["address"] == EMBED["addr"] for c in clients()):
            clear_embed(False)
            return {"ok": False, "message": "that window is gone"}
        place_window(EMBED["addr"], panel_rect(bool(on)))
        dock_to_top(0.4)
        dock_to_top()
    NOTIFY({"t": "embed", "kind": "full", "on": bool(on)})
    return {"ok": True, "message": "full screen" if on else "normal size"}


def watch_closed():
    """Background thread: if the user closes the real window by hand, clear the frame too."""
    import threading

    def run():
        while True:
            try:
                s = _events_socket()
                buf, last_raise, last_fs = b"", 0.0, 0.0
                while True:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                    lines = buf.split(b"\n"); buf = lines.pop()
                    for ln in lines:
                        if ln.startswith((b"openwindow", b"closewindow", b"fullscreen", b"movewindow", b"workspace")):
                            if time.time() - last_fs > 0.3:                          # a window landing next to the kiosk must never leave it tiled in a corner
                                last_fs = time.time()
                                threading.Timer(0.25, ensure_kiosk_fullscreen).start()
                        if EMBED["kind"] == "window" and ln.startswith((b"activewindow", b"openwindow", b"windowtitle", b"movewindow", b"focusedmon", b"workspace")):
                            if time.time() - last_raise > 0.12:                 # the orb must stay above a window that re-raises itself while it loads
                                last_raise = time.time(); dock_to_top()
                        if ln.startswith(b"closewindow>>"):
                            gone = "0x" + ln[13:].decode(errors="ignore").strip()
                            if gone == POOL["addr"]:
                                POOL["addr"] = None                         # closed by hand: ensure_pool() will make a new one next time
                            if EMBED["kind"] == "window" and EMBED["addr"] == gone:
                                clear_embed(False)
            except Exception:
                time.sleep(2)
    threading.Thread(target=run, daemon=True).start()


CDP_PORT = 9223
BLANK = f"http://127.0.0.1:{os.environ.get('JARVIS_PORT', '8765')}/static/blank.html"
POOL_WS = "special:jarvis-pool"
POOL = {"addr": None, "lock": None}
PROFILE = os.path.expanduser("~/.cache/jarvis-web")      # its own profile: the DevTools port exposes this browser only, never your everyday one


def _cdp_pages():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=1) as r:
            return [t for t in json.load(r) if t.get("type") == "page"]
    except Exception:
        return []


def cdp_navigate(url):
    """Point the pooled window at `url` through the DevTools protocol (no new window, no browser start). Returns True on success."""
    import asyncio
    import aiohttp
    pages = _cdp_pages()
    if not pages:
        return False

    async def go():
        async with aiohttp.ClientSession() as sess, sess.ws_connect(pages[0]["webSocketDebuggerUrl"], timeout=3) as ws:
            await ws.send_json({"id": 1, "method": "Page.navigate", "params": {"url": url}})
            while True:
                m = await ws.receive_json(timeout=4)
                if m.get("id") == 1:
                    return "errorText" not in m.get("result", {}) and "error" not in m
    try:
        return asyncio.run(go())
    except Exception:
        return False


def pool_alive():
    return bool(POOL["addr"]) and any(c["address"] == POOL["addr"] for c in clients()) and bool(_cdp_pages())


def ensure_pool():
    """One Chromium (own profile, DevTools on localhost) with one blank app window parked on a hidden workspace, so opening a page is a navigate +
    a move into place (about 0.3 s) instead of starting a browser. Idempotent; never raises."""
    import threading
    if POOL["lock"] is None:
        POOL["lock"] = threading.Lock()
    if not POOL["lock"].acquire(blocking=False):
        return
    try:
        if pool_alive():
            return
        mine = [c for c in clients() if str(c.get("class", "")).startswith("chrome-127.0.0.1__static")]     # a pooled window that outlived a server restart
        if mine and _cdp_pages():
            keep = next((c for c in mine if str(c["workspace"]["name"]).startswith("special")), mine[0])
            for extra in mine:
                if extra["address"] != keep["address"]:
                    _dispatch('hl.dsp.window.close({ %s })' % _win(extra["address"]))              # never more than one
            POOL["addr"] = keep["address"]
            pool_park()
            ensure_kiosk_fullscreen()
            return
        sock = _events_socket()
        try:
            os.makedirs(PROFILE, exist_ok=True)
            subprocess.Popen(["chromium", f"--user-data-dir={PROFILE}", f"--remote-debugging-port={CDP_PORT}", "--remote-debugging-address=127.0.0.1",
                              "--ozone-platform=wayland", f"--app=http://127.0.0.1:{os.environ.get('JARVIS_PORT', '8765')}/static/blank.html", "--no-first-run", "--noerrdialogs", "--password-store=basic",
                              "--autoplay-policy=no-user-gesture-required", "--disable-features=Translate", "--disable-background-networking", "--disable-sync"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            win = _wait_openwindow(sock, 20.0)
        finally:
            sock.close()
        if win:
            POOL["addr"] = win[0]
            pool_park()
            ensure_kiosk_fullscreen()
    except Exception as e:
        print("[pool] failed:", type(e).__name__, flush=True)
    finally:
        POOL["lock"].release()


def ensure_kiosk_fullscreen():
    """A window that lands on the kiosk's workspace ends its fullscreen (and tiles it into a quarter of the screen). It must always be full screen, so put it
    back, quietly: addressed by window, no focus change (idempotent)."""
    k = kiosk_client()
    if k and k.get("fullscreen", 0) == 0:
        _dispatch('hl.dsp.window.fullscreen({ mode = "fullscreen", action = "set", %s })' % _win(k["address"]))


PREV = {"ws": None}


def _active_ws():
    a = _hypr_json("activeworkspace")
    return a.get("id") if isinstance(a, dict) else None


def go_to_kiosk():
    """Orb -> full screen: switch to Jarvis's own workspace, remembering where you were."""
    k = kiosk_client()
    if not k:
        return {"ok": False, "message": "the Jarvis window is not open"}
    here = _active_ws()
    if here is not None and here != k["workspace"]["id"]:
        PREV["ws"] = here
        try:                                                    # bin/jarvis-escape (SUPER+ESCAPE) reads this to put you back where you were, with no server involved
            open(os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "jarvis.prevws"), "w").write(str(here))
        except OSError:
            pass
    ensure_kiosk_fullscreen()
    focus_workspace(k["workspace"]["id"])
    return {"ok": True, "message": "full screen"}


def go_to_orb():
    """Full screen -> orb: back to the workspace you were on (or the first one)."""
    k = kiosk_client()
    here = _active_ws()
    if k and here == k["workspace"]["id"]:
        target = PREV["ws"] if PREV["ws"] not in (None, k["workspace"]["id"]) else 1
        PREV["ws"] = None
        focus_workspace(target)
        return {"ok": True, "message": "orb"}
    return {"ok": True, "message": "already the orb"}


def toggle_orb():
    k = kiosk_client()
    return go_to_orb() if k and _active_ws() == k["workspace"]["id"] else go_to_kiosk()


def pool_park():
    """Blank the pooled window and send it back to the hidden workspace."""
    addr = POOL["addr"]
    if not addr:
        return
    cdp_navigate(BLANK)
    import threading
    if _RECYCLE["timer"]:
        _RECYCLE["timer"].cancel()
    _RECYCLE["timer"] = threading.Timer(150, _recycle_pool)
    _RECYCLE["timer"].daemon = True
    _RECYCLE["timer"].start()
    if any(c["address"] == addr and c.get("pinned") for c in clients()):
        _dispatch('hl.dsp.window.pin({ %s })' % _win(addr))
    _dispatch('hl.dsp.window.move({ workspace = "%s", %s, follow = false })' % (POOL_WS, _win(addr)))


_RECYCLE = {"timer": None}


def _recycle_pool():
    """The parked pooled browser keeps hundreds of MB from the last page (a video held 1 GB). Parked and idle for 2.5 minutes: replace it with a fresh small one (in the background)."""
    import threading
    if EMBED.get("kind") == "window" or not POOL["addr"]:
        return
    subprocess.run(["pkill", "-f", "chromium.*--remote-debugging-port=922[3]"], capture_output=True)
    POOL["addr"] = None
    time.sleep(1.5)
    threading.Thread(target=ensure_pool, daemon=True).start()
    print("[pool] recycled the parked browser to free memory", flush=True)


def pool_show(url, rect, ws_id, pin=True):
    """Navigate the pooled window to `url`, bring it to workspace `ws_id`, float + pin + place it over `rect`."""
    addr = POOL["addr"]
    if _RECYCLE["timer"]:
        _RECYCLE["timer"].cancel()                              # it is in use again
    if not cdp_navigate(url):
        return None
    _dispatch('hl.dsp.window.move({ workspace = "%d", %s, follow = false })' % (ws_id, _win(addr)))
    place_window(addr, rect, pin)
    return addr


# ----------------------------------------------------------------------------- orb mode: nothing may expand the orb by itself
PORT = os.environ.get("JARVIS_PORT", "8765")
POPUP = {"cards": [], "last": 0.0, "timer": None}


def orb_mode(kiosk=None):
    """True while you are on your own desktop with the small orb (the Jarvis kiosk exists but is not the workspace in front) AND "stay out" is on (default):
    Jarvis then never takes the screen back by itself (no focus stealing on an answer, a wake word or a timer); it speaks and shows the orb or a popup instead."""
    from . import prefs
    k = kiosk or kiosk_client()
    return bool(k) and _active_ws() != k["workspace"]["id"] and bool(prefs.get("stay_out"))


# ----------------------------------------------------------------------------- window switcher (inside the Jarvis full screen) and "switch to the terminal"
ALIASES = {"terminal": ("foot", "alacritty", "kitty", "wezterm", "ghostty", "terminal", "konsole"), "browser": ("chromium", "firefox", "chrome", "brave", "zen", "browser"),
           "files": ("nautilus", "thunar", "dolphin", "pcmanfm", "files", "yazi"), "claude": ("claude",), "editor": ("code", "nvim", "vim", "emacs", "zed", "editor")}


def other_windows():
    """Your windows, not Jarvis's own (the kiosk, the pooled browser window, the card popup): [{address, class, title, ws}]."""
    own = {POOL.get("addr"), POPUP.get("addr")}
    out = []
    for c in clients():
        if c.get("class") == "jarvis-ui" or c["address"] in own or c["workspace"]["id"] < 0 or not c.get("mapped", True) or c.get("hidden"):
            continue
        out.append({"address": c["address"], "class": c.get("class", ""), "title": (c.get("title") or c.get("class") or "")[:70], "ws": c["workspace"]["id"]})
    return out


def focus_other(address):
    """Bring one of YOUR windows to the front (only an address from other_windows() is accepted). Jarvis stays open behind it."""
    if address not in {w["address"] for w in other_windows()}:
        return {"ok": False, "message": "no such window"}
    return {"ok": focus_window(address), "message": "switched"}


PLACED = {}


def place_other(address):
    """Put one of YOUR windows on top of the Jarvis frame (floating, pinned, exactly over the panel) with Jarvis behind it as the background layer; a Jarvis-style frame is drawn around it by
    the page. Only a window from other_windows(). Nothing is captured or streamed: it is the real window. Undo with release_other()."""
    w = next((x for x in other_windows() if x["address"] == address), None)
    k = kiosk_client()
    if not w or not k:
        return {"ok": False, "message": "no such window"}
    cur = next((c for c in clients() if c["address"] == address), {})
    PLACED[address] = {"ws": cur.get("workspace", {}).get("id", 1), "floating": bool(cur.get("floating"))}
    _dispatch('hl.dsp.window.move({ workspace = "%d", %s, follow = false })' % (k["workspace"]["id"], _win(address)))
    place_window(address, panel_rect(), pin=True)
    ensure_kiosk_fullscreen()
    focus_window(address)
    NOTIFY({"t": "embed", "kind": "window", "title": w["title"][:30]})
    return {"ok": True, "message": "placed"}


def release_other(address):
    """Give a placed window back: unpin, back to where it was (tiled again if it was), and the frame goes away."""
    st = PLACED.pop(address, None)
    if st is None or address not in {c["address"] for c in clients()}:
        return {"ok": False, "message": "not placed"}
    cur = next((c for c in clients() if c["address"] == address), {})
    if cur.get("pinned"):
        _dispatch('hl.dsp.window.pin({ %s })' % _win(address))
    if not st["floating"]:
        _dispatch('hl.dsp.window.float({ action = "off", %s })' % _win(address))
    _dispatch('hl.dsp.window.move({ workspace = "%d", %s, follow = false })' % (st["ws"], _win(address)))
    ensure_kiosk_fullscreen()
    if not PLACED:
        NOTIFY({"t": "embed", "kind": "close"})
    return {"ok": True, "message": "released"}


def find_window(word):
    """'terminal' / 'claude' / 'firefox': the most relevant open window of yours, or None."""
    w = word.lower().strip()
    keys = ALIASES.get(w, ()) + (w,)
    for c in other_windows():
        hay = (c["class"] + " " + c["title"]).lower()
        if any(k in hay for k in keys):
            return c
    return None


def open_path(path):
    """FILES: open a file or folder in its default app, on another workspace so the full-screen Jarvis is never disturbed. Path must be inside your home folder."""
    from . import terminal
    real = terminal.under_home(path)
    if not real or not os.path.exists(real):
        return {"ok": False, "message": "not found"}
    k = kiosk_client()
    if k and _active_ws() == k["workspace"]["id"]:
        focus_workspace(_free_workspace(k))
    return {"ok": hypr_exec(["xdg-open", real]), "message": "opened"}


def window_command(text):
    """"switch to the terminal", "show Claude", "go to my browser": only when a window of that name is open (otherwise None, so "show me a map" is never hijacked)."""
    m = re.fullmatch(r"(?:please )?(?:switch to|go to|show|focus|bring up|open up|jump to) (?:the |my |a )?([a-z0-9 .+-]{2,24})(?: window)?", text.lower().strip(" .?!"))
    if not m:
        return None
    w = find_window(m.group(1).strip())
    if not w:
        return None
    return {"speech": "Switching.", "open": {"action": "focus_other", "address": w["address"]}}


def _orb_centre():
    try:
        d = json.load(open(os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "jarvis", "orb.json")))
        return int(d["cx"]), int(d["cy"])
    except Exception:
        w, h = monitor_size()
        return w - 70, h - 70


def _beside_orb(w, h):
    """A rect of size w x h next to the orb, on the side facing the screen centre, clamped on screen."""
    sw, sh = monitor_size()
    cx, cy = _orb_centre()
    x = cx - 60 - w if cx > sw / 2 else cx + 60
    y = min(max(cy - h // 2, 8), sh - h - 8)
    return int(min(max(x, 8), sw - w - 8)), int(y), w, h


def show_in_window(url, label="", page_url=None):
    """Orb mode: a big thing (video, map, page) opens in an ordinary movable window on THIS workspace. The kiosk is never touched, the orb stays where it is,
    and closing the window leaves you exactly where you were."""
    if not pool_alive():
        ensure_pool()
    sw, sh = monitor_size()
    w, h = int(sw * .62), int(sh * .66)
    clear_embed()
    addr = pool_show(url, ((sw - w) // 2, (sh - h) // 2, w, h), _active_ws() or 1, pin=False)
    if not addr:
        return {"ok": False, "message": "could not open a window"}
    focus_window(addr)
    EMBED.update(kind="window", addr=addr, full=False, page_url=page_url or url)
    return {"ok": True, "message": "opened in a window", "address": addr}


def popup_card(card):
    """Orb mode: a card shows as a compact popup beside the orb (never the full Jarvis window). -> True if handled."""
    from . import prefs
    k = kiosk_client()
    if k and _active_ws() != k["workspace"]["id"] and not prefs.get("stay_out"):
        go_to_kiosk()                                          # "stay out" is off: Jarvis may come to the front for a card
        return False
    if not orb_mode():
        return False
    POPUP["cards"] = (POPUP["cards"] + [card])[-4:]
    POPUP["last"] = time.time()
    POPUP["big"] = card.get("type") == "guide"                 # a step-by-step card needs room and time
    ensure_popup()
    return True


def ensure_popup():
    import threading
    cur = next((c for c in clients() if c["address"] == POPUP.get("addr")), None)       # (an --app window's class is derived from its URL, so it is tracked by address)
    if not cur:
        sock = None
        try:
            sock = _events_socket()
        except OSError:
            pass
        hypr_exec(["chromium", "--app=http://127.0.0.1:%s/?popup=1" % PORT, "--class=jarvis-popup", "--user-data-dir=" + os.path.expanduser("~/.cache/jarvis-popup"),
                   "--ozone-platform=wayland", "--no-first-run", "--disable-background-networking", "--disable-sync", "--password-store=basic"])
        win = _wait_openwindow(sock, 8.0) if sock else None
        if sock:
            sock.close()
        if not win:
            return
        addr = win[0]
        POPUP["addr"] = addr
        threading.Thread(target=_close_popup_strays, args=(addr,), daemon=True).start()
    else:
        addr = cur["address"]
    sw, sh = monitor_size()
    place_window(addr, _beside_orb(int(sw * .62), int(sh * .8)) if POPUP.get("big") else _beside_orb(340, 340), pin=False)
    if POPUP["timer"]:
        POPUP["timer"].cancel()
    POPUP["timer"] = threading.Timer(600 if POPUP.get("big") else 40, close_popup)
    POPUP["timer"].daemon = True
    POPUP["timer"].start()


def _close_popup_strays(addr):
    """Chromium may open a SECOND window next to the popup (a restored app window): it opens tiled and made your full-screen terminal shrink to half and stay that way. Close any other
    window of the popup's own process, a few times in the first 3 seconds (the glitch hunt found this with logs/windows.log)."""
    for _ in range(6):
        time.sleep(0.5)
        cs = clients()
        me = next((c for c in cs if c["address"] == addr), None)
        if not me:
            return
        for c in cs:
            if c.get("pid") == me.get("pid") and c["address"] != addr:
                _dispatch('hl.dsp.window.close({ %s })' % _win(c["address"]))
                print("[popup] closed a stray window of the popup browser", flush=True)


def close_popup():
    POPUP["cards"] = []
    if POPUP.get("addr") and any(c["address"] == POPUP["addr"] for c in clients()):
        _dispatch('hl.dsp.window.close({ %s })' % _win(POPUP["addr"]))
    POPUP["addr"] = None


def ensure_dock():
    """The orb (bin/jarvis-orb) must exist while something else is in front. The script allows one instance only."""
    subprocess.Popen([os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "jarvis-orb")],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def close_last():
    """'close that': close what is in the frame; otherwise the newest window Jarvis opened (never the kiosk, the dock or your own windows)."""
    if EMBED["kind"] in ("iframe", "image"):               # a page window: close the front one (the page tells us when none are left)
        NOTIFY({"t": "embed", "kind": "close"})
        return {"ok": True, "message": "closed"}
    if clear_embed():
        if PREV["ws"] is not None:                         # it was opened from another workspace: go back there
            go_to_orb()
        return {"ok": True, "message": "closed"}
    live = {c["address"] for c in clients()}
    while RECENT and RECENT[-1] not in live:
        RECENT.pop()
    if not RECENT:
        return {"ok": False, "message": "nothing of mine is open"}
    addr = RECENT.pop()
    ok = _dispatch('hl.dsp.window.close({ %s })' % _win(addr))
    return {"ok": ok, "message": "closed"}


def execute(action, timing=None):
    """Do a validated action. Returns {"ok": bool, "message": str}. `timing(name)` is called at 'dispatched' and 'window'."""
    mark = timing or (lambda name: None)
    kiosk = kiosk_client()
    kind = action["action"]
    if action.get("url"):
        EMBED["page_url"] = action.get("page_url") or action["url"]                  # what "that video" / "this page" / "send that to my Mac" refer to
    if kind == "focus_jarvis" and EMBED["kind"] and EMBED["full"]:         # "go back" / "back to Jarvis" while a video is full screen: leave full screen (the video keeps playing)
        return set_full(False)
    if kind == "focus_jarvis":
        clear_embed()
        ensure_kiosk_fullscreen()
        if not kiosk:
            return {"ok": False, "message": "the Jarvis window is not open"}
        r = go_to_kiosk()
        focus_window(kiosk["address"])
        return {"ok": r["ok"], "message": "back to Jarvis"}
    if kind == "focus_other":
        return focus_other(action["address"])
    if kind == "to_orb":
        clear_embed()
        return go_to_orb()
    if kind == "toggle_orb":
        return toggle_orb()
    if kind == "close_last":
        return close_last()
    if kind == "win_cmd":
        NOTIFY({"t": "embed", "kind": "win", "cmd": action["cmd"], "type": action.get("type")})
        return {"ok": True, "message": action["cmd"]}
    if kind == "fullscreen_embed":
        return set_full(action.get("on", True))
    orb = kind in ("show_map", "open_map", "open_url") and orb_mode(kiosk)     # orb form: never expand to full screen, open a normal window instead
    if orb and kind == "show_map":
        from . import maps
        spec = maps.SPECS.get(action["id"])
        if not spec:
            return {"ok": False, "message": "no map to show"}
        r = show_in_window("http://127.0.0.1:%s/map?id=%s" % (PORT, spec["id"]), spec.get("title", "map"), spec.get("gmaps"))
        mark("dispatched"); mark("window")
        return r
    if orb and kind == "open_map":
        url = osm_embed(action["query"]) or "https://www.openstreetmap.org/search?query=" + urllib.parse.quote(action["query"])
        r = show_in_window(url, action.get("label", "map"))
        mark("dispatched"); mark("window")
        return r
    if orb and kind == "open_url":
        r = show_in_window(action["url"], action.get("label", ""))
        mark("dispatched"); mark("window")
        return r
    if kind == "show_map":                                 # the Jarvis map page (Leaflet, route lines, places) inside the frame
        from . import maps
        spec = maps.SPECS.get(action["id"])
        if not spec or not kiosk:
            return {"ok": False, "message": "no map to show"}
        clear_embed(keep_pages=True)
        EMBED.update(kind="iframe", addr=None, full=False, page_url=spec.get("gmaps"))
        NOTIFY({"t": "embed", "kind": "iframe", "src": "/map?id=" + spec["id"], "title": spec.get("title", "map")})
        go_to_kiosk()
        mark("dispatched"); mark("window")
        return {"ok": True, "message": "map shown"}
    if kind == "open_map":
        url = osm_embed(action["query"])
        if url and kiosk:
            clear_embed(keep_pages=True)
            EMBED.update(kind="iframe", addr=None, full=False)
            NOTIFY({"t": "embed", "kind": "iframe", "src": url, "title": action.get("label", "map")})
            go_to_kiosk()                                  # the frame is in the Jarvis page: be on its workspace to see it (closing takes you back)
            mark("dispatched"); mark("window")
            return {"ok": True, "message": "map shown"}
        action = {"action": "open_url", "url": "https://www.openstreetmap.org/search?query=" + urllib.parse.quote(action["query"]), "label": "map"}
        kind = "open_url"
    if kind == "open_url":
        emb = embed_for(action["url"]) if kiosk else None
        if emb:                                            # inside the Jarvis frame: no window, no browser start
            clear_embed(keep_pages=True)
            EMBED.update(kind=emb["kind"], addr=None, full=False)
            NOTIFY({"t": "embed", "kind": emb["kind"], "src": emb["src"], "title": action.get("label", "")})
            go_to_kiosk()
            mark("dispatched"); mark("window")
            return {"ok": True, "message": "shown in the frame"}
        if kiosk and not pool_alive():                     # the pooled browser died: bring it back in the background (this open uses the slower path)
            import threading
            threading.Thread(target=ensure_pool, daemon=True).start()
        if kiosk and pool_alive():                         # fastest: the warm pooled window navigates to the page and moves into place
            clear_embed()
            addr = pool_show(action["url"], panel_rect(), _free_workspace(kiosk))   # NOT the kiosk's workspace: a window arriving there would end its fullscreen
            if addr:
                mark("dispatched")
                go_to_kiosk()                              # remembers where you were, so closing it takes you back
                focus_window(addr)
                EMBED.update(kind="window", addr=addr, full=False)
                NOTIFY({"t": "embed", "kind": "window", "title": action.get("label", "")})
                mark("window")
                return {"ok": True, "message": "opened (pooled)", "address": addr}
        argv = ["chromium", "--app=" + action["url"]]       # an app window: no tabs or address bar, so it can sit exactly over the frame
    elif kind == "open_app":
        argv = list(APPS[action["app"]][1])
    elif kind == "open_desktop":
        argv = ["gtk-launch", action["id"]]
    else:
        return {"ok": False, "message": "unknown action"}
    place = kiosk is not None and not (kind == "open_app" and action["app"] in NO_FULLSCREEN)
    ensure_dock()
    clear_embed()
    try:
        sock = _events_socket()
    except OSError:
        sock = None
    try:
        if kiosk:                                          # the new window starts on another workspace, so the fullscreen kiosk is never disturbed
            focus_workspace(_free_workspace(kiosk))
        if not hypr_exec(argv):
            return {"ok": False, "message": "could not launch " + argv[0]}
        mark("dispatched")
        win = _wait_openwindow(sock, 6.0) if sock else None
    finally:
        if sock:
            sock.close()
    if win is None:
        return {"ok": True, "message": "launched, but no window appeared"}
    addr = win[0]
    if place:                                              # inside the frame: floating, pinned above the kiosk, exactly over the panel
        place_window(addr, panel_rect())
        go_to_kiosk()
        focus_window(addr)
        dock_to_top(0.5)
        dock_to_top()
        EMBED.update(kind="window", addr=addr, full=False)
        NOTIFY({"t": "embed", "kind": "window", "title": action.get("label", "")})
    else:
        focus_window(addr)
    mark("window")
    RECENT.append(addr); del RECENT[:-8]
    return {"ok": True, "message": "opened", "address": addr, "workspace": win[1]}


# ----------------------------------------------------------------------------- what the user says
def _site_url(t):
    t = re.sub(r"\s+dot\s+", ".", t.strip().lower())
    t = re.sub(r"^(?:the |a )", "", t).strip(" .?!")
    if t in SITES:
        return t, SITES[t]
    t2 = t.replace(" ", "")
    if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", t2) and not t2.endswith(("tion", "ing")):
        return t2, "https://" + t2
    return None


def ack_phrases():
    """Everything the fast path can say, so it can be pre-synthesised at startup and played with no delay."""
    out = ["Closing it.", "Full screen.", "Normal size.", "Back to Jarvis.", "Back to Rose.", "Opening maps."]
    for name in SITES:
        out.append(f"Opening {PRETTY.get(name, name.title())}.")
    for label in {v[0] for v in APPS.values()}:
        out.append(f"Opening {label}.")
    return sorted(set(out))


def parse_command(text):
    """Voice command -> {"speech": ..., "open": action} or None. Pure matching: no model involved."""
    t = text.strip()
    low = t.lower().strip(" .?!")
    if EMBED["kind"] in ("iframe", "image"):                                       # window commands apply while something is open inside Jarvis
        wc = None
        if re.fullmatch(r"(?:please )?(?:minimi[sz]e)(?: it| the (?:video|map|image|window))?", low): wc = ("min", None, "Minimised.")
        elif re.fullmatch(r"(?:please )?(?:maximi[sz]e)(?: it| the (?:video|map|image|window))?", low): wc = ("max", None, "Maximised.")
        elif re.fullmatch(r"(?:please )?(?:make it |make the (?:video|map|image|window) )(?:smaller|shrink)|(?:please )?shrink (?:it|the (?:video|map|image|window))", low): wc = ("smaller", None, "Smaller.")
        elif re.fullmatch(r"(?:please )?(?:make it |make the (?:video|map|image|window) )(?:bigger|larger)|(?:please )?enlarge it", low): wc = ("bigger", None, "Bigger.")
        elif re.fullmatch(r"(?:please )?(?:restore|unminimi[sz]e)(?: it)?|(?:please )?bring (?:it|the video) back", low): wc = ("restore", None, "Restored.")
        else:
            mv = re.fullmatch(r"(?:please )?(?:move|put|slide) (?:it|the (?:video|map|image|window)) (?:to the |to |on the |at the )?(left|right|top|bottom|centre|center|middle)(?: please)?", low)
            if mv: wc = ({"centre": "center", "middle": "center"}.get(mv.group(1), mv.group(1)), None, "Moved.")
            cl = re.fullmatch(r"(?:please )?(?:close|shut) the (video|map|image|page)", low)
            if cl: wc = ("close", {"page": "page"}.get(cl.group(1), cl.group(1)), "Closing the " + cl.group(1) + ".")
        if wc:
            return {"speech": wc[2], "open": {"action": "win_cmd", "cmd": wc[0], "type": wc[1]}}
    if re.fullmatch(r"(?:please )?(?:make it |go |switch to |show it |put it )?(?:full ?screen|bigger|maximi[sz]e(?: it)?)(?: please)?", low):
        return {"speech": "Full screen.", "open": {"action": "fullscreen_embed", "on": True}}
    if re.fullmatch(r"(?:please )?(?:exit|leave|stop|end|back from|go out of|get out of)(?: the)? full ?screen|(?:make it |go )?(?:smaller|normal size|back to normal)", low):
        return {"speech": "Normal size.", "open": {"action": "fullscreen_embed", "on": False}}
    if re.fullmatch(r"(?:please )?(?:minimi[sz]e|go to (?:the )?(?:corner|orb)|shrink|go small|to the corner|go to orb)(?: please)?", low):
        return {"speech": "Going to the corner.", "open": {"action": "to_orb"}}
    if re.fullmatch(r"(?:please )?(?:come back|show yourself|show your ?self|expand|come out|come here|go full screen)(?: (?:jarvis|rose))?(?: please)?", low):
        return {"speech": "Here I am.", "open": {"action": "focus_jarvis"}}
    if re.fullmatch(r"(?:please )?(?:close|shut) (?:that|this|it|that window|this window|that up)(?: please)?", low):
        return {"speech": "Closing it.", "open": {"action": "close_last"}}
    m = re.search(r"\b(?:back to|return to|go back to|switch back to|bring up|show me)\s+(jarvis|rose)\b", low)
    if m or low in ("go back", "back"):
        return {"speech": "Back to " + (m.group(1).capitalize() if m else "Jarvis") + ".", "open": {"action": "focus_jarvis"}}
    m = re.search(r"\bplay\s+(.+?)\s+on\s+(?:you ?tube|youtube)\b", low)
    if m:
        q = t[m.start(1):m.end(1)].strip()
        return {"speech": f"Playing {q} on YouTube.", "open": {"action": "open_url", "url": "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(q), "label": "YouTube"}}
    m = re.search(r"\bopen\s+(?:up\s+)?(?:google\s+)?maps?(?:\s+(?:for|to|of|at|near))?\s*(.*)$", low)
    if m:
        place = t[m.start(1):m.end(1)].strip(" .?!")
        if place:
            return {"speech": f"Opening maps for {place}.", "open": {"action": "open_map", "query": place, "label": place}}
        return {"speech": "Opening maps.", "open": {"action": "open_url", "url": SITES["maps"], "label": "Maps"}}
    m = re.search(r"\b(?:open|launch|start|go to|take me to|pull up|visit|bring up)\s+(.+)$", low)
    if not m:
        return None
    target = re.sub(r"^(?:up |the |my |a )+", "", m.group(1).strip())
    target_orig = re.sub(r"^(?:up |the |my |a )+", "", t[m.start(1):m.end(1)].strip(" .?!"), flags=re.I)
    verb_goto = bool(re.search(r"\b(?:go to|take me to|visit)\b", low))
    if target in APPS or ("my " + target) in APPS:
        key = target if target in APPS else "my " + target
        return {"speech": f"Opening {APPS[key][0]}.", "open": {"action": "open_app", "app": key, "label": APPS[key][0]}}
    site = _site_url(target)
    if site:
        name, url = site
        return {"speech": f"Opening {PRETTY.get(name, name.title()) if '.' not in name else name}.", "open": {"action": "open_url", "url": url, "label": name}}
    if verb_goto:                                          # "go to <something we do not know>": search for it
        return {"speech": f"Searching for {target_orig}.", "open": {"action": "open_url", "url": "https://www.google.com/search?q=" + urllib.parse.quote_plus(target), "label": "search"}}
    hit = find_desktop_app(target)
    if hit:
        return {"speech": f"Opening {hit[0].title()}.", "open": {"action": "open_desktop", "id": hit[1], "label": hit[0].title()}}
    return None                                            # unknown: the brain may know a URL for it (and is validated if it answers)
