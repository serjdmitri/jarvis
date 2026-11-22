"""A quick type box near the pointer: a shortcut opens one small text field (GTK4 layer-shell overlay, so it works over any app, even fullscreen); type, press Enter,
and the answer comes back as a card or a spoken line. Escape, a click away, or ten idle seconds closes it.

It only takes the keyboard while it is open (keyboard mode ON_DEMAND), so it cannot conflict with typing in the terminal card or other apps: the shortcut itself is a
Hyprland bind that runs `jarvisctl typebox alt|ctrl`, and the server opens the box only for the key you chose in settings (Alt+Space, Ctrl+Shift+Space, both or neither).
The text goes to the running Jarvis exactly as if it had been spoken (same local skills, same allowlists). Nothing is stored.
Run through bin/jarvis-typebox (libgtk4-layer-shell must be preloaded)."""
import json
import os
import socket
import subprocess
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LS  # noqa: E402

W, H = 360, 46
ACC = {"jarvis": "#3d8bff", "rose": "#ff2d55"}
CLOSE_AFTER_S = 10


def cursor():
    """-> (x, y, monitor_w, monitor_h) from hyprctl, or a screen-centre fallback."""
    try:
        cp = subprocess.run(["hyprctl", "cursorpos"], capture_output=True, text=True, timeout=1.5).stdout.strip().split(",")
        x, y = int(cp[0]), int(cp[1])
    except Exception:
        x = y = -1
    try:
        mons = json.loads(subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=1.5).stdout)
        m = next((m for m in mons if m.get("focused")), mons[0])
        mw, mh = int(m["width"] / (m.get("scale") or 1)), int(m["height"] / (m.get("scale") or 1))
        x, y = (x - int(m.get("x", 0)), y - int(m.get("y", 0))) if x >= 0 else (mw // 2 - W // 2, mh // 3)
    except Exception:
        mw, mh = 1280, 800
        x, y = (mw // 2 - W // 2, mh // 3) if x < 0 else (x, y)
    return x, y, mw, mh


def place(x, y, mw, mh):
    """Just below the pointer, kept fully on the screen."""
    return max(8, min(mw - W - 8, x - W // 2)), max(8, min(mh - H - 8, y + 18))


def send_text(text):
    """The typed words go to the running Jarvis through its control socket, like a spoken request. -> True if it was accepted."""
    t = " ".join(text.split())[:400]
    if not t:
        return False
    s = socket.socket(socket.AF_UNIX)
    try:
        s.settimeout(3)
        s.connect(os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "jarvis.sock"))
        s.sendall(("ask " + t + "\n").encode())
        return b"asked" in s.recv(200) or True
    except OSError:
        return False
    finally:
        s.close()


class Box(Gtk.Application):
    def __init__(self, persona="jarvis", name="Jarvis"):
        super().__init__(application_id="dev.jarvis.typebox")
        self.persona, self.name, self.win, self.t0 = persona, name, None, time.time()

    def do_activate(self):
        if self.win:
            return
        w = self.win = Gtk.Window(application=self)
        w.set_default_size(W, H)
        LS.init_for_window(w)
        LS.set_namespace(w, "jarvis-typebox")
        LS.set_layer(w, LS.Layer.OVERLAY)
        LS.set_anchor(w, LS.Edge.LEFT, True)
        LS.set_anchor(w, LS.Edge.TOP, True)
        LS.set_exclusive_zone(w, -1)
        LS.set_keyboard_mode(w, LS.KeyboardMode.ON_DEMAND)
        x, y, mw, mh = cursor()
        px, py = place(x, y, mw, mh)
        LS.set_margin(w, LS.Edge.LEFT, px)
        LS.set_margin(w, LS.Edge.TOP, py)
        acc = ACC.get(self.persona, "#3d8bff")
        css = Gtk.CssProvider()
        css.load_from_data(("window, window.background { background: transparent; } entry { background: rgba(4,8,22,.94); color: #e8f0ff; border: 1px solid %s; border-radius: 10px; padding: 8px 12px; "
                            "font-size: 15px; box-shadow: 0 0 18px %s; min-height: 28px; }" % (acc, acc + "88")).encode())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        e = self.entry = Gtk.Entry()
        e.set_placeholder_text(f"Ask {self.name}…  (Enter to send, Esc to close)")
        e.set_size_request(W, H)
        e.connect("activate", self.on_enter)
        kc = Gtk.EventControllerKey()
        kc.connect("key-pressed", self.on_key)
        e.add_controller(kc)
        w.set_child(e)
        w.connect("notify::is-active", lambda win, _p: (not win.is_active()) and time.time() - self.t0 > 0.6 and self.close())     # a click elsewhere closes it
        w.present()
        e.grab_focus()
        GLib.timeout_add(500, self.idle)
        self.hold()

    def idle(self):
        if time.time() - self.t0 > CLOSE_AFTER_S and not self.entry.get_text():
            self.close()
            return False
        if self.entry.get_text():
            self.t0 = time.time()                      # still typing: stay open
        return True

    def on_key(self, ctl, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def on_enter(self, entry):
        ok = send_text(entry.get_text())
        entry.set_text("")
        self.close() if ok else entry.set_placeholder_text("Jarvis is not running")
        if not ok:
            GLib.timeout_add(1500, lambda: self.close() or False)

    def close(self):
        if self.win:
            self.win.destroy()
            self.win = None
        self.release()
        self.quit()


def main():
    persona, name = (sys.argv[1], sys.argv[2]) if len(sys.argv) > 2 else ("jarvis", "Jarvis")
    Box(persona, name).run([])


if __name__ == "__main__":
    main()
