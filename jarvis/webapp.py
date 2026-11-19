"""Jarvis web UI backend: serves the page, talks to it over a websocket, drives the Assistant.
Same control socket as the GTK app, so the SUPER+J / SUPER+SHIFT+J keys keep working.
Run: .venv/bin/python -m jarvis.webapp   (bin/jarvis-ui also opens Chromium in kiosk mode)"""
import asyncio
import json
import math
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from aiohttp import WSMsgType, web

from . import dictate, maps, mac, opener, phone, phonecam, prefs, terminal, actions, config, settings, skills, tts
from .core import Assistant

PORT = int(os.environ.get("JARVIS_PORT", "8765"))
WEB = Path(__file__).resolve().parent / "web"
HOME = Path.home().resolve()
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
UI_STATE = {"idle": "idle", "recording": "listening", "transcribing": "thinking", "thinking": "thinking",
            "speaking": "speaking", "loading": "loading", "error": "error"}


def safe_image_path(p):
    """Local image the page may display: under $HOME, an image type, no hidden dirs (.ssh, .config, ...)."""
    try:
        path = Path(os.path.expanduser(p)).resolve()
        rel = path.relative_to(HOME)
    except (ValueError, OSError):
        return None
    if path.suffix.lower() not in IMG_EXT or not path.is_file() or any(part.startswith(".") for part in rel.parts):
        return None
    return path


