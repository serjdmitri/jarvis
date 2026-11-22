"""Things Jarvis draws ON your real screen, in a transparent GTK4 layer-shell overlay above every window. Three modes (run through bin/jarvis-screenfx):

  point  X Y W H "caption"   a glowing ring / reticle around a box (normalised 0-1 coordinates) with a small caption. Click-through: you still click the real button.
                             Used by "Show me how". It never clicks or types for you: it only points.
  draw                       captures ONE finger/mouse stroke ("circle to ask"), prints the box around it as one JSON line {"x","y","w","h"} (normalised) and exits;
                             a tap without dragging prints "cancel". Closes after 25 s of nothing.
  bubble X Y W H "text"      the answer, in a small card placed next to the region. Only the card takes clicks (the x closes it); everything else is click-through.

Animations run at 30 fps at most, only while a window is open. Run: LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so python -m jarvis.screenfx point 0.4 0.3 0.1 0.05 "Click here"."""
import json
import math
import signal
import subprocess
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
gi.require_version("Gtk4LayerShell", "1.0")
import cairo  # noqa: E402
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402
from gi.repository import Gtk4LayerShell as LS  # noqa: E402

ACC = {"jarvis": (0.24, 0.55, 1.0), "rose": (1.0, 0.17, 0.33)}
LIFETIME = {"point": 240, "draw": 25, "bubble": 90}


def monitor():
    """The focused monitor's logical size in px."""
    try:
        mons = json.loads(subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=1.5).stdout)
        m = next((m for m in mons if m.get("focused")), mons[0])
        sc = m.get("scale") or 1
        return int(m["width"] / sc), int(m["height"] / sc)
    except Exception:
        return 1280, 800


def clamp_box(x, y, w, h):
    x, y = max(0.0, min(1.0, x)), max(0.0, min(1.0, y))
    return x, y, max(0.0, min(1.0 - x, w)), max(0.0, min(1.0 - y, h))


def place_card(box, sw, sh, cw, ch, gap=16):
    """Where a card of size cw x ch goes beside a region (px): right of it if there is room, else left, else below/above; always on screen."""
    x, y, w, h = box[0] * sw, box[1] * sh, box[2] * sw, box[3] * sh
    if x + w + gap + cw <= sw:
        px, py = x + w + gap, y
    elif x - gap - cw >= 0:
        px, py = x - gap - cw, y
    elif y + h + gap + ch <= sh:
        px, py = x, y + h + gap
    else:
        px, py = x, y - gap - ch
    return max(8, min(sw - cw - 8, px)), max(8, min(sh - ch - 8, py))


