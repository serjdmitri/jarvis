"""The Jarvis orb: just the glowing ring and the word, a perfect circle on a transparent surface, always on top of every window
(including a fullscreen one) and on every workspace. It is a GTK4 layer-shell OVERLAY surface drawn with Cairo (no browser, no square,
no grid): Hyprland window rules cannot make a Chromium window transparent or keep it above a focused window, a layer surface can.

  hold the orb = talk (release sends)     quick tap = toggle listening     drag (move > 12 px before the hold fires) = move it, remembered
  while he listens / thinks / speaks the ring pulses and spins; his short reply fades in as small text to the left of the orb.
Run through bin/jarvis-orb (libgtk4-layer-shell must be preloaded). It talks to the Jarvis server over the same websocket as the page."""
import asyncio
import json
import math
import os
import socket
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
gi.require_version("Gtk4LayerShell", "1.0")
import aiohttp  # noqa: E402
import cairo  # noqa: E402
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402
from gi.repository import Gtk4LayerShell as LS  # noqa: E402

PORT = int(os.environ.get("JARVIS_PORT", "8765"))
ORB = 140                      # the circle's bounding box, px
TEXT_W = 210                   # room for his short reply, left of the orb
WIN_W, WIN_H = ORB + TEXT_W, ORB
R = 36.0                       # ring radius
THEMES = {
    "jarvis": {"acc": (0.24, 0.55, 1.0), "disc": (0.015, 0.025, 0.065), "word": "JARVIS", "white": (0.93, 0.96, 1.0)},
    "rose": {"acc": (1.0, 0.17, 0.33), "disc": (0.02, 0.008, 0.02), "word": "ROSE", "white": (1.0, 0.92, 0.95)},
}


def cfg_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "jarvis", "orb.json")


def screen_size():
    try:
        g = Gdk.Display.get_default().get_monitors().get_item(0).get_geometry()
        return g.width, g.height
    except Exception:
        return 900, 600


def default_pos():
    sw, sh = screen_size()
    return sw - 40 - ORB // 2, sh - 40 - ORB // 2


def load_pos():
    """-> orb CENTRE on the screen (logical px). Older files stored right/bottom margins."""
    try:
        d = json.load(open(cfg_path()))
        if "cx" in d:
            return int(d["cx"]), int(d["cy"])
        sw, sh = screen_size()
        return sw - int(d["right"]) - ORB // 2, sh - int(d["bottom"]) - ORB // 2
    except Exception:
        return default_pos()


def save_pos(cx, cy):
    try:
        os.makedirs(os.path.dirname(cfg_path()), exist_ok=True)
        json.dump({"cx": int(cx), "cy": int(cy)}, open(cfg_path(), "w"))
    except OSError:
        pass


CRACK = 190          # keep the orb out of this square at the top-right corner (the cracked one)
EDGE = 16            # gap kept from every screen edge
SNAP = 36            # within this distance of an edge, snap to the edge gap
HOLD_TALK_S = 0.25   # a press this long starts hold-to-talk
HOLD_MOVE_S = 0.5    # ... and at this length the orb gives a small pulse: now a drag moves it (before that, movement is ignored)
BTN_R = 13           # the small round expand button
BTN_SHOW_S = 4.0     # how long it stays after a tap (always while hovering)


def avoid_corner():
    """"top-right" (default: the cracked corner), "top-left", "bottom-left", "bottom-right" or None. Read from the settings file each time, so a change applies at once."""
    try:
        v = json.load(open(os.path.join(os.path.dirname(cfg_path()), "settings.json"))).get("orb_avoid", "top-right")
    except Exception:
        v = "top-right"
    return v if v in ("top-right", "top-left", "bottom-left", "bottom-right") else None


STEP = 170           # one "move left/right/up/down"


