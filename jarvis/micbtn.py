"""The dictation mic button: a small round button on the overlay layer (above every window, never takes the keyboard focus, so the words are
typed into the window you were already using). HOLD it to dictate (release types the words), DRAG it anywhere (move more than 10 px before the 250 ms
hold fires): the spot is remembered in ~/.config/jarvis/micbtn.json. Run through bin/jarvis-micbtn (libgtk4-layer-shell preloaded)."""
import asyncio
import json
import math
import os
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
import aiohttp  # noqa: E402
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LS  # noqa: E402

PORT = int(os.environ.get("JARVIS_PORT", "8765"))
SIZE = 64
THEMES = {"jarvis": (0.24, 0.55, 1.0), "rose": (1.0, 0.17, 0.33)}


def cfg_path():
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "jarvis", "micbtn.json")


def load_pos():
    try:
        d = json.load(open(cfg_path()))
        return int(d["right"]), int(d["bottom"])
    except Exception:
        return 200, 40


def save_pos(r, b):
    try:
        os.makedirs(os.path.dirname(cfg_path()), exist_ok=True)
        json.dump({"right": int(r), "bottom": int(b)}, open(cfg_path(), "w"))
    except OSError:
        pass


class MicBtn(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="local.jarvis.micbtn")
        self.win = None
        self.theme, self.live = "jarvis", False
        self.right, self.bottom = load_pos()
        self.press = None
        self.dictating = False
        self.loop = self.ws = None

    def do_activate(self):
        if self.win:
            return
        w = self.win = Gtk.Window(application=self)
        w.set_default_size(SIZE, SIZE)
        LS.init_for_window(w)
        LS.set_namespace(w, "jarvis-micbtn")
        LS.set_layer(w, LS.Layer.OVERLAY)
        LS.set_anchor(w, LS.Edge.RIGHT, True)
        LS.set_anchor(w, LS.Edge.BOTTOM, True)
        LS.set_exclusive_zone(w, -1)
        LS.set_keyboard_mode(w, LS.KeyboardMode.NONE)
        self.margins()
        css = Gtk.CssProvider()
        css.load_from_data(b"window, window.background { background: transparent; }")
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        da = self.da = Gtk.DrawingArea()
        da.set_content_width(SIZE)
        da.set_content_height(SIZE)
        da.set_draw_func(self.draw)
        w.set_child(da)
        g = Gtk.GestureDrag()
        g.set_button(0)
        g.connect("drag-begin", self.on_begin)
        g.connect("drag-end", self.on_end)
        da.add_controller(g)
        m = Gtk.EventControllerMotion()
        m.connect("motion", self.on_motion)
        da.add_controller(m)
        w.present()
        self.hold()
        GLib.timeout_add(80, lambda: (self.da.queue_draw(), True)[1])
        threading.Thread(target=self.net, daemon=True).start()

    def margins(self):
        LS.set_margin(self.win, LS.Edge.RIGHT, self.right)
        LS.set_margin(self.win, LS.Edge.BOTTOM, self.bottom)

    def send(self, msg):
        if self.loop and self.ws is not None:
            asyncio.run_coroutine_threadsafe(self.ws.send_json(msg), self.loop)

    def on_begin(self, g, x, y):
        self.press = {"x": x, "y": y, "held": False, "drag": False}
        GLib.timeout_add(250, self.fire, self.press)

    def fire(self, p):
        if self.press is p and not p["drag"]:
            p["held"] = True
            self.send({"t": "dictate", "down": True})
            self.dictating = True
        return False

    def on_motion(self, c, x, y):
        p = self.press
        if not p:
            return
        if not p["drag"] and not p["held"] and math.hypot(x - p["x"], y - p["y"]) > 10:
            p["drag"], p["gx"], p["gy"] = True, x, y
        if p["drag"]:
            self.right = int(max(0, self.right - (x - p["gx"])))
            self.bottom = int(max(0, self.bottom - (y - p["gy"])))
            self.margins()

    def on_end(self, g, ox, oy):
        p, self.press = self.press, None
        if not p:
            return
        if p["drag"]:
            save_pos(self.right, self.bottom)
        elif p["held"] and self.dictating:
            self.send({"t": "dictate", "down": False})
            self.dictating = False

    def net(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.net_main())

    async def net_main(self):
        while True:
            try:
                async with aiohttp.ClientSession() as s, s.ws_connect(f"http://127.0.0.1:{PORT}/ws", headers={"Origin": f"http://127.0.0.1:{PORT}"}) as ws:
                    self.ws = ws
                    async for m in ws:
                        if m.type == aiohttp.WSMsgType.TEXT:
                            d = json.loads(m.data)
                            if d.get("t") == "hello" and d.get("persona") in THEMES:
                                self.theme = d["persona"]
                            elif d.get("t") == "persona" and d.get("v") in THEMES:
                                self.theme = d["v"]
                            elif d.get("t") == "state":
                                self.live = d.get("state") == "listening" and "dictation" in str(d.get("detail", ""))
            except Exception:
                pass
            self.ws = None
            await asyncio.sleep(1.5)

    def draw(self, da, cr, w, h):
        acc = (1.0, 0.25, 0.25) if (self.live or self.dictating) else THEMES[self.theme]
        cx = cy = SIZE / 2
        pulse = 0.5 + 0.5 * math.sin(time.time() * 6) if (self.live or self.dictating) else 0.0
        cr.set_source_rgba(0.02, 0.03, 0.07, 0.88)
        cr.arc(cx, cy, 25, 0, 2 * math.pi)
        cr.fill()
        cr.set_line_width(2.5 + pulse * 1.5)
        cr.set_source_rgba(*acc, 0.9)
        cr.arc(cx, cy, 25, 0, 2 * math.pi)
        cr.stroke()
        cr.set_source_rgba(0.93, 0.96, 1.0, 0.95)                  # a microphone: capsule, cradle, stem
        cr.set_line_width(2.2)
        cr.set_line_cap(1)
        cr.arc(cx, cy - 6, 5, math.pi, 2 * math.pi)
        cr.line_to(cx + 5, cy + 1)
        cr.arc(cx, cy + 1, 5, 0, math.pi)
        cr.close_path()
        cr.stroke()
        cr.arc(cx, cy + 1, 10, 0.15 * math.pi, 0.85 * math.pi)
        cr.stroke()
        cr.move_to(cx, cy + 11)
        cr.line_to(cx, cy + 16)
        cr.stroke()


if __name__ == "__main__":
    raise SystemExit(MicBtn().run([]))