class Fx(Gtk.Application):
    def __init__(self, mode, args, persona="jarvis"):
        super().__init__(application_id=f"dev.jarvis.screenfx.{mode}")
        self.mode, self.args, self.persona = mode, args, persona
        self.win = self.da = None
        self.t0 = time.time()
        self.stroke = []
        self.sw, self.sh = monitor()
        self.card_rect = None
        self.geo = None                                  # (x, y, w, h) of the small window in screen px, for point and bubble

    # ------------------------------------------------------------------ window
    def plan(self):
        """The small rectangle (screen px) each overlay needs, so the compositor and Cairo only handle that, not the whole screen."""
        sw, sh = self.sw, self.sh
        if self.mode == "point":
            x, y, bw, bh = clamp_box(*[float(v) for v in self.args[:4]])
            cx, cy = (x + bw / 2) * sw, (y + bh / 2) * sh
            r = max(26.0, max(bw * sw, bh * sh) / 2 + 14)
            half = r + 48
            above = cy >= sh * 0.62
            x0, x1 = max(0, min(sw - 400, cx - 200)), 0
            y0 = max(0, cy - half - (130 if above else 0))
            y1 = min(sh, cy + half + (0 if above else 130))
            return int(x0), int(y0), int(min(400, sw - x0)), int(y1 - y0)
        if self.mode == "bubble":
            x, y, bw, bh = clamp_box(*[float(v) for v in self.args[:4]])
            text = str(self.args[4])[:420]
            sf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8)
            lay = PangoCairo.create_layout(cairo.Context(sf))
            lay.set_font_description(Pango.FontDescription("Noto Sans 12"))
            cw = min(360, sw * 0.45)
            lay.set_width(int((cw - 40) * Pango.SCALE))
            lay.set_wrap(Pango.WrapMode.WORD_CHAR)
            lay.set_text(text, -1)
            ch = lay.get_pixel_size()[1] + 28
            px, py = place_card((x, y, bw, bh), sw, sh, cw, ch)
            ux0, uy0 = max(0, min(x * sw, px) - 6), max(0, min(y * sh, py) - 6)
            ux1, uy1 = min(sw, max((x + bw) * sw, px + cw) + 6), min(sh, max((y + bh) * sh, py + ch) + 6)
            return int(ux0), int(uy0), int(ux1 - ux0), int(uy1 - uy0)
        return None

    def do_activate(self):
        if self.win:
            return
        w = self.win = Gtk.Window(application=self)
        LS.init_for_window(w)
        LS.set_namespace(w, "jarvis-fx-" + self.mode)
        LS.set_layer(w, LS.Layer.OVERLAY)
        self.geo = self.plan()
        if self.geo:
            gx, gy, gw, gh = self.geo
            w.set_default_size(gw, gh)
            LS.set_anchor(w, LS.Edge.LEFT, True)
            LS.set_anchor(w, LS.Edge.TOP, True)
            LS.set_margin(w, LS.Edge.LEFT, gx)
            LS.set_margin(w, LS.Edge.TOP, gy)
        else:
            for e in (LS.Edge.LEFT, LS.Edge.RIGHT, LS.Edge.TOP, LS.Edge.BOTTOM):
                LS.set_anchor(w, e, True)
        LS.set_exclusive_zone(w, -1)
        LS.set_keyboard_mode(w, LS.KeyboardMode.NONE)
        css = Gtk.CssProvider()
        css.load_from_data(b"window, window.background { background: transparent; }")
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        da = self.da = Gtk.DrawingArea()
        if self.geo:
            da.set_content_width(self.geo[2])
            da.set_content_height(self.geo[3])
        else:
            da.set_hexpand(True)
            da.set_vexpand(True)
        da.set_draw_func(self.draw)
        w.set_child(da)
        if self.mode == "draw":
            g = Gtk.GestureDrag()
            g.set_button(0)
            g.connect("drag-begin", lambda g_, x, y: self.stroke.clear() or self.stroke.append((x, y)) or self.da.queue_draw())
            g.connect("drag-update", self.on_drag)
            g.connect("drag-end", self.on_end)
            da.add_controller(g)
        elif self.mode == "bubble":
            c = Gtk.GestureClick()
            c.connect("pressed", self.on_bubble_click)
            da.add_controller(c)
        w.connect("map", lambda *_: GLib.timeout_add(120, self.set_input))
        w.present()
        GLib.timeout_add(50 if self.mode == "point" else 1000, self.tick)          # point: 20 fps on a small surface; the others only check their lifetime once a second
        signal.signal(signal.SIGTERM, lambda *_: self.quit())
        self.hold()

    def set_input(self):
        surf = self.win.get_surface()
        if not surf:
            return False
        if self.mode == "point":
            surf.set_input_region(cairo.Region())                 # nothing takes the pointer: you click the real button underneath
        elif self.mode == "bubble" and self.card_rect:
            x, y, w, h = self.card_rect
            gx, gy = self.geo[0], self.geo[1]
            surf.set_input_region(cairo.Region(cairo.RectangleInt(int(x - gx), int(y - gy), int(w), int(h))))
        return False

    def tick(self):
        if time.time() - self.t0 > LIFETIME[self.mode]:
            if self.mode == "draw":
                print("cancel", flush=True)
            self.quit()
            return False
        if self.mode == "point":
            self.da.queue_draw()
        return True

    # ------------------------------------------------------------------ drawing
    def accent(self):
        return ACC.get(self.persona, ACC["jarvis"])

    def draw(self, da, cr, w, h):
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        if self.geo:
            cr.translate(-self.geo[0], -self.geo[1])                 # from here on: screen coordinates
            w, h = self.sw, self.sh
        if self.mode == "point":
            self.draw_point(cr, w, h)
        elif self.mode == "draw":
            self.draw_stroke(cr, w, h)
        elif self.mode == "bubble":
            self.draw_bubble(cr, w, h)

    def draw_point(self, cr, w, h):
        x, y, bw, bh = clamp_box(*[float(v) for v in self.args[:4]])
        cx, cy = (x + bw / 2) * w, (y + bh / 2) * h
        r = max(26.0, max(bw * w, bh * h) / 2 + 14)
        t = time.time() - self.t0
        a = self.accent()
        pulse = 0.5 + 0.5 * math.sin(t * 4.2)
        ripple = (t * 0.9) % 1.0
        for width, alpha in ((10, 0.10), (6, 0.18), (3, 0.9)):                 # glow: wide faint strokes under the bright one
            cr.set_source_rgba(a[0], a[1], a[2], alpha * (0.7 + 0.3 * pulse))
            cr.set_line_width(width)
            cr.arc(cx, cy, r, 0, 2 * math.pi)
            cr.stroke()
        cr.set_source_rgba(a[0], a[1], a[2], 0.5 * (1 - ripple))               # an expanding ripple
        cr.set_line_width(2)
        cr.arc(cx, cy, r + ripple * 26, 0, 2 * math.pi)
        cr.stroke()
        cr.set_source_rgba(1, 1, 1, 0.9)                                        # reticle ticks
        cr.set_line_width(2)
        for k in range(4):
            ang = k * math.pi / 2 + math.pi / 4
            cr.move_to(cx + math.cos(ang) * (r - 6), cy + math.sin(ang) * (r - 6))
            cr.line_to(cx + math.cos(ang) * (r + 8), cy + math.sin(ang) * (r + 8))
        cr.stroke()
        if len(self.args) > 4 and self.args[4]:
            self.caption(cr, str(self.args[4]), cx, cy + r + 14 if cy < h * 0.62 else cy - r - 14, w, h, above=cy >= h * 0.62)

    def caption(self, cr, text, cx, y, w, h, above=False):
        lay = PangoCairo.create_layout(cr)
        lay.set_font_description(Pango.FontDescription("Noto Sans Medium 12"))
        lay.set_width(int(min(340, w * 0.6) * Pango.SCALE))
        lay.set_wrap(Pango.WrapMode.WORD_CHAR)
        lay.set_alignment(Pango.Alignment.CENTER)
        lay.set_text(text[:160], -1)
        tw, th = lay.get_pixel_size()
        bx = max(8, min(w - tw - 24, cx - tw / 2 - 8))
        by = y - th - 16 if above else y
        by = max(8, min(h - th - 20, by))
        a = self.accent()
        cr.set_source_rgba(0.02, 0.04, 0.1, 0.9)
        self.rounded(cr, bx, by, tw + 16, th + 12, 8)
        cr.fill_preserve()
        cr.set_source_rgba(a[0], a[1], a[2], 0.9)
        cr.set_line_width(1.2)
        cr.stroke()
        cr.set_source_rgba(0.93, 0.96, 1, 1)
        cr.move_to(bx + 8, by + 6)
        PangoCairo.show_layout(cr, lay)

    @staticmethod
    def rounded(cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    # ------------------------------------------------------------------ draw mode
    def on_drag(self, g, dx, dy):
        ok, sx, sy = g.get_start_point()
        self.stroke.append((sx + dx, sy + dy))
        self.t0 = time.time()                                                   # still drawing: do not time out
        self.da.queue_draw()

    def on_end(self, g, dx, dy):
        pts = self.stroke
        if len(pts) < 6 or math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]) < 1 and len(pts) < 10:
            print("cancel", flush=True)                                         # a tap = cancel
            return self.quit()
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        if max(xs) - min(xs) < 24 and max(ys) - min(ys) < 24:
            print("cancel", flush=True)
            return self.quit()
        pad = 8
        x0, y0, x1, y1 = max(0, min(xs) - pad), max(0, min(ys) - pad), min(self.sw, max(xs) + pad), min(self.sh, max(ys) + pad)
        print(json.dumps({"x": round(x0 / self.sw, 4), "y": round(y0 / self.sh, 4), "w": round((x1 - x0) / self.sw, 4), "h": round((y1 - y0) / self.sh, 4)}), flush=True)
        self.quit()

    def draw_stroke(self, cr, w, h):
        a = self.accent()
        cr.set_source_rgba(0, 0, 0, 0.12)                                       # a barely visible veil so you can tell the screen is waiting for a circle
        cr.paint()
        if len(self.stroke) > 1:
            for width, alpha in ((9, 0.18), (3.5, 0.95)):
                cr.set_source_rgba(a[0], a[1], a[2], alpha)
                cr.set_line_width(width)
                cr.set_line_join(cairo.LINE_JOIN_ROUND)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.move_to(*self.stroke[0])
                for p in self.stroke[1:]:
                    cr.line_to(*p)
                cr.stroke()
        else:
            self.caption(cr, "Draw a circle around it  (tap to cancel)", w / 2, 36, w, h)

    # ------------------------------------------------------------------ bubble mode
    def draw_bubble(self, cr, w, h):
        x, y, bw, bh = clamp_box(*[float(v) for v in self.args[:4]])
        text = str(self.args[4])[:420]
        a = self.accent()
        cr.set_source_rgba(a[0], a[1], a[2], 0.85)                              # the region stays outlined so you can see what the answer is about
        cr.set_line_width(2)
        self.rounded(cr, x * w, y * h, bw * w, bh * h, 6)
        cr.stroke()
        lay = PangoCairo.create_layout(cr)
        lay.set_font_description(Pango.FontDescription("Noto Sans 12"))
        cw = min(360, w * 0.45)
        lay.set_width(int((cw - 40) * Pango.SCALE))
        lay.set_wrap(Pango.WrapMode.WORD_CHAR)
        lay.set_text(text, -1)
        tw, th = lay.get_pixel_size()
        ch = th + 28
        px, py = place_card((x, y, bw, bh), w, h, cw, ch)
        if self.card_rect != (px, py, cw, ch):
            self.card_rect = (px, py, cw, ch)
            GLib.idle_add(self.set_input)
        cr.set_source_rgba(0.02, 0.04, 0.1, 0.94)
        self.rounded(cr, px, py, cw, ch, 10)
        cr.fill_preserve()
        cr.set_source_rgba(a[0], a[1], a[2], 0.9)
        cr.set_line_width(1.3)
        cr.stroke()
        cr.set_source_rgba(0.93, 0.96, 1, 1)
        cr.move_to(px + 14, py + 14)
        PangoCairo.show_layout(cr, lay)
        cr.set_source_rgba(0.7, 0.8, 1, 0.8)                                   # the x
        cr.set_line_width(2)
        cr.move_to(px + cw - 20, py + 8); cr.line_to(px + cw - 8, py + 20)
        cr.move_to(px + cw - 8, py + 8); cr.line_to(px + cw - 20, py + 20)
        cr.stroke()

    def on_bubble_click(self, gesture, n, x, y):
        if self.card_rect and x > self.card_rect[0] + self.card_rect[2] - 30 and y < self.card_rect[1] + 30:
            self.quit()


def main(argv):
    if not argv or argv[0] not in LIFETIME:
        print(__doc__)
        return 2
    mode, rest = argv[0], argv[1:]
    persona = "jarvis"
    if rest and rest[-1].startswith("--persona="):
        persona = rest.pop().split("=", 1)[1]
    if mode in ("point", "bubble") and len(rest) < 4:
        print("needs X Y W H [text]")
        return 2
    return Fx(mode, rest, persona).run([])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
