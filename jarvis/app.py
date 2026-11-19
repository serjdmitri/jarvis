"""Jarvis window (GTK4): shows state + conversation, with hold-to-talk / hold-to-look
buttons for touch, and a unix socket so Hyprland keybinds can drive it."""
import os
import socket
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import config  # noqa: E402
from .core import Assistant  # noqa: E402

APP_ID = "local.jarvis"
CSS = b"""
window { background: #14161a; color: #e6e6e6; }
.status { font-size: 15px; font-weight: bold; padding: 6px; color: #9aa4b2; }
.status.recording { color: #ff5d5d; }
.status.thinking, .status.transcribing { color: #ffd166; }
.status.speaking { color: #6ee7a8; }
.log { background: #1b1e24; color: #e6e6e6; font-size: 14px; padding: 8px; }
button.talk { font-size: 17px; font-weight: bold; padding: 18px 8px; background: #2a3140; color: #fff; }
button.talk.active { background: #c0392b; }
button.look.active { background: #2b7bd6; }
button.small { padding: 4px 10px; }
"""

STATUS = {
    "loading": "loading models...", "idle": "ready", "recording": "listening...",
    "transcribing": "transcribing...", "thinking": "thinking...", "speaking": "speaking...",
    "error": "error",
}


class JarvisWindow(Gtk.ApplicationWindow):
    def __init__(self, app, asst_factory):
        super().__init__(application=app, title="Jarvis")
        self.set_default_size(330, 380)
        css = Gtk.CssProvider()
        css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(self.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8, margin_bottom=8,
                      margin_start=8, margin_end=8)
        self.set_child(box)
        self.status = Gtk.Label(label=STATUS["loading"], xalign=0)
        self.status.add_css_class("status")
        box.append(self.status)

        self.view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                 left_margin=6, right_margin=6, top_margin=6, bottom_margin=6)
        self.view.add_css_class("log")
        buf = self.view.get_buffer()
        self.tags = {
            "you": buf.create_tag("you", foreground="#8ab4f8", weight=700),
            "jarvis": buf.create_tag("jarvis", foreground="#6ee7a8", weight=700),
            "sys": buf.create_tag("sys", foreground="#9aa4b2", style=2),
            "body": buf.create_tag("body", pixels_below_lines=8),
        }
        sw = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        sw.set_child(self.view)
        self.sw = sw
        box.append(sw)

        row = Gtk.Box(spacing=6, homogeneous=True)
        self.talk = self._hold_button("Hold to talk", "talk", look=False)
        self.look = self._hold_button("Hold to look", "look", look=True)
        row.append(self.talk)
        row.append(self.look)
        box.append(row)
        reset = Gtk.Button(label="New conversation")
        reset.add_css_class("small")
        reset.connect("clicked", lambda *_: self.asst.reset())
        box.append(reset)

        self.connect("close-request", lambda w: (w.set_visible(False), True)[1])  # hide, keep running
        self.app_log = app.log
        self.asst = asst_factory(self.on_state, self.on_msg)

    def _hold_button(self, label, kind, look):
        b = Gtk.Button(label=label)
        b.add_css_class("talk")
        b.add_css_class(kind)
        g = Gtk.GestureClick()
        g.set_button(0)  # any mouse button, and touch
        g.connect("pressed", lambda *_: self.asst.press(look=look))
        g.connect("released", lambda *_: self.asst.release())
        g.connect("cancel", lambda *_: self.asst.release())
        b.add_controller(g)
        return b

    # callbacks may come from worker threads
    def on_state(self, state, detail=""):
        GLib.idle_add(self._state, state, detail)

    def on_msg(self, role, text):
        GLib.idle_add(self._msg, role, text)

    def _state(self, state, detail):
        for s in STATUS:
            self.status.remove_css_class(s)
        self.status.add_css_class(state)
        self.status.set_text(detail or STATUS[state])
        for w, k in ((self.talk, "talk"), (self.look, "look")):
            (w.add_css_class if state == "recording" else w.remove_css_class)("active")
        if state == "error":
            self._msg("sys", detail)

    def _msg(self, role, text):
        self.app_log.append(f"{role}: {text}")
        buf = self.view.get_buffer()
        end = buf.get_end_iter()
        if buf.get_char_count():
            buf.insert(end, "\n")
        label = {"you": "You", "jarvis": "Jarvis", "sys": ""}[role]
        if label:
            buf.insert_with_tags(buf.get_end_iter(), label + "\n", self.tags[role])
            buf.insert_with_tags(buf.get_end_iter(), text + "\n", self.tags["body"])
        else:
            buf.insert_with_tags(buf.get_end_iter(), text + "\n", self.tags["sys"])
        GLib.idle_add(lambda: self.sw.get_vadjustment().set_value(self.sw.get_vadjustment().get_upper()))


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.win = None
        self.log = []

    def do_activate(self):
        if self.win:
            self.win.present()
            return
        self.win = JarvisWindow(self, Assistant)
        self.win.present()
        self.win.asst.preload()
        threading.Thread(target=self._serve, daemon=True).start()

    # --- control socket ---------------------------------------------------
    def _serve(self):
        sock_path = str(config.SOCK)
        if os.path.exists(sock_path):
            os.unlink(sock_path)
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(sock_path)
        srv.listen(8)
        while True:
            conn, _ = srv.accept()
            try:
                cmd = conn.makefile().readline().strip()
                GLib.idle_add(self._cmd, cmd)
                extra = "\n".join(self.log[-6:]) if cmd == "log" else ""
                conn.sendall(f"ok {cmd} [{self.win.asst.state}]\n{extra}\n".encode())
            except OSError:
                pass
            finally:
                conn.close()

    def _cmd(self, cmd):
        a = self.win.asst
        if cmd == "start":
            a.press(look=False)
        elif cmd == "look":
            a.press(look=True)
        elif cmd == "stop":
            a.release()
        elif cmd == "reset":
            a.reset()
        elif cmd == "show":
            self.win.present()
        elif cmd == "quit":
            self.quit()
        return False


def main():
    sys.exit(App().run([sys.argv[0]]))


if __name__ == "__main__":
    main()