def target(op, cx, cy):
    """Where "move to the top / middle / corner / left / right / up / down" or "reset" puts the orb (centre, px), before snapping."""
    sw, sh = screen_size()
    h = ORB // 2
    dx, dy = default_pos()
    return {"corner": (dx, dy), "reset": (dx, dy), "top": (cx, EDGE + h), "bottom": (cx, sh - EDGE - h), "middle": (sw // 2, sh // 2), "left": (EDGE + h, cy), "right": (sw - EDGE - h, cy),
            "up": (cx, cy - STEP), "down": (cx, cy + STEP), "leftstep": (cx - STEP, cy), "rightstep": (cx + STEP, cy)}.get(op, (cx, cy))


def snap(cx, cy):
    """Gently settle a dropped orb: inside the screen, snapped to the edge gap near an edge, and out of the cracked top-right corner."""
    sw, sh = screen_size()
    h = ORB // 2
    lo_x, hi_x, lo_y, hi_y = EDGE + h, sw - EDGE - h, EDGE + h, sh - EDGE - h
    cx, cy = min(max(cx, lo_x), hi_x), min(max(cy, lo_y), hi_y)
    if cx - lo_x < SNAP:
        cx = lo_x
    if hi_x - cx < SNAP:
        cx = hi_x
    if cy - lo_y < SNAP:
        cy = lo_y
    if hi_y - cy < SNAP:
        cy = hi_y
    corner = avoid_corner()
    if corner:                                               # inside the corner to keep clear: slide out of it (along the shorter way)
        left, top = "left" in corner, "top" in corner
        x_in = cx < CRACK if left else cx > sw - CRACK
        y_in = cy < CRACK if top else cy > sh - CRACK
        if x_in and y_in:
            dx = (CRACK - cx) if left else (cx - (sw - CRACK))
            dy = (CRACK - cy) if top else (cy - (sh - CRACK))
            if dy <= dx:
                cy = CRACK if top else sh - CRACK
            else:
                cx = CRACK if left else sw - CRACK
    return int(cx), int(cy)


def hypr(cmd):
    try:
        c = socket.socket(socket.AF_UNIX)
        c.settimeout(1)
        c.connect(f"{os.environ['XDG_RUNTIME_DIR']}/hypr/{os.environ['HYPRLAND_INSTANCE_SIGNATURE']}/.socket.sock")
        c.sendall(cmd.encode())
        out = b""
        while True:
            ch = c.recv(65536)
            if not ch:
                break
            out += ch
        c.close()
        return json.loads(out.decode())
    except Exception:
        return None


class Orb(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="local.jarvis.orb")
        self.win = None
        self.theme, self.mode, self.muted = "jarvis", "idle", False
        self.pers = {}                                         # persona id -> {"base", "accent", "word"} (from the server)
        self.level = 0.0            # smoothed audio level 0..1
        self.target = 0.0
        self.spin, self.t0 = 0.0, time.time()
        self.text, self.text_t, self.text_dur = "", 0.0, 0.0
        self.cx, self.cy = load_pos()               # the orb's centre on the screen
        self.flip = self.cx < screen_size()[0] / 2  # orb at the left end of the surface (text to its right) when it sits in the left half
        self.press = None           # gesture state
        self.holding = False        # a talk is in progress (hold or toggled by a tap)
        self.pulse_t = 0.0          # when the "you can drag now" pulse fired
        self.btn_until = 0.0        # the expand button is shown until then (a tap), and always while hovering
        self.hover = False
        self.on_kiosk, self.embed = False, False
        self.loop = self.ws = None
        self.tick_n = 0
        self._cache = {}

    # ------------------------------------------------------------------ geometry
    def orb_xy(self):
        """Orb centre inside the surface."""
        return (ORB / 2 if self.flip else WIN_W - ORB / 2), WIN_H / 2

    def btn_xy(self):
        """The expand button: diagonal, on the side facing the screen centre, just outside the rings."""
        ox, oy = self.orb_xy()
        return ox + (52 if self.flip else -52), oy - 52

    def btn_visible(self):
        return self.hover or time.time() < self.btn_until

    def apply_position(self):
        left = self.cx - ORB / 2 if self.flip else self.cx + ORB / 2 - WIN_W
        LS.set_margin(self.win, LS.Edge.LEFT, int(max(0, left)))
        LS.set_margin(self.win, LS.Edge.TOP, int(max(0, self.cy - WIN_H / 2)))

    def set_input_region(self):
        """Only the orb and (while it shows) the expand button take the pointer; the rest of the surface lets clicks through."""
        surf = self.win.get_surface()
        if surf:
            ox, oy = self.orb_xy()
            reg = cairo.Region(cairo.RectangleInt(int(ox - ORB / 2), 0, ORB, ORB))
            bx, by = self.btn_xy()
            reg.union_rectangle(cairo.RectangleInt(int(bx - BTN_R - 4), int(by - BTN_R - 4), 2 * BTN_R + 8, 2 * BTN_R + 8))
            surf.set_input_region(reg)
        return False

    # ------------------------------------------------------------------ window
    def do_activate(self):
        if self.win:
            return
        w = self.win = Gtk.Window(application=self)
        w.set_default_size(WIN_W, WIN_H)
        LS.init_for_window(w)
        LS.set_namespace(w, "jarvis-orb")
        LS.set_layer(w, LS.Layer.OVERLAY)
        LS.set_anchor(w, LS.Edge.LEFT, True)
        LS.set_anchor(w, LS.Edge.TOP, True)
        LS.set_exclusive_zone(w, -1)                      # never pushes other windows around
        LS.set_keyboard_mode(w, LS.KeyboardMode.NONE)     # never steals the keyboard
        self.cx, self.cy = snap(self.cx, self.cy)
        self.apply_position()
        css = Gtk.CssProvider()
        css.load_from_data(b"window, window.background { background: transparent; }")
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        da = self.da = Gtk.DrawingArea()
        da.set_content_width(WIN_W)
        da.set_content_height(WIN_H)
        da.set_draw_func(self.draw)
        w.set_child(da)
        drag = Gtk.GestureDrag()
        drag.set_button(0)
        drag.connect("drag-begin", self.on_begin)
        drag.connect("drag-end", self.on_end)
        da.add_controller(drag)
        mot = Gtk.EventControllerMotion()
        mot.connect("motion", self.on_motion)
        mot.connect("enter", lambda *_: setattr(self, "hover", True) or self.set_input_region())
        mot.connect("leave", lambda *_: setattr(self, "hover", False))
        da.add_controller(mot)
        w.connect("map", lambda *_: GLib.timeout_add(150, self.set_input_region))
        w.present()
        self.hold()
        GLib.timeout_add(66, self.tick)                   # ~15 fps while active (it was 25: the orb was the top CPU user)
        GLib.timeout_add(500, self.watch_workspace)
        threading.Thread(target=self.net, daemon=True).start()

    def watch_workspace(self):
        """In full screen (Jarvis's own workspace, nothing open in the frame) the page has its own central orb, so this corner orb steps aside."""
        act, cl = hypr("j/activeworkspace"), hypr("j/clients")
        if isinstance(act, dict) and isinstance(cl, list):
            k = next((c for c in cl if c.get("class") == "jarvis-ui"), None)
            self.on_kiosk = bool(k and k["workspace"]["id"] == act.get("id"))
        show = not (self.on_kiosk and not self.embed)
        if show != self.win.get_visible():
            self.win.set_visible(show)
            if show:
                GLib.timeout_add(200, self.set_input_region)
        return True

    # ------------------------------------------------------------------ input
    def in_button(self, x, y):
        if not self.btn_visible():
            return False
        bx, by = self.btn_xy()
        return math.hypot(x - bx, y - by) <= BTN_R + 4

    def on_begin(self, g, x, y):
        p = self.press = {"x": x, "y": y, "t": time.time(), "talk": False, "drag": False, "btn": self.in_button(x, y), "gx": x, "gy": y}
        if not p["btn"]:
            GLib.timeout_add(int(HOLD_TALK_S * 1000), self.fire_talk, p)
            GLib.timeout_add(int(HOLD_MOVE_S * 1000), self.fire_pulse, p)

    def fire_talk(self, p):
        if self.press is p and not p["drag"]:
            p["talk"] = True
            if not self.holding:
                self.send({"t": "talk", "down": True, "look": False})
                self.holding = True
        return False

    def fire_pulse(self, p):
        """Half a second of holding: a small pulse = "you can drag it now". Staying still keeps it a hold-to-talk."""
        if self.press is p and not p["drag"]:
            self.pulse_t = time.time()
        return False

    def on_motion(self, c, x, y):
        p = self.press
        if not p or p["btn"]:
            return
        if not p["drag"] and time.time() - p["t"] >= HOLD_MOVE_S and math.hypot(x - p["x"], y - p["y"]) > 8:
            p["drag"], p["gx"], p["gy"] = True, x, y
            if self.holding:                              # it was a move, not a talk: throw the recording away
                self.send({"t": "talk", "down": False, "cancel": True})
                self.holding = False
        if p["drag"]:
            self.cx += x - p["gx"]
            self.cy += y - p["gy"]
            sw, sh = screen_size()
            self.cx, self.cy = min(max(self.cx, 0), sw), min(max(self.cy, 0), sh)
            self.apply_position()

    def on_end(self, g, ox, oy):
        p, self.press = self.press, None
        if not p:
            return
        if p["btn"]:
            if self.in_button(p["x"], p["y"]):
                self.send({"t": "action", "name": "toggle_orb", "payload": {}})     # expand to full screen
            return
        if p["drag"]:
            self.cx, self.cy = snap(self.cx, self.cy)
            self.flip = self.cx < screen_size()[0] / 2
            self.apply_position()
            GLib.timeout_add(100, self.set_input_region)
            save_pos(self.cx, self.cy)
        elif p["talk"]:
            if self.holding:
                self.send({"t": "talk", "down": False})
                self.holding = False
        else:                                              # a quick tap toggles listening, and shows the expand button for a few seconds
            self.send({"t": "talk", "down": self.holding is False, "look": False})
            self.holding = not self.holding
            self.btn_until = time.time() + BTN_SHOW_S
            self.set_input_region()

    # ------------------------------------------------------------------ server link
    def send(self, msg):
        if self.loop and self.ws is not None:
            asyncio.run_coroutine_threadsafe(self.ws.send_json(msg), self.loop)

    def net(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.net_main())

    async def net_main(self):
        url = f"http://127.0.0.1:{PORT}/ws"
        while True:
            try:
                async with aiohttp.ClientSession() as s, s.ws_connect(url, headers={"Origin": f"http://127.0.0.1:{PORT}"}) as ws:
                    self.ws = ws
                    async for m in ws:
                        if m.type == aiohttp.WSMsgType.TEXT:
                            GLib.idle_add(self.on_msg, json.loads(m.data))
            except Exception:
                pass
            self.ws = None
            GLib.idle_add(self.on_msg, {"t": "state", "state": "offline"})
            await asyncio.sleep(1.5)

    def on_msg(self, m):
        t = m.get("t")
        if t == "hello":
            self.mode = m.get("state", "idle")
            self.pers = {p["id"]: p for p in m.get("personas", []) if isinstance(p, dict) and "id" in p}
            self.theme = m.get("persona") if (m.get("persona") in THEMES or m.get("persona") in self.pers) else self.theme
            self.muted = bool(m.get("muted"))
        elif t == "state":
            self.mode = m.get("state", "idle")
            if self.mode != "listening":
                self.holding = self.holding and self.press is not None
        elif t == "level":
            if (m.get("src") == "mic" and self.mode == "listening") or (m.get("src") == "out" and self.mode == "speaking"):
                self.target = max(self.target * 0.5, float(m.get("v", 0)))
        elif t == "persona":
            if m.get("v") in THEMES or m.get("v") in self.pers:
                self.theme = m["v"]
        elif t == "orb" and self.win:                      # voice / settings: "move to the top", "reset the orb"
            self.cx, self.cy = snap(*target(str(m.get("op")), self.cx, self.cy))
            self.flip = self.cx < screen_size()[0] / 2
            self.apply_position()
            GLib.timeout_add(100, self.set_input_region)
            if m.get("op") == "reset":
                try:
                    os.unlink(cfg_path())
                except OSError:
                    pass
            else:
                save_pos(self.cx, self.cy)
        elif t == "muted":
            self.muted = bool(m.get("on"))
        elif t == "embed":
            self.embed = m.get("kind") not in (None, "close", "full")
        elif t == "caption":                               # dictation: the words he heard, live, then they fade
            txt = (m.get("text") or "").strip()
            self.text, self.text_t = txt, time.time()
            self.text_dur = 60.0 if m.get("live") else 3.0
        elif t == "msg" and m.get("role") == "jarvis":
            txt = (m.get("text") or "").strip()
            if txt:
                self.text, self.text_t = txt, time.time()
                self.text_dur = 4.0 + min(8.0, len(txt) * 0.05)
        return False

    # ------------------------------------------------------------------ animation + drawing
    def tick(self):
        now = time.time()
        dt = 0.066
        self.tick_n += 1
        rate = {"listening": 1.4, "thinking": 3.2, "speaking": 0.9}.get(self.mode, 0.25)
        self.spin = (self.spin + rate * dt * (1 + self.level)) % (2 * math.pi)
        self.target *= 0.86
        self.level += (self.target - self.level) * 0.35
        busy = (self.mode in ("listening", "thinking", "speaking") or (self.text and now - self.text_t < self.text_dur + 1)
                or now - self.pulse_t < 0.6 or self.btn_visible() or self.press is not None)
        if busy or self.tick_n % 4 == 0:                   # idle: ~4 fps is enough for the slow breathing and spin
            self.da.queue_draw()
        return True

    def look(self, name):
        """The drawn look of a persona: its base look (jarvis or rose) with the persona's colour and name."""
        p = self.pers.get(name) or {}
        th = dict(THEMES.get(p.get("base") or name, THEMES["jarvis"]))
        a = str(p.get("accent") or "")
        if len(a) == 7 and a[0] == "#":
            try:
                th["acc"] = tuple(int(a[i:i + 2], 16) / 255 for i in (1, 3, 5))
            except ValueError:
                pass
        if p.get("word"):
            th["word"] = str(p["word"])[:12]
        return th

    def static_layer(self, th_name, dim, w, h, ox, oy):
        """Glow, dark disc, rings and the word never change while idle: draw them once into a surface and just paint it."""
        key = (th_name, dim, ox)
        if key in self._cache:
            return self._cache[key]
        th = self.look(th_name)
        acc, white, disc = th["acc"], th["white"], th["disc"]
        sf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(sf)
        cr.translate(ox, oy)
        g = cairo.RadialGradient(0, 0, R * 0.8, 0, 0, R * 1.95)
        g.add_color_stop_rgba(0, *acc, 0.40 * dim)
        g.add_color_stop_rgba(1, *acc, 0)
        cr.set_source(g)
        cr.arc(0, 0, R * 1.95, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgba(*disc, 1)
        cr.arc(0, 0, R - 3, 0, 2 * math.pi)
        cr.fill()
        cr.set_line_width(5)
        cr.set_source_rgba(*acc, 0.95 * dim)
        cr.arc(0, 0, R, 0, 2 * math.pi)
        cr.stroke()
        cr.set_line_width(1.6)
        cr.set_source_rgba(*white, 0.9 * dim)
        cr.arc(0, 0, R - 5.5, 0, 2 * math.pi)
        cr.stroke()
        cr.set_line_width(1)
        cr.set_source_rgba(*acc, 0.40 * dim)
        cr.arc(0, 0, R + 7, 0, 2 * math.pi)
        cr.stroke()
        lay = PangoCairo.create_layout(cr)
        if th_name == "rose":
            lay.set_font_description(Pango.FontDescription("Noto Sans Black Italic 15"))
            cr.rotate(-0.05)
        else:
            lay.set_font_description(Pango.FontDescription("Noto Sans Medium 8.5"))
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_letter_spacing_new(int(1.6 * Pango.SCALE)))
            lay.set_attributes(attrs)
        lay.set_text(th["word"], -1)
        tw, thh = lay.get_pixel_size()
        cr.move_to(-tw / 2, -thh / 2)
        cr.set_source_rgba(*white, dim)
        PangoCairo.show_layout(cr, lay)
        self._cache[key] = sf
        return sf

    def draw(self, da, cr, w, h):
        th = self.look(self.theme)
        acc, white = th["acc"], th["white"]
        now = time.time()
        cx, cy = self.orb_xy()
        calm = self.mode in ("idle", "offline")
        dim = 0.45 if (self.muted or self.mode == "offline") else 1.0
        pulse = math.sin(now * 1.6) * 0.5 + 0.5
        k = 1.0 if calm else 1.0 + 0.05 * self.level + 0.015 * math.sin(now * 7 if self.mode == "thinking" else now * 3)
        cr.save()
        if k != 1.0:
            cr.translate(cx, cy); cr.scale(k, k); cr.translate(-cx, -cy)
        cr.set_source_surface(self.static_layer(self.theme, dim, w, h, cx, cy), 0, 0)
        cr.paint()
        cr.restore()
        extra = (self.level * 0.45) if not calm else pulse * 0.10
        if extra > 0.01:
            g = cairo.RadialGradient(cx, cy, R * 0.9, cx, cy, R * 1.9)
            g.add_color_stop_rgba(0, *acc, extra * dim)
            g.add_color_stop_rgba(1, *acc, 0)
            cr.set_source(g)
            cr.arc(cx, cy, R * 1.9, 0, 2 * math.pi)
            cr.fill()
        sweep = {"thinking": 1.9, "listening": 1.2, "speaking": 0.9}.get(self.mode, 0.7)
        cr.save()
        cr.translate(cx, cy)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_width(2.6)
        cr.set_source_rgba(*white, (0.9 if not calm else 0.55) * dim)
        cr.arc(0, 0, (R + 12) * k, self.spin, self.spin + sweep)
        cr.stroke()
        cr.set_line_width(1.2)
        cr.set_source_rgba(*acc, 0.7 * dim)
        cr.arc(0, 0, (R + 17) * k, -self.spin * 0.7 + 2.5, -self.spin * 0.7 + 2.5 + sweep * 0.6)
        cr.stroke()
        cr.restore()
        pa = now - self.pulse_t                              # the small pulse that says "you can drag it now"
        if 0 <= pa < 0.5:
            f = pa / 0.5
            cr.set_line_width(3 * (1 - f))
            cr.set_source_rgba(*white, 0.9 * (1 - f))
            cr.arc(cx, cy, R + 6 + 26 * f, 0, 2 * math.pi)
            cr.stroke()
        if self.muted:
            cr.set_line_width(2)
            cr.set_source_rgba(*white, 0.8)
            cr.move_to(cx - R * 0.7, cy + R * 0.7)
            cr.line_to(cx + R * 0.7, cy - R * 0.7)
            cr.stroke()
        if self.btn_visible():                               # the small round "expand" button (part of this same round window)
            bx, by = self.btn_xy()
            cr.set_source_rgba(0.02, 0.03, 0.07, 0.92)
            cr.arc(bx, by, BTN_R, 0, 2 * math.pi)
            cr.fill()
            cr.set_line_width(1.6)
            cr.set_source_rgba(*acc, 0.95)
            cr.arc(bx, by, BTN_R, 0, 2 * math.pi)
            cr.stroke()
            cr.set_source_rgba(*white, 0.95)                 # four corner arrows = expand
            cr.set_line_width(1.6)
            for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                cr.move_to(bx + sx * 3, by + sy * 6.5); cr.line_to(bx + sx * 6.5, by + sy * 6.5); cr.line_to(bx + sx * 6.5, by + sy * 3)
                cr.stroke()
        if self.text:                                        # his short reply, beside the orb, fading out
            age = now - self.text_t
            alpha = 1.0 if age < self.text_dur else max(0.0, 1 - (age - self.text_dur) / 0.8)
            if alpha > 0:
                tl = PangoCairo.create_layout(cr)
                tl.set_font_description(Pango.FontDescription("Noto Sans 9"))
                tl.set_width((TEXT_W - 10) * Pango.SCALE)
                tl.set_wrap(Pango.WrapMode.WORD_CHAR)
                tl.set_ellipsize(Pango.EllipsizeMode.END)
                tl.set_height(-3)
                tl.set_alignment(Pango.Alignment.LEFT if self.flip else Pango.Alignment.RIGHT)
                tl.set_text(self.text, -1)
                _, tth = tl.get_pixel_size()
                x, y = (ORB + 8 if self.flip else 2), (h - tth) / 2
                for (dx, dy, c, a) in ((1, 1, (0, 0, 0), 0.7), (0, 0, white, 1.0)):
                    cr.move_to(x + dx, y + dy)
                    cr.set_source_rgba(*c, a * alpha * 0.95)
                    PangoCairo.show_layout(cr, tl)
            else:
                self.text = ""


if __name__ == "__main__":
    raise SystemExit(Orb().run([]))
