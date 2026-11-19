"""The 'back to Jarvis' button: a tiny, dim pill on the overlay layer that exists only while some OTHER window is in front
of the Jarvis kiosk (so it never covers Jarvis itself). One tap focuses the Jarvis window again (same as the voice command
and SUPER+ALT+J).  Run through bin/jarvis-pill (needs libgtk4-layer-shell preloaded)."""
import json
import os
import socket
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LS  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIOSK = ("jarvis-ui", "local.jarvis", "chrome-127.0.0.1__dock-Default")
CSS = b"""
window { background: transparent; }
button { background: rgba(5,8,20,.70); color: #9fb4e0; border: 1px solid rgba(160,190,255,.45); border-radius: 0; padding: 9px 20px;
         font-family: 'Noto Sans Mono', monospace; font-weight: 700; font-size: 9px; letter-spacing: 2px; box-shadow: none; }
button:active { color: #ffffff; border-color: #ffffff; }
"""


def hj(*args):
    try:
        return json.loads(subprocess.run(["hyprctl", *args, "-j"], capture_output=True, text=True, timeout=2).stdout)
    except Exception:
        return None


def want_visible():
    cl, act = hj("clients"), hj("activewindow")
    if not isinstance(cl, list) or not any(c.get("class") in KIOSK for c in cl):
        return False                                     # no Jarvis window to go back to
    return isinstance(act, dict) and act.get("class") not in KIOSK and bool(act.get("class"))


class Pill(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="local.jarvis.pill")
        self.win, self.timer = None, 0

    def do_activate(self):
        if self.win:
            return
        w = self.win = Gtk.Window(application=self)
        LS.init_for_window(w)
        LS.set_namespace(w, "jarvis-pill")
        LS.set_layer(w, LS.Layer.OVERLAY)
        LS.set_anchor(w, LS.Edge.BOTTOM, True)
        LS.set_margin(w, LS.Edge.BOTTOM, 64)                # well away from every corner
        LS.set_keyboard_mode(w, LS.KeyboardMode.NONE)       # never steals the keyboard
        css = Gtk.CssProvider(); css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        b = Gtk.Button(label="BACK TO JARVIS")
        b.connect("clicked", lambda *_: subprocess.Popen([os.path.join(ROOT, "bin", "jarvisctl"), "back"]))
        w.set_child(b)
        self.hold()
        self.refresh()
        sig, rt = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"), os.environ.get("XDG_RUNTIME_DIR")
        path = f"{rt}/hypr/{sig}/.socket2.sock"
        self.sock = socket.socket(socket.AF_UNIX); self.sock.connect(path); self.sock.setblocking(False)
        GLib.io_add_watch(self.sock.fileno(), GLib.IO_IN | GLib.IO_HUP, self.on_event)

    def on_event(self, fd, cond):
        try:
            data = self.sock.recv(65536)
        except BlockingIOError:
            return True
        if not data:
            self.quit(); return False
        if any(k in data for k in (b"activewindow", b"openwindow", b"closewindow", b"workspace", b"movewindow")):
            GLib.source_remove(self.timer) if self.timer else None
            self.timer = GLib.timeout_add(120, self.refresh)    # debounce: events arrive in bursts
        return True

    def refresh(self):
        self.timer = 0
        self.win.set_visible(want_visible())
        return False


if __name__ == "__main__":
    sys.exit(Pill().run([sys.argv[0]]))