class Server:
    def __init__(self):
        self.clients, self.history, self.geo_cache = set(), [], {}
        self.ui_beat = 0.0                                   # last heartbeat from the kiosk page (the watchdog restarts a page that goes silent)
        self.loop = None
        self.muted = False
        self.assistant = None

    # --- thread-safe push to every connected page -----------------------------
    def bcast(self, obj):
        if self.loop:
            data = json.dumps(obj)
            async def safe(c):
                try:
                    await c.send_str(data)
                except Exception:                                # a client that went away (reload, orb restart): drop it quietly, no traceback spam
                    self.clients.discard(c)
            self.loop.call_soon_threadsafe(lambda: [asyncio.ensure_future(safe(c)) for c in list(self.clients)])

    def on_state(self, state, detail=""):
        self.bcast({"t": "state", "state": UI_STATE.get(state, state), "detail": detail})

    def on_msg(self, role, text):
        self.history = (self.history + [{"role": role, "text": text}])[-40:]
        self.bcast({"t": "msg", "role": role, "text": text})

    def on_level(self, kind, v, db):
        self.bcast({"t": "level", "src": kind, "v": round(v, 3), "db": round(db, 1)})

    def on_ui(self, hidden):
        self.bcast({"t": "ui", "hidden": hidden})

    def on_muted(self, on):
        self.bcast({"t": "muted", "on": on})

    def on_status(self, lines, active):
        self.bcast({"t": "status", "lines": lines, "active": active})

    def on_listen(self, on):
        self.bcast({"t": "listen", "on": on})

    def on_persona(self, name):
        self.bcast({"t": "persona", "v": name})

    def on_card(self, card):
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._send_card(card), self.loop)

    async def _send_card(self, card):
        try:
            card = await self.loop.run_in_executor(None, self._prepare, card)
        except Exception as e:
            card = {"type": "text", "title": "card error", "text": str(e)[:200]}
        if card:
            await self.loop.run_in_executor(None, opener.popup_card, card)       # orb form: also a compact popup beside the orb (the full window is never brought up for a card)
            self.bcast({"t": "card", "card": card})

    def _prepare(self, card):
        t = card["type"]
        if t == "qrcard":
            return {"type": "image", "title": card.get("title", "QR code"), "src": "/shot/" + Path(card["file"]).name, "shot": True}
        if t == "screenshot":
            return {"type": "image", "title": card.get("title", ""), "src": "/shot/" + Path(card["file"]).name, "shot": True}
        if t == "image":
            src = card.get("src", "")
            if src.startswith("https://"):
                return card
            path = safe_image_path(src)
            if not path:
                return {"type": "text", "title": card.get("title") or "image", "text": f"Can't show that image ({src[:80]})."}
            return {**card, "src": "/file?p=" + urllib.parse.quote(str(path))}
        if t == "map" and "lat" not in card:
            q = card.get("query", "")
            if q not in self.geo_cache:
                url = "https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" + urllib.parse.quote(q)
                req = urllib.request.Request(url, headers={"User-Agent": "jarvis-local-assistant/0.1"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    hit = json.load(r)
                self.geo_cache[q] = (float(hit[0]["lat"]), float(hit[0]["lon"])) if hit else None
            if not self.geo_cache[q]:
                return {"type": "text", "title": card.get("title") or "map", "text": f"Couldn't find “{q}” on the map."}
            card = {**card, "lat": self.geo_cache[q][0], "lon": self.geo_cache[q][1]}
        return card

    # --- http / websocket ---------------------------------------------------------
    def _origin_ok(self, req):
        host_ok = req.headers.get("Host") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")
        origin = req.headers.get("Origin")
        return host_ok and origin in (None, f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")

    async def index(self, req):
        if not self._origin_ok(req):
            raise web.HTTPForbidden()
        return web.FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})

    async def term_ws(self, req):
        """The terminal socket: right Host, right Origin, and the first message must carry the session token; anything else is closed. Nothing is started before that."""
        if not self._origin_ok(req) or req.headers.get("Origin") is None:
            raise web.HTTPForbidden()
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=1 << 20)
        await ws.prepare(req)
        try:
            first = await asyncio.wait_for(ws.receive_str(), 5)
            ok = first.startswith("\x00") and terminal.token_ok(json.loads(first[1:]).get("token"))
        except Exception:
            ok = False
        if not ok:
            await ws.close()
            return ws
        await terminal.handle(ws, first, asyncio.get_running_loop())
        return ws

    def _token_ok(self, req):
        return self._origin_ok(req) and terminal.token_ok(req.headers.get("X-Jarvis-Token", ""))

    async def files_api(self, req):
        if not self._token_ok(req):
            raise web.HTTPForbidden()
        r = terminal.listing(req.query.get("path"), req.query.get("hidden") == "1")
        if r is None:
            raise web.HTTPNotFound()
        return web.json_response(r)

    async def files_thumb(self, req):
        if not self._token_ok(req):
            raise web.HTTPForbidden()
        b = await asyncio.get_running_loop().run_in_executor(None, terminal.thumb, req.query.get("path"))
        if not b:
            raise web.HTTPNotFound()
        return web.Response(body=b, content_type="image/jpeg", headers={"Cache-Control": "no-store"})

    async def look_frame_api(self, request):
        """LIVE polling for a source without a stream address (USB webcam / screen): one fresh picture per request, only while the LOOK view is open."""
        if not self._origin_ok(request) or not self.assistant.look.get("open"):
            raise web.HTTPForbidden()
        path = await asyncio.get_running_loop().run_in_executor(None, self.assistant.look_live_frame)
        return web.json_response({"src": "/shot/" + os.path.basename(path)})

    async def geosuggest_api(self, request):
        if not self._origin_ok(request):
            raise web.HTTPForbidden()
        return web.json_response(await asyncio.get_running_loop().run_in_executor(None, maps.suggest, request.query.get("q", "")))

    async def windows_api(self, request):
        """The window switcher inside the Jarvis full screen: your open windows (hyprctl clients), never Jarvis's own."""
        if not self._origin_ok(request):
            raise web.HTTPForbidden()
        return web.json_response(await asyncio.get_running_loop().run_in_executor(None, opener.other_windows))

    async def popup_api(self, request):
        return web.json_response(opener.POPUP["cards"])

    async def idle_api(self, req):
        """What the idle screen shows besides the clock: the weather for the home area and the next reminder. The weather is cached for 10 minutes."""
        if not self._origin_ok(req):
            raise web.HTTPForbidden()
        from . import extras
        now = time.time()
        if now - getattr(self, "_idle_w_t", 0) > 600:
            self._idle_w_t = now
            try:
                w = await asyncio.get_running_loop().run_in_executor(None, skills.weather, "what's the weather today", "jarvis")
                c = (w or {}).get("cards", [None])[0]
                self._idle_w = {"temp": c["temp"], "unit": c["unit"], "desc": c["desc"], "place": (c.get("place") or "").split(",")[0]} if c else None
            except Exception:
                self._idle_w = None
        pend = sorted([p for p in extras.PENDING if p["due"] > now], key=lambda p: p["due"])
        nxt = {"label": pend[0]["label"] or "timer", "in_s": int(pend[0]["due"] - now), "at": time.strftime("%-I:%M %p", time.localtime(pend[0]["due"]))} if pend else None
        return web.json_response({"weather": getattr(self, "_idle_w", None), "next": nxt})

    async def map_page(self, req):
        if not self._origin_ok(req):
            raise web.HTTPForbidden()
        return web.FileResponse(WEB / "map.html", headers={"Cache-Control": "no-store"})

    def _spec(self, req):
        if not self._origin_ok(req) or req.match_info["id"] not in maps.SPECS:
            raise web.HTTPNotFound()
        return maps.SPECS[req.match_info["id"]]

    async def map_api(self, req):
        return web.json_response(self._spec(req))

    async def map_mode(self, req):
        s = self._spec(req)
        if req.query.get("m") in maps.MODES:
            s["mode"] = req.query["m"]
            if s.get("dest") and s.get("origin"):
                s["gmaps"] = maps.gmaps_link((s["origin"]["lat"], s["origin"]["lon"]), (s["dest"]["lat"], s["dest"]["lon"]), s["mode"])
        return web.json_response({"ok": True})

    async def map_goto(self, req):
        s = self._spec(req)
        try:
            pid = int(req.query.get("pid", ""))
        except ValueError:
            raise web.HTTPBadRequest()
        r = await asyncio.get_running_loop().run_in_executor(None, maps.goto_place, s["id"], pid)
        if not r:
            raise web.HTTPNotFound()
        opener.EMBED["page_url"] = r.get("gmaps") or opener.EMBED.get("page_url")
        return web.json_response(r)

    async def map_confirm(self, req):
        """The YES button on a "is this where you are?" map: saves it as the home area."""
        s = self._spec(req)
        c = s.get("confirm")
        if not c:
            return web.json_response({"message": "nothing to confirm"})
        settings.update(home_area=c["label"], home_lat=c["lat"], home_lon=c["lon"])
        maps.PENDING["g"] = None
        return web.json_response({"message": "Saved as your home area"})

    async def map_change(self, req):
        """The CHANGE button next to "Location: ...": the assistant asks where you are and keeps the mic open for the answer."""
        threading.Thread(target=self.assistant.ask_for_location, daemon=True).start()
        return web.json_response({"message": "Tell me where you are"})

    async def map_send(self, req):
        """SEND TO MAC / SEND TO PHONE on the map: a Google Maps directions link, through the signed Mac inbox or the phone topic."""
        s = self._spec(req)
        link = s.get("gmaps")
        if not link and s.get("places"):
            p = s["places"][0]
            link = f"https://www.google.com/maps/search/?api=1&query={p['lat']:.5f},{p['lon']:.5f}"
        if not link:
            return web.json_response({"message": "Nothing to send"})
        title = s.get("title") or "Map"
        if req.query.get("to") == "mac":
            r = await asyncio.get_running_loop().run_in_executor(None, mac.send, {"url": link, "label": title[:40]})
            msg = "Sent to your Mac" if r["ok"] else ("Your Mac is not paired. Say pair my Mac." if r["message"] == "not paired" else "Could not send: " + r["message"])
        else:
            r0 = (s.get("routes") or {}).get(s.get("mode"))
            body = f"{maps.fmt_dur(r0['dur_s'])}, {maps.fmt_dist(r0['dist_m'])}" if r0 else title
            r = await asyncio.get_running_loop().run_in_executor(None, phone.send, {"title": title[:60], "message": body, "link": link})
            msg = "Sent to your phone" if r["ok"] else "Could not send: " + r["message"]
        return web.json_response({"ok": r["ok"], "message": msg})

    async def dock(self, req):
        if not self._origin_ok(req):
            raise web.HTTPForbidden()
        raise web.HTTPFound("/?dock=1")

    async def shot(self, req):
        name = req.match_info["name"]
        f = config.SHOTS_DIR / name
        if not self._origin_ok(req) or not (re.fullmatch(r"shot-\d+\.jpg", name) or re.fullmatch(r"qr-\d+\.png", name)) or not f.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(f)

    async def file(self, req):
        path = safe_image_path(req.query.get("p", ""))
        if not self._origin_ok(req) or not path:
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def ws(self, req):
        if not self._origin_ok(req):
            raise web.HTTPForbidden()
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(req)
        self.clients.add(ws)
        a = self.assistant
        await ws.send_str(json.dumps({"t": "hello", "state": UI_STATE.get(a.state, a.state), "history": self.history,
                                      "muted": self.muted, "source": a.source, "persona": a.persona, "handsfree": bool(a.hf), "muted": a.muted, "followup": a.followup, "prefs": prefs.public(a.persona)}))
        try:
            async for m in ws:
                if m.type == WSMsgType.TEXT:
                    msg = json.loads(m.data)
                    if msg.get("t") == "term_open":                      # the token goes to the page that asked, over this socket, and nowhere else
                        await ws.send_str(json.dumps({"t": "termtoken", "token": terminal.TOKEN, "claude_dir": settings.get("claude_dir") or ""}))
                        continue
                    self._client_msg(msg)
        finally:
            self.clients.discard(ws)
            if a.state == "recording" and not self.clients:   # page vanished while the key was "held"
                a.release()
        return ws

    def _client_msg(self, m):
        a, t = self.assistant, m.get("t")
        if t == "talk" and m.get("cancel"):
            a.cancel_press()
        elif t == "talk":
            a.press(look=bool(m.get("look"))) if m.get("down") else a.release()
        elif t == "reset":
            a.reset()
        elif t == "silent":                                    # speech output off (not the same as MUTE = deaf mode)
            self.muted = a.speaker.muted = bool(m.get("on"))
            if self.muted:
                a.speaker.stop()
        elif t == "followup":
            a.set_followup(bool(m.get("on")))
        elif t == "muted":                                     # MUTE button: deaf mode
            threading.Thread(target=a.set_muted, args=(bool(m.get("on")),), daemon=True).start()
        elif t == "action":
            def go(name=m.get("name"), payload=m.get("payload")):
                self.bcast({"t": "action_result", "name": name, "result": actions.run(name, payload, a)})
            threading.Thread(target=go, daemon=True).start()
        elif t == "setting":                                   # one validated preference (speed, auto visuals, stay out, clock...)
            def go(k=m.get("key"), v=m.get("value")):
                try:
                    a.apply_pref(k, v, sample=(k == "speed"))
                except (ValueError, TypeError):
                    pass
            threading.Thread(target=go, daemon=True).start()
        elif t == "ask" and m.get("text") in ("pair my Mac", "set up Spotify", "show the clock now"):      # fixed requests from the settings buttons
            a.ask_text(m["text"])
        elif t == "term_focus":
            a.term_focus(bool(m.get("on")))
        elif t == "claude_dir":
            d = terminal.under_home(m.get("dir") or "")
            if d and os.path.isdir(d):
                settings.update(claude_dir=d)
        elif t == "look_select":
            try:
                a.look_select(int(m.get("i")))
            except (TypeError, ValueError):
                pass
        elif t == "look_op" and m.get("op") in ("live", "freeze", "close", "clear"):
            if m["op"] == "close":
                a.look_close()
            elif m["op"] == "freeze":
                a.look["live"] = False; opener.NOTIFY({"t": "look", "op": "freeze"})
            elif m["op"] == "live":
                a.look.update(live=True, boxes=[], sel=None); opener.NOTIFY({"t": "look", "op": "live", "stream": phonecam.get_url() if a.look["source"] == "phone camera" else None})
            else:
                a.look["boxes"], a.look["sel"] = [], None; opener.NOTIFY({"t": "look", "op": "boxes", "boxes": []})
        elif t == "look_mac":
            threading.Thread(target=lambda: a.look["path"] and a.run_mac({"file": a.look["path"], "label": "look"}), daemon=True).start()
        elif t == "locsetup":
            a.on_card(maps.location_card(False))
        elif t == "phonecam_setup":
            a.on_card(phonecam.setup_card())
        elif t == "soundtest":
            threading.Thread(target=a.run_soundtest, daemon=True).start()
        elif t == "listen":
            threading.Thread(target=a.set_handsfree, args=(bool(m.get("on")),), daemon=True).start()
        elif t == "beat":
            self.ui_beat = time.time()
        elif t == "dictate":                                   # hold the on-screen mic button / the key: words are typed into the focused window
            threading.Thread(target=self.dictation.start if m.get("down") else self.dictation.stop, daemon=True).start()
        elif t == "persona":
            threading.Thread(target=a.set_persona, args=(m.get("v"), bool(m.get("announce", True))), daemon=True).start()
        elif t == "source" and m.get("v") in ("auto", "screen", "window", "camera", "phone"):
            a.source = m["v"]
            if m["v"] == "camera":
                threading.Thread(target=a.camera_preview, daemon=True).start()
        elif t == "camera":
            threading.Thread(target=a.camera_preview, daemon=True).start()

    # --- background tasks -------------------------------------------------------
    async def quiet_loop(self):
        """Quiet hours: dim the screen when they start, restore it when they end (checked every 30 s)."""
        from . import quiet
        while True:
            await asyncio.sleep(30)
            try:
                on = quiet.active()
                if on != getattr(self.assistant, "_dimmed", False):
                    await asyncio.get_running_loop().run_in_executor(None, self.assistant.quiet_apply, on)
            except Exception as e:
                print("[quiet] failed:", type(e).__name__, flush=True)

    async def briefing_loop(self):
        """'Brief me every morning at 8': at that minute, once a day, only if the screen is on and he is not muted."""
        last = None
        while True:
            await asyncio.sleep(20)
            try:
                hhmm = settings.get("briefing_time")
                now = time.strftime("%H:%M")
                if hhmm and now == hhmm and last != time.strftime("%Y-%m-%d"):
                    a = self.assistant
                    mons = opener._hypr_json("monitors")
                    awake = isinstance(mons, list) and any(m.get("dpmsStatus", True) for m in mons)
                    from . import quiet
                    if awake and not a.muted and a.state == "idle" and not quiet.active():
                        last = time.strftime("%Y-%m-%d")
                        a.ask_text("good morning")
            except Exception as e:
                print("[briefing] failed:", type(e).__name__, flush=True)

    async def health_loop(self):
        """Every 20 s: machine problems must last a minute, are announced at most once per 30 minutes, and go to a small status chip (not a card, not speech) unless something
        is about to fail (memory almost gone, battery nearly empty), which gets a card. SETTINGS > Advanced > Machine warnings turns all of it off."""
        from . import health, prefs
        while True:
            await asyncio.sleep(20)
            try:
                if not prefs.get("machine_warnings"):
                    self.bcast({"t": "health", "msg": ""})
                    continue
                new = health.check()
                a = self.assistant
                if new:
                    crit = [v for k, v in new if k == "critical"]
                    self.bcast({"t": "health", "msg": " · ".join(v for _, v in new)})
                    self._health_clear = time.time() + 90
                    if crit:
                        self.on_card({"type": "text", "title": "Machine warning", "text": crit[0]})
                elif getattr(self, "_health_clear", 0) and time.time() > self._health_clear:
                    self._health_clear = 0
                    self.bcast({"t": "health", "msg": ""})
            except Exception as e:
                print("[health] check failed:", type(e).__name__, flush=True)

    async def thermal_loop(self):
        """Lite mode turns on by itself when the CPU is hot (>= 78 C) and off again when it has cooled (<= 68 C). Sent to the pages as {"t": "lite"}."""
        hot = False
        while True:
            try:
                temps = [int(open(f"/sys/class/thermal/{z}/temp").read()) / 1000 for z in os.listdir("/sys/class/thermal") if z.startswith("thermal_zone")
                         and open(f"/sys/class/thermal/{z}/type").read().strip() in ("x86_pkg_temp", "acpitz", "coretemp")]
                t = max(temps) if temps else 0
                if not hot and t >= 78:
                    hot = True
                    self.bcast({"t": "lite", "on": True, "why": f"CPU {t:.0f} C: LITE MODE ON"})
                elif hot and t <= 68:
                    hot = False
                    self.bcast({"t": "lite", "on": False, "why": f"CPU cooled ({t:.0f} C): LITE MODE OFF"})
            except Exception:
                pass
            await asyncio.sleep(10)

    async def mic_loop(self):
        """Live mic level to the page while the key (or the screen) is held."""
        a = self.assistant
        while True:
            await asyncio.sleep(0.05)
            if a.state == "recording" and self.clients:
                x = a.rec.tail(1600).astype(np.float32) / 32768.0
                if len(x):
                    db = 20 * math.log10(max(float(np.sqrt((x * x).mean())), 1e-5))
                    self.bcast({"t": "level", "src": "mic", "v": round(min(1, max(0, (db + 55) / 35)), 3), "db": round(db, 1)})

    async def control(self, reader, writer):
        """jarvisctl socket: start | stop | look | reset | show | log | quit | status"""
        a = self.assistant
        try:
            cmd = (await reader.readline()).decode().strip()
            extra = ""
            if cmd == "start":
                a.press()
            elif cmd == "look":
                a.press(look=True)
            elif cmd == "stop":
                a.release()
            elif cmd == "reset":
                a.reset()
            elif cmd == "home" or cmd.startswith("home "):
                if cmd == "home":
                    extra = f"home area: {settings.get('home_area') or 'not set'}"
                else:
                    r = await asyncio.get_running_loop().run_in_executor(None, skills.set_home, "set my home to " + cmd[5:])
                    extra = r["speech"] if r else "could not set home area"
            elif cmd == "mute" or cmd.startswith("mute "):
                arg = cmd.split()[1] if " " in cmd else "status"
                if arg in ("on", "off", "toggle"):
                    threading.Thread(target=a.set_muted, args=((not a.muted) if arg == "toggle" else arg == "on",), daemon=True).start()
                    await asyncio.sleep(0.3)
            elif cmd.startswith("act "):                         # local test hook: a structured action, validated like any other
                act = opener.validate(json.loads(cmd[4:]))
                extra = str(a.run_open(act)) if act else "rejected"
            elif cmd.startswith("ask "):                         # local test hook: the same path as a spoken question (typed text)
                threading.Thread(target=a.ask_text, args=(cmd[4:],), daemon=True).start()
                extra = "asked"
            elif cmd == "soundtest":
                threading.Thread(target=a.run_soundtest, daemon=True).start()
                extra = "sound test started"
            elif cmd == "dictate start":
                extra = "dictating" if await asyncio.get_running_loop().run_in_executor(None, self.dictation.start) else "busy"
            elif cmd == "dictate stop":
                await asyncio.get_running_loop().run_in_executor(None, self.dictation.stop)
            elif cmd.startswith("dictate file "):                # test hook: dictate from a recording instead of the microphone
                extra = "typed: " + str(await asyncio.get_running_loop().run_in_executor(None, self.dictation.from_file, cmd[13:]))
            elif cmd in ("micbtn show", "micbtn hide"):
                dictate.set_micbtn(cmd.endswith("show"))
            elif cmd == "camera":
                ok = await asyncio.get_running_loop().run_in_executor(None, a.camera_preview)
                extra = "camera frame shown" if ok else "no USB webcam"
            elif cmd.startswith("help "):                            # from the watchdog: a plain card instead of a restart loop
                self.on_card({"type": "text", "title": "Jarvis needs help", "text": cmd[5:][:300]})
            elif cmd == "listen restart":
                threading.Thread(target=lambda: (a.set_handsfree(False), time.sleep(1.0), a.set_handsfree(True)), daemon=True).start()
            elif cmd == "brainwarm":
                threading.Thread(target=a.brain.warm, daemon=True).start()
            elif cmd == "toggle":                                 # SUPER+SHIFT+J: corner orb <-> full screen
                threading.Thread(target=opener.toggle_orb, daemon=True).start()
            elif cmd == "back":                                   # SUPER+ALT+J: focus the Jarvis window again
                threading.Thread(target=a.run_open, args=({"action": "focus_jarvis", "label": "Jarvis"},), daemon=True).start()
            elif cmd.startswith("ask "):                          # dev/test: a text request, as if it had been transcribed
                threading.Thread(target=a.ask_text, args=(cmd[4:],), daemon=True).start()
            elif cmd.startswith("followup "):
                a.set_followup(cmd.split()[1] == "on")
            elif cmd.startswith("listen "):
                threading.Thread(target=a.set_handsfree, args=(cmd.split()[1] == "on",), daemon=True).start()
            elif cmd.startswith("persona "):
                threading.Thread(target=a.set_persona, args=(cmd.split()[1],), daemon=True).start()
            elif cmd == "timings":                                  # the last few requests: seconds since the question for each stage
                from . import extras as _ex
                extra = " || ".join(" | ".join(f"{k} {v:.2f}" for k, v in h.items()) for h in _ex.HISTORY[-1:])
            elif cmd == "log":
                extra = "\n".join(f"{h['role']}: {h['text']}" for h in self.history[-6:])
            elif cmd == "quit":
                try:                                                 # a deliberate quit: tell the watchdog not to bring Jarvis back
                    open(os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "jarvis.stopped"), "w").close()
                except OSError:
                    pass
                asyncio.get_running_loop().call_later(0.2, lambda: os._exit(0))
            mic = "off" if not a.hf else ("ok" if time.time() - getattr(a.hf, "alive_t", time.time()) < 6 else "stale")
            ui = int(time.time() - self.ui_beat) if self.ui_beat else 0
            writer.write(f"ok {cmd.split()[0]} [{a.state}] mic={mic} ui={ui}s persona={a.persona} voice={Path(tts._current).stem} handsfree={'on' if a.hf else 'off'} followup={'on' if a.followup else 'off'} conv={'on' if a.in_conv else 'off'} muted={'on' if a.muted else 'off'}\n{extra}\n".encode())
            await writer.drain()
        finally:
            writer.close()

    async def run(self):
        self.loop = asyncio.get_running_loop()
        self.assistant = Assistant(self.on_state, self.on_msg, on_card=self.on_card, on_level=self.on_level,
                                   on_persona=self.on_persona, on_listen=self.on_listen, on_status=self.on_status, on_muted=self.on_muted, on_ui=self.on_ui, cards=True, handsfree=config.HANDSFREE)
        self.dictation = dictate.Dictation(self.assistant, lambda text, live: self.loop.call_soon_threadsafe(self.bcast, {"t": "caption", "text": text, "live": live}))
        @web.middleware
        async def nocache(req, handler):
            resp = await handler(req)
            resp.headers.setdefault("Cache-Control", "no-store")
            return resp
        app = web.Application(middlewares=[nocache])
        app.router.add_get("/", self.index)
        app.router.add_get("/api/idle", self.idle_api)
        app.router.add_get("/api/popup", self.popup_api)
        app.router.add_get("/api/windows", self.windows_api)
        app.router.add_get("/api/geosuggest", self.geosuggest_api)
        app.router.add_get("/api/look/frame", self.look_frame_api)
        app.router.add_get("/term/ws", self.term_ws)
        app.router.add_get("/api/files", self.files_api)
        app.router.add_get("/api/files/thumb", self.files_thumb)
        app.router.add_get("/map", self.map_page)
        app.router.add_get("/api/map/{id}", self.map_api)
        app.router.add_post("/api/map/{id}/mode", self.map_mode)
        app.router.add_post("/api/map/{id}/goto", self.map_goto)
        app.router.add_post("/api/map/{id}/send", self.map_send)
        app.router.add_post("/api/map/{id}/confirm", self.map_confirm)
        app.router.add_post("/api/map/{id}/change", self.map_change)
        app.router.add_get("/dock", self.dock)
        app.router.add_get("/ws", self.ws)
        app.router.add_get("/shot/{name}", self.shot)
        app.router.add_get("/file", self.file)
        app.router.add_static("/static", WEB, follow_symlinks=False)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", PORT).start()
        if config.SOCK.exists():
            config.SOCK.unlink()
        await asyncio.start_unix_server(self.control, path=str(config.SOCK))
        self.assistant.preload()
        asyncio.ensure_future(self.thermal_loop())
        asyncio.ensure_future(self.health_loop())
        asyncio.ensure_future(self.briefing_loop())
        asyncio.ensure_future(self.quiet_loop())
        if os.environ.get("JARVIS_NO_DESKTOP") != "1":       # desktop side effects: the dock orb and a parked warm browser (tests set JARVIS_NO_DESKTOP=1)
            opener.set_notifier(lambda msg: self.loop.call_soon_threadsafe(self.bcast, msg))
            opener.watch_closed()
            if settings.get("micbtn"):
                dictate.set_micbtn(True)
            threading.Thread(target=lambda: (opener.ensure_dock(), opener.ensure_pool()), daemon=True).start()
            from . import syshold
            import atexit, signal
            if syshold.hold():                                   # the system screensaver must not start while Jarvis runs (the original value is restored on exit)
                print("[idle] system screensaver held off while Jarvis runs", flush=True)
            atexit.register(syshold.release)
            signal.signal(signal.SIGTERM, lambda *a: (syshold.release(), os._exit(0)))
        print(f"jarvis ui on http://127.0.0.1:{PORT}", flush=True)
        await self.mic_loop()


def main():
    asyncio.run(Server().run())


if __name__ == "__main__":
    main()
