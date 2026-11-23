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

from . import personas, audio, companion, desk, lookmem, dictate, maps, mac, opener, phone, phonecam, prefs, terminal, voiceapi, actions, config, settings, skills, tts
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
        self._settings_problems = []

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

    def on_phone_request(self, text, reply):
        self.bcast({"t": "phonereq", "text": text[:140], "reply": reply[:160]})

    def voice_set(self, on):
        """Turn the iPhone endpoint on or off (called from the settings card's button, from a worker thread). -> (ok, message)."""
        fut = asyncio.run_coroutine_threadsafe(voiceapi.start(self.assistant, self.on_phone_request) if on else voiceapi.stop(), self.loop)
        res = fut.result(15)
        ok, msg = res if on else (True, "off")
        if ok:
            settings.update(phone_voice=bool(on))
        return ok, msg

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

    async def launcher_api(self, req):
        if not self._token_ok(req):
            raise web.HTTPForbidden()
        return web.json_response(await asyncio.get_running_loop().run_in_executor(None, desk.launcher_search, req.query.get("q", "")))

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

    async def lookmem_file(self, req):
        """A picture saved by "remember this" (data/look/look-<ms>.jpg), shown again by "where did I put ...?"."""
        name = req.match_info["name"]
        f = lookmem.DIR / name
        if not self._origin_ok(req) or not re.fullmatch(r"look-\d+\.jpg", name) or not f.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(f, headers={"Cache-Control": "no-store"})

    async def look_stream_api(self, request):
        """The phone camera as ONE shared stream for the page (LOOK live view, Phone Camera preview): the server holds the only connection to the phone."""
        from . import phonecam
        if not self._origin_ok(request) or not phonecam.get_url():
            raise web.HTTPForbidden()
        resp = web.StreamResponse(headers={"Content-Type": "multipart/x-mixed-replace; boundary=jvframe", "Cache-Control": "no-store"})
        await resp.prepare(request)
        try:
            phonecam.RELAY.hold()
        except RuntimeError:
            return resp
        last = -1
        try:
            idle = 0
            while True:
                if request.transport is None or request.transport.is_closing():     # the page went away even though no write failed (frames stalled)
                    break
                with phonecam.RELAY.lock:
                    n, f = phonecam.RELAY.n, phonecam.RELAY.latest
                if f and n != last:
                    last, idle = n, 0
                    await resp.write(b"--jvframe\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(f)).encode() + b"\r\n\r\n" + f + b"\r\n")
                else:
                    idle += 1
                    if idle > 200 and phonecam.RELAY.err:               # about 8 s of nothing and an error: end the stream, the page says why
                        break
                await asyncio.sleep(0.07)                                # about 14 frames a second at most
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            phonecam.RELAY.release()
        return resp

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

    async def mkt_pulse_api(self, req):
        from . import mktdesk
        if not mktdesk.enabled():
            return web.json_response({"error": "off"}, status=404)
        return web.json_response(await asyncio.get_running_loop().run_in_executor(None, mktdesk.pulse))

    async def mkt_chart_api(self, req):
        """Chart data computed by code (levels, averages, the statistical band) for the desk card's own buttons; the voice path uses the same function."""
        from . import markets, mktdesk
        if not mktdesk.enabled():
            return web.json_response({"error": "off"}, status=404)
        q = req.query
        sym = re.sub(r"[^A-Za-z0-9.^=-]", "", q.get("sym", ""))[:12]
        rng = q.get("range", "3mo") if q.get("range") in markets.RANGES else "3mo"
        ind = [x for x in q.get("ind", "").split(",") if re.fullmatch(r"ma\d{1,3}", x)][:4]
        cmp_ = re.sub(r"[^A-Za-z0-9.^=-]", "", q.get("cmp", ""))[:12] or None
        if not sym:
            return web.json_response({"error": "no symbol"}, status=400)
        p = await asyncio.get_running_loop().run_in_executor(None, lambda: markets.chart_payload(sym, rng, ind, q.get("levels") == "1", q.get("band") == "1", cmp_))
        return web.json_response(p)

    async def model_file(self, req):
        """A cached real model (only a file this app downloaded and checked: the name must be exactly 16 hex characters and glb or stl)."""
        from . import models3d
        p = models3d.path_of(req.match_info["name"])
        if not p:
            raise web.HTTPNotFound()
        return web.FileResponse(p, headers={"Cache-Control": "private, max-age=3600", "Content-Type": "model/gltf-binary" if p.suffix == ".glb" else "model/stl"})

    async def holo_export_api(self, req):
        """A picture or a few seconds of video of the open 3D model, from the card's PNG / VIDEO buttons -> ~/jarvis/exports (small files only, fixed names)."""
        kind = req.query.get("kind", "")
        if kind not in ("png", "webm") or (req.content_length or 0) > 8_000_000:
            return web.json_response({"ok": False, "error": "not allowed"}, status=400)
        data = await req.read()
        if len(data) > 8_000_000 or not data:
            return web.json_response({"ok": False, "error": "too big"}, status=413)
        magic_ok = data[:8] == b"\x89PNG\r\n\x1a\n" if kind == "png" else data[:4] == b"\x1aE\xdf\xa3"
        if not magic_ok:
            return web.json_response({"ok": False, "error": "not a " + kind}, status=415)
        import re as _re
        slug = _re.sub(r"[^a-z0-9]+", "-", req.query.get("title", "model").lower()).strip("-")[:30] or "model"
        d = config.ROOT / "exports"
        d.mkdir(exist_ok=True)
        name = f"{slug}-{int(time.time())}.{kind}"
        (d / name).write_bytes(data)
        for f in sorted(d.glob("*"), key=lambda x: x.stat().st_mtime)[:-30]:                           # keep the newest 30
            try:
                f.unlink()
            except OSError:
                pass
        self.assistant.last_export = str(d / name)
        return web.json_response({"ok": True, "name": name})

    async def audio_api(self, req):
        """Settings > Music: the Spotify player state and the audio outputs PipeWire knows (read only: nothing is changed, nothing is paired)."""
        if not self._origin_ok(req):
            raise web.HTTPForbidden()
        from . import spotify, spotifyd
        loop = asyncio.get_running_loop()
        st = await loop.run_in_executor(None, spotifyd.state)
        outs = await loop.run_in_executor(None, spotifyd.outputs)
        return web.json_response({"player": st, "outputs": outs, "spotify": spotify.connected(), "max": spotifyd.max_volume()})

    async def conn_warn_loop(self):
        """No popup spam: a warning shows only when a link you are USING right now has been down for more than 10 seconds, and clears by itself."""
        from . import connections, spotify_cli, wifi
        last, off_since, shown = None, 0.0, False
        while True:
            await asyncio.sleep(10)
            try:
                ok, _ssid = await asyncio.get_running_loop().run_in_executor(None, connections._wifi)           # no known network in range for 30 s: the Wi-Fi screen opens by itself (once per outage), so no keyboard is needed
                if ok:
                    off_since, shown = 0.0, False
                else:
                    off_since = off_since or time.time()
                    if time.time() - off_since > 30 and not shown:
                        r = await asyncio.get_running_loop().run_in_executor(None, wifi.scan, True)
                        if not r.get("known_nearby"):
                            shown = True
                            self.bcast({"t": "wifi_screen"})
                a = self.assistant
                use = []
                it = spotify_cli.LAST.get("item")
                if it and time.time() - spotify_cli.LAST.get("t", 0) < 600:
                    use += ["spotify", "internet"]
                if a.look.get("open") and a.look.get("source") == "phone camera":
                    use.append("phone_cam")
                lines = await asyncio.get_running_loop().run_in_executor(None, connections.warnings, use)
                if lines != last:
                    last = lines
                    self.bcast({"t": "conn_warn", "lines": lines})
            except Exception:
                pass

    async def wifi_api(self, req):
        """Settings > Wi-Fi: the networks around (read only). ?rescan=1 asks the radio to scan again."""
        if not self._origin_ok(req):
            raise web.HTTPForbidden()
        from . import wifi
        r = await asyncio.get_running_loop().run_in_executor(None, wifi.scan, req.query.get("rescan") == "1")
        return web.json_response(r)

    async def wifi_connect(self, req):
        """Connect to a network from the last scan. The password comes in the request body, goes to nmcli on stdin, and is never logged or kept."""
        if not self._origin_ok(req):
            raise web.HTTPForbidden()
        from . import wifi
        try:
            d = await req.json()
        except ValueError:
            raise web.HTTPBadRequest()
        ok, msg = await asyncio.get_running_loop().run_in_executor(None, wifi.connect, str(d.get("ssid", "")), (str(d["password"])[:128] if d.get("password") else None))
        return web.json_response({"ok": ok, "message": msg})

    async def conn_api(self, req):
        """Settings > Connections: every link with green/amber/red, plain words and the last success. Read only: never starts a test."""
        if not self._origin_ok(req):
            raise web.HTTPForbidden()
        from . import connections
        rows = await asyncio.get_running_loop().run_in_executor(None, connections.snapshot)
        return web.json_response({"rows": rows})

    async def conn_act(self, req):
        """TEST or FIX one link (POST /api/connections/<id>/test|fix). A phone test can take a minute: it runs in a thread and the page polls."""
        if not self._origin_ok(req):
            raise web.HTTPForbidden()
        from . import connections
        i, op = req.match_info["id"], req.match_info["op"]
        if i not in connections.ORDER or op not in ("test", "fix"):
            raise web.HTTPNotFound()
        a = self.assistant
        def go():
            try:
                connections.test(i, a) if op == "test" else connections.fix(i)
            except Exception as e:
                connections._set(i, "red", "That check failed: " + type(e).__name__)
        threading.Thread(target=go, daemon=True).start()
        connections._set(i, "amber", "Testing…" if op == "test" else "Fixing…")
        return web.json_response({"ok": True})

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
        if not nxt:                                                         # no reminder: the next calendar event, if a calendar is connected
            from . import calmail
            nxt = await asyncio.get_running_loop().run_in_executor(None, calmail.next_label)
        return web.json_response({"weather": getattr(self, "_idle_w", None), "next": nxt})

    async def map_page(self, req):
        if not self._origin_ok(req):
            raise web.HTTPForbidden()
        return web.FileResponse(WEB / "map.html", headers={"Cache-Control": "no-store"})

    def _spec(self, req):
        if req.match_info["id"] == "me" and self._origin_ok(req):
            return maps.me_spec()
        if not self._origin_ok(req) or req.match_info["id"] not in maps.SPECS:
            raise web.HTTPNotFound()
        return maps.SPECS[req.match_info["id"]]

    async def map_api(self, req):
        if req.match_info["id"] == "me":                                      # no spec: just show where I am (the MAP button)
            return web.json_response(maps.me_spec())
        return web.json_response(self._spec(req))

    async def hook_api(self, req):
        """Claude Code / Codex status from bin/jarvis-hook (localhost only, per-install token)."""
        from . import claudestat, notifier
        if not claudestat.check(req.match_info["token"]) or req.remote not in ("127.0.0.1", "::1"):
            raise web.HTTPNotFound()
        try:
            body = await req.json()
        except Exception:
            raise web.HTTPBadRequest()
        st = claudestat.update(body)
        if not st:
            raise web.HTTPBadRequest()
        self.bcast({"t": "claudestat", **st})
        if st["state"] == "done":
            threading.Thread(target=notifier.send, args=("claude", "Claude is done", "Claude Code finished on the Go."), daemon=True).start()
        return web.json_response({"ok": True})

    async def map_style(self, req):
        if not self._origin_ok(req):
            raise web.HTTPForbidden()
        from . import tilecache
        origin = "%s://%s" % (req.scheme, req.host)
        try:
            st = await asyncio.get_running_loop().run_in_executor(None, tilecache.style, origin)
        except Exception:
            raise web.HTTPServiceUnavailable(text="map style unavailable")
        return web.json_response(st, headers={"Cache-Control": "max-age=300"})

    async def tile_proxy(self, req):
        if not self._origin_ok(req):
            raise web.HTTPForbidden()
        from . import tilecache
        try:
            data, ctype, how = await asyncio.get_running_loop().run_in_executor(None, tilecache.get, req.match_info["src"], req.match_info["path"])
        except LookupError:
            raise web.HTTPNotFound()
        except OSError:
            raise web.HTTPNotFound(text="no tile")
        return web.Response(body=data, content_type=ctype, headers={"Cache-Control": "max-age=86400", "X-Tile": how})

    async def map_mode(self, req):
        s = self._spec(req)
        if req.query.get("m") in maps.MODES:
            s["mode"] = req.query["m"]
            if s.get("dest") and s.get("origin"):
                s["gmaps"] = maps.gmaps_link((s["origin"]["lat"], s["origin"]["lon"]), (s["dest"]["lat"], s["dest"]["lon"]), s["mode"])
        return web.json_response({"ok": True})

    async def map_alt(self, req):
        """The arrows on a route map: another match for the same name (the next Starbucks)."""
        s = self._spec(req)
        try:
            i = int(req.query.get("i", ""))
        except ValueError:
            raise web.HTTPBadRequest()
        r = await asyncio.get_running_loop().run_in_executor(None, maps.pick_alt, s["id"], i)
        if not r:
            raise web.HTTPNotFound()
        opener.EMBED["page_url"] = r.get("gmaps") or opener.EMBED.get("page_url")
        return web.json_response(r)

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
        try:                                                          # "restarted because: ..." for a minute after the supervisor restarted something
            rr = json.loads((config.ROOT / "run" / "restart_reason.json").read_text())
            if time.time() - rr["t"] < 90 and int(rr.get("n", 1)) >= 3:                # a quiet self-heal: nothing is shown for a normal restart, only when it keeps happening (3 times within the supervisor's window)
                await ws.send_str(json.dumps({"t": "restarted", "what": rr["what"], "why": rr["why"]}))
        except (OSError, ValueError, KeyError):
            pass
        for pr in (self._settings_problems or [])[:1]:
            await ws.send_str(json.dumps({"t": "restarted", "what": "settings", "why": "a saved value is invalid: " + pr}))
        await ws.send_str(json.dumps({"t": "hello", "build": max((f.stat().st_mtime_ns for f in WEB.glob("*") if f.suffix in (".js", ".css", ".html")), default=0), "state": UI_STATE.get(a.state, a.state), "history": self.history,
                                      "muted": self.muted, "source": a.source, "persona": a.persona, "personas": personas.public(), "handsfree": bool(a.hf), "muted": a.muted, "followup": a.followup, "prefs": prefs.public(a.persona)}))
        try:
            async for m in ws:
                if m.type == WSMsgType.TEXT:
                    try:
                        msg = json.loads(m.data)
                    except ValueError:
                        continue                                        # a malformed message is ignored; it must not drop the page's connection
                    if not isinstance(msg, dict):
                        continue
                    if msg.get("t") == "scratch_get":
                        await ws.send_str(json.dumps({"t": "scratch", "text": desk.scratch_get()}))
                        continue
                    if msg.get("t") == "clip_get":
                        await ws.send_str(json.dumps({"t": "clips", "items": list(desk.CLIP)[:20]}))
                        continue
                    if msg.get("t") == "voice_copy":                         # the full address (it contains the secret) goes to the page that asked, to put on the clipboard, and nowhere else
                        await ws.send_str(json.dumps({"t": "clip", "text": voiceapi.url()}))
                        continue
                    if msg.get("t") == "voice_copy_look":                    # same secret, the photo route
                        await ws.send_str(json.dumps({"t": "clip", "text": voiceapi.url().replace("/voice", "/look")}))
                        continue
                    if msg.get("t") == "voice_copy_location":                # same secret, the location route
                        await ws.send_str(json.dumps({"t": "clip", "text": voiceapi.url().replace("/voice", "/location")}))
                        continue
                    if msg.get("t") == "term_open":                      # the token goes to the page that asked, over this socket, and nowhere else
                        await ws.send_str(json.dumps({"t": "termtoken", "token": terminal.TOKEN, "claude_dir": settings.get("claude_dir") or ""}))
                        continue
                    try:
                        self._client_msg(msg)
                    except Exception as e:                              # one bad message (wrong type, missing field) never kills the connection
                        print("[ws] bad message ignored:", type(e).__name__, flush=True)
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
            (threading.Thread(target=a.press, kwargs={"look": True}, daemon=True).start() if m.get("look") else a.press()) if m.get("down") else a.release()
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
        elif t == "ask" and m.get("text") in ("pair my Mac", "set up Spotify", "show the clock now", "set up the iphone voice", "what happened", "take the tour", "what couldn't you understand", "check my connections"):      # fixed requests from the settings buttons
            a.ask_text(m["text"])
        elif t == "sfx" and m.get("ev") in ("open", "close", "menu"):
            from . import sounds
            if time.time() - getattr(self, "_sfx_t", 0) > 0.12:
                self._sfx_t = time.time()
                sounds.play(m["ev"])
        elif t == "presence" and m.get("op") in ("pause", "resume", "off", "on"):
            from . import presence
            threading.Thread(target=a.run_presence, args=({"kind": m["op"]},), daemon=True).start()
        elif t == "perf":
            from . import perf
            self.bcast({"t": "card", "card": perf.card()}) if False else a.on_card(perf.card())
        elif t == "typed" and isinstance(m.get("text"), str) and m["text"].strip():           # the chat widget / type line: the same router as voice
            img = None
            mm = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)", m.get("image") or "") if isinstance(m.get("image"), str) and len(m.get("image") or "") < 8_500_000 else None
            if mm:
                import base64
                config.SHOTS_DIR.mkdir(exist_ok=True)
                img = str(config.SHOTS_DIR / f"shot-{int(time.time() * 1000)}.{'jpg' if mm.group(1) == 'jpeg' else mm.group(1)}")
                with open(img, "wb") as f:
                    f.write(base64.b64decode(mm.group(2)))
                for old in sorted([x for x in config.SHOTS_DIR.glob("shot-*.*") if x.suffix in (".png", ".webp")], key=lambda x: x.stat().st_mtime)[:-4]:
                    old.unlink(missing_ok=True)                           # typed pictures are not kept forever
            threading.Thread(target=a.ask_text, args=(m["text"].strip()[:600],), kwargs={"image": img} if img else {}, daemon=True).start()
        elif t == "scratch_save":
            desk.scratch_put(str(m.get("text", "")))
        elif t == "layout_save":
            n = desk.save_layout(str(m.get("name", "layout")), [w for w in (m.get("wins") or []) if isinstance(w, dict)])
            a.say(f"Saved the {m.get('name', 'layout')} layout with {n} window{'s' if n != 1 else ''}.")
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
        elif t == "look_op" and m.get("op") in ("live", "freeze", "close", "clear", "phone", "highlight", "scan", "connect", "read", "count", "measure", "wiring", "connected"):
            if m["op"] == "phone":
                threading.Thread(target=lambda: a.run_send("phone", "look"), daemon=True).start()
            elif m["op"] in ("connect", "read", "count", "measure", "wiring"):
                q = {"connect": "help me connect these", "read": "read the label on this", "count": "count the items on the desk", "measure": "measure this with a card", "wiring": "check my wiring"}[m["op"]]
                threading.Thread(target=a.ask_text, args=(q,), daemon=True).start()
            elif m["op"] == "connected":
                g = getattr(a, "_guide", None)
                if g:
                    g.confirm(); a.say("Good. Verify before powering.") if g.electronics else a.say("Good, connected.")
            elif m["op"] in ("highlight", "scan"):
                threading.Thread(target=a.ask_text, args=("highlight everything" if m["op"] == "highlight" else "scan everything on my desk",), daemon=True).start()
            elif m["op"] == "close":
                a.look_close()
            elif m["op"] == "freeze":
                a.look["live"] = False; opener.NOTIFY({"t": "look", "op": "freeze"})
            elif m["op"] == "live":
                a.look.update(live=True, boxes=[], sel=None); opener.NOTIFY({"t": "look", "op": "live", "stream": "/api/look/stream" if a.look["source"] == "phone camera" else None})
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
        elif t == "system" and m.get("op") in ("restart_jarvis", "reboot", "poweroff", "screen_off", "screen_on", "brightness"):     # a touch on a SYSTEM button (restart/shut down only with the page's CONFIRM press)
            from . import sysctl
            def go():
                ok, say = sysctl.run(m["op"], m.get("arg"), bool(m.get("confirm")))
                a.say(say)
            threading.Thread(target=go, daemon=True).start()
        elif t == "dictate":                                   # hold the on-screen mic button / the key: words are typed into the focused window
            threading.Thread(target=self.dictation.start if m.get("down") else self.dictation.stop, daemon=True).start()
        elif t == "mkt_closed":
            from . import mktdesk
            mktdesk.STATE["open"] = False
        elif t in ("holo_open", "holo_closed", "holo_sel", "holo_notes", "holo_tour", "holo_save_ask"):
            a.holo_event(m)
        elif t == "persona":
            threading.Thread(target=a.set_persona, args=(m.get("v"), bool(m.get("announce", True))), daemon=True).start()
        elif t == "source" and m.get("v") in ("auto", "screen", "window", "camera", "phone"):
            a.source = m["v"]                                         # choosing a source never starts a camera or raises a popup
        elif t == "camera" or t == "look_button":
            threading.Thread(target=a.look_button, daemon=True).start()

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

    def open_typebox(self):
        """Open (or close, if open) the small type box next to the pointer. Needs no browser: it is a tiny native overlay."""
        a = self.assistant
        subprocess.Popen([str(config.ROOT / "bin" / "jarvis-typebox"), a.persona, personas.PERSONAS[a.persona]["name"]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)

    async def market_alert_loop(self):
        """Alerts for stocks you asked about: one check a minute, only if you have any. They only notify (a chip, a spoken line, your phone); they never act."""
        from . import markets, mktdesk
        while True:
            await asyncio.sleep(60)
            try:
                if mktdesk.enabled() and markets.alerts():
                    for line in await asyncio.get_running_loop().run_in_executor(None, markets.check_alerts):
                        self.bcast({"t": "health", "msg": line})
                        self.assistant.say(line + " " + "This is only an alert, not advice.") if not self.assistant.muted else None
                        if prefs.get("notify_phone_reminders"):
                            threading.Thread(target=self.assistant.run_phone, args=({"title": "Market alert", "message": line},), daemon=True).start()
            except Exception as e:
                print("[markets] alert check failed:", type(e).__name__, flush=True)

    async def buddy_loop(self):
        """Recurring reminders and promise nudges: checked every 10 s."""
        while True:
            await asyncio.sleep(10)
            try:
                await asyncio.get_running_loop().run_in_executor(None, self.assistant.buddy_tick)
                from . import perf
                g = perf.governor()
                if g:                                                       # no fan: too hot -> lite mode by itself (said once), back to normal when it has cooled
                    self.bcast({"t": "thermal", "on": g == "hot", "temp": round(perf.cpu_temp_c() or 0)})
                    if g == "hot":
                        self.assistant.say("It is running hot, so I switched to lite mode until it cools down.")
            except Exception as e:
                print("[buddy] failed:", type(e).__name__, flush=True)

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

    async def watch_loop(self):
        """Small background checks: offline chip, microphone clipping, a daily look for updates (only if this copy has an update source)."""
        from . import net, updater
        last_update, was_off = 0.0, False
        while True:
            await asyncio.sleep(20)
            try:
                off = not await asyncio.get_running_loop().run_in_executor(None, net.online)
                if off != was_off:
                    was_off = off
                    self.assistant.offline_changed(off, "net", speak=True)           # chip + said once; back online clears it by itself
                if int(time.time()) % 600 < 20:
                    self.assistant.mic_watch()
                    audio.cleanup_stale()
                    tts.prune_cache()
                if time.time() - last_update > 86400 and updater.source()[0] and prefs.get("update_check"):
                    last_update = time.time()
                    c = await asyncio.get_running_loop().run_in_executor(None, updater.check)
                    if c.get("behind"):
                        self.bcast({"t": "health", "msg": f"Update available ({c['behind']}): say 'update yourself'"})
            except Exception as e:
                print("[watch] failed:", type(e).__name__, flush=True)

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
            cmd = (await reader.readline()).decode(errors="replace").strip() or "status"        # an empty line is just a status check, never a crash
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
                ok = await asyncio.get_running_loop().run_in_executor(None, a.look_button)
                extra = "look view opened" if ok else "no camera frame"
            elif cmd.startswith("help "):                            # from the watchdog: a plain card instead of a restart loop
                self.on_card({"type": "text", "title": "Jarvis needs help", "text": cmd[5:][:300]})
            elif cmd == "listen restart":
                threading.Thread(target=lambda: (a.set_handsfree(False), time.sleep(1.0), a.set_handsfree(True)), daemon=True).start()
            elif cmd.split()[0] == "typebox":                     # the Hyprland binds: `jarvisctl typebox alt` / `typebox ctrl` / `typebox` (the menu button)
                key = (cmd.split() + ["any"])[1]
                want = settings.get("typebox_key") or "alt"
                if key == "any" or want == "both" or want == key:
                    threading.Thread(target=self.open_typebox, daemon=True).start()
            elif cmd == "circle":                                 # SUPER+SHIFT+C: circle to ask
                threading.Thread(target=a.circle_start, daemon=True).start()
            elif cmd == "buddytick":                              # test hook: run the reminder/promise check now
                threading.Thread(target=a.buddy_tick, daemon=True).start()
            elif cmd == "brainwarm":
                threading.Thread(target=a.brain.warm, daemon=True).start()
            elif cmd == "toggle":                                 # SUPER+SHIFT+J: corner orb <-> full screen
                threading.Thread(target=opener.toggle_orb, daemon=True).start()
            elif cmd == "back":                                   # SUPER+ALT+J: focus the Jarvis window again
                threading.Thread(target=a.run_open, args=({"action": "focus_jarvis", "label": "Jarvis"},), daemon=True).start()
            elif cmd.startswith("ask "):                          # dev/test: a text request, as if it had been transcribed
                threading.Thread(target=a.ask_text, args=(cmd[4:],), daemon=True).start()
            elif cmd.startswith("testlook ") and os.environ.get("JARVIS_NO_DESKTOP"):      # tests only (the throwaway server): pretend the camera view is open or closed
                a.look["open"] = cmd.split()[1] == "open"
                if a.look["open"]:
                    a.look.setdefault("boxes", [])
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
            if not self._origin_ok(req):                              # EVERY route: only this page on 127.0.0.1 (a web page on another site, or a rebinding trick, gets nothing)
                raise web.HTTPForbidden()
            resp = await handler(req)
            resp.headers.setdefault("Cache-Control", "no-store")
            return resp
        app = web.Application(middlewares=[nocache])
        app.router.add_get("/", self.index)
        app.router.add_get("/api/idle", self.idle_api)
        app.router.add_get("/api/audio", self.audio_api)
        app.router.add_get("/api/connections", self.conn_api)
        app.router.add_get("/api/wifi", self.wifi_api)
        app.router.add_post("/api/wifi/connect", self.wifi_connect)
        app.router.add_post("/api/connections/{id}/{op}", self.conn_act)
        app.router.add_post("/api/holo/export", self.holo_export_api)
        app.router.add_get("/api/markets/pulse", self.mkt_pulse_api)
        app.router.add_get("/api/markets/chart", self.mkt_chart_api)
        app.router.add_get("/api/model/{name}", self.model_file)
        app.router.add_get("/api/popup", self.popup_api)
        app.router.add_get("/api/windows", self.windows_api)
        app.router.add_get("/api/geosuggest", self.geosuggest_api)
        app.router.add_get("/api/look/frame", self.look_frame_api)
        app.router.add_get("/api/look/stream", self.look_stream_api)
        app.router.add_get("/lookmem/{name}", self.lookmem_file)
        app.router.add_get("/term/ws", self.term_ws)
        app.router.add_get("/api/files", self.files_api)
        app.router.add_get("/api/launcher", self.launcher_api)
        app.router.add_get("/api/files/thumb", self.files_thumb)
        app.router.add_get("/map", self.map_page)
        app.router.add_get("/api/map/{id}", self.map_api)
        app.router.add_post("/hook/{token}", self.hook_api)
        app.router.add_get("/api/mapstyle", self.map_style)
        app.router.add_get("/api/tc/{src}/{path:.*}", self.tile_proxy)
        app.router.add_post("/api/map/{id}/mode", self.map_mode)
        app.router.add_post("/api/map/{id}/goto", self.map_goto)
        app.router.add_post("/api/map/{id}/alt", self.map_alt)
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
        from . import awake
        awake.start()                                                  # no screen saver / lock / idle while Jarvis is open; released when it closes
        import atexit
        atexit.register(awake.stop)
        await asyncio.start_unix_server(self.control, path=str(config.SOCK))
        self.assistant.preload()
        asyncio.ensure_future(self.thermal_loop())
        asyncio.ensure_future(self.health_loop())
        asyncio.ensure_future(self.watch_loop())
        asyncio.ensure_future(self.briefing_loop())
        asyncio.ensure_future(self.quiet_loop())
        asyncio.ensure_future(self.buddy_loop())
        asyncio.ensure_future(self.conn_warn_loop())
        asyncio.ensure_future(self.market_alert_loop())
        from . import news, weather
        if settings.get("desk_presence"):
            from . import presence
            threading.Timer(20, lambda: presence.start(self.assistant.presence_on_change, lambda: bool(self.assistant.look.get("open")))).start()
        from . import settingsio
        note = settingsio.ensure_schema()
        self._settings_problems = settingsio.problems()
        if note:
            print("[settings]", note, flush=True)
        news.start()                                                          # headlines refresh in the background every 12 minutes: "show the news" opens from memory
        threading.Thread(target=lambda: (time.sleep(25), weather.prefetch()), daemon=True).start()
        opener.set_notifier(lambda msg: self.loop.call_soon_threadsafe(self.bcast, msg))      # page messages (moods, embeds, prefs...) also reach a test server's page
        if os.environ.get("JARVIS_NO_DESKTOP") != "1":       # desktop side effects: the dock orb and a parked warm browser (tests set JARVIS_NO_DESKTOP=1)
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
        self.assistant.voice_set = self.voice_set
        desk.start_clip_watch()
        if mac.paired():
            companion.start(self.assistant)                                # the Mac can ask and hand over notes, links and the clipboard
        if prefs.get("phone_voice"):                                   # only if you switched it on in settings
            ok, msg = await voiceapi.start(self.assistant, self.on_phone_request)
            print("[phone voice]", "on" if ok else "could not start", flush=True)
        print(f"jarvis ui on http://127.0.0.1:{PORT}", flush=True)
        await self.mic_loop()


def main():
    config.harden()
    asyncio.run(Server().run())


if __name__ == "__main__":
    main()
