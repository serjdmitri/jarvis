"""The Jarvis supervisor: starts everything at login and brings back whatever dies or hangs, within about 5 seconds.

Watches: the server (does the control socket answer?), the kiosk window (exactly one `jarvis-ui`; the page also sends a heartbeat every 3 s: a window that is
up but silent for 20 s is a hung page and is restarted), the orb, the brain process (respawned by asking the server to warm it) and the hands-free mic loop
(`mic=` in the status line). Restarts are limited: more than 5 in 2 minutes and it stops restarting, shows a plain "Jarvis needs help" card with the last
error (and a desktop notification) instead of looping, then tries again after 5 minutes.
Logs to logs/watchdog.log (rotating, 3 x 200 KB). It logs component names and times only: never user speech, codes, topics or keys.
One instance only (a lock file)."""
import fcntl
import json
import logging
import logging.handlers
import os
import re
import signal
import subprocess
import sys
import time
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv", "bin", "python")
LOGDIR = os.path.join(ROOT, "logs")
CHECK_S = 2.0
LIMIT_N, LIMIT_S, COOLDOWN_S = 5, 120, 300
UI_STALE_S = 20
log = logging.getLogger("jarvis-watchdog")


def setup_log():
    os.makedirs(LOGDIR, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(os.path.join(LOGDIR, "watchdog.log"), maxBytes=200_000, backupCount=3)
    h.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(h)
    log.setLevel(logging.INFO)


def sh(cmd, timeout=4):
    try:
        return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:
        return ""


def ctl(cmd, timeout=2.5):
    """The server's control socket; '' when it does not answer."""
    import socket
    s = socket.socket(socket.AF_UNIX)
    try:
        s.settimeout(timeout)
        s.connect(os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "jarvis.sock"))
        s.sendall(cmd.encode() + b"\n")
        return s.recv(4096).decode().strip()
    except OSError:
        return ""
    finally:
        s.close()


STOP_FLAG = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "jarvis.stopped")     # written by a deliberate quit: the watchdog then leaves everything alone


def clients():
    try:
        return json.loads(sh(["hyprctl", "clients", "-j"]) or "[]")
    except ValueError:
        return []


def spawn(argv, name):
    log.info("starting %s", name)
    subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


class Watchdog:
    def __init__(self):
        self.restarts = deque()
        self.paused_until = 0.0
        self.last_error = ""
        self.miss = {"server": 0, "kiosk": 0, "orb": 0, "brain": 0, "mic": 0, "ui": 0}
        self.started = time.time()
        self.grace = {}                                  # component -> time before which it is not judged (it was just started)
        self.was_up = set()                              # components that have been seen healthy: only THEIR disappearance is a restart

    def note_restart(self, what, why):
        now = time.time()
        self.restarts.append(now)
        while self.restarts and now - self.restarts[0] > LIMIT_S:
            self.restarts.popleft()
        self.last_error = f"{what}: {why}"
        log.info("restart %s (%s) [%d in the last %ds]", what, why, len(self.restarts), LIMIT_S)
        if len(self.restarts) > LIMIT_N:
            self.give_up()
            return False
        return True

    def give_up(self):
        self.paused_until = time.time() + COOLDOWN_S
        log.info("too many restarts: pausing %ds. last error: %s", COOLDOWN_S, self.last_error)
        msg = f"Jarvis needs help. It restarted too often. Last problem: {self.last_error}"
        ctl("help " + msg[:200])                       # a card, if the server is up
        sh(["notify-send", "-u", "critical", "Jarvis needs help", self.last_error[:120]])

    # ---------------------------------------------------------------- the checks
    def server_ok(self):
        return "status" in ctl("status")

    def restart_server(self, why, first=False):
        if not first and not self.note_restart("server", why):
            return
        sh("pkill -f '[.]venv/bin/python -m jarvis[.]webapp'")
        time.sleep(1.2)
        spawn([PY, "-m", "jarvis.webapp"], "server")
        self.grace["server"] = time.time() + 75        # the speech models take a while to load: not judged silent before then
        for _ in range(75):
            if self.server_ok():
                break
            time.sleep(1)

    def kiosk_windows(self):
        return [c for c in clients() if c.get("class") == "jarvis-ui"]

    def restart_kiosk(self, why, kill=False, first=False):
        if not first and not self.note_restart("kiosk", why):
            return
        self.grace["kiosk"] = time.time() + 100      # chromium is slow to appear while the speech models load: judged only after this
        if kill:
            sh(["pkill", "-f", "chromium.*--class=jarvis-ui"])
            time.sleep(1.5)
        spawn([os.path.join(ROOT, "bin", "jarvis-ui")], "kiosk")

    def tick(self):
        if time.time() < self.paused_until or os.path.exists(STOP_FLAG):          # paused after too many restarts, or you quit Jarvis on purpose
            return
        st = ctl("status")
        if not st and time.time() < self.grace.get("server", 0):
            return                                      # still loading
        if not st:
            self.miss["server"] += 1
            if self.miss["server"] >= 2 or time.time() - self.started < 3:            # ~4 s without an answer
                self.miss["server"] = 0
                self.restart_server("control socket silent")
            return
        self.miss["server"] = 0
        self.was_up.add("server")
        wins = self.kiosk_windows()
        if len(wins) == 0:
            if time.time() < self.grace.get("kiosk", 0) or sh("pgrep -f 'chromium.*--class=jarvis-ui'"):
                pass                                                                   # just started: give it time to appear
            else:
                self.miss["kiosk"] += 1
                if self.miss["kiosk"] >= 2:
                    self.miss["kiosk"] = 0
                    self.restart_kiosk("window missing", first="kiosk" not in self.was_up)
        else:
            self.miss["kiosk"] = 0
            self.was_up.add("kiosk")
            if len(wins) > 1:                                                          # never two Jarvis windows
                log.info("%d kiosk windows: closing extras", len(wins))
                for c in wins[1:]:
                    sh(["hyprctl", "dispatch", 'hl.dsp.window.close({ window = "address:%s" })' % c["address"]])
            m = re.search(r"ui=(\d+)s", st)
            if m and int(m.group(1)) > UI_STALE_S and time.time() >= self.grace.get("kiosk", 0):                                    # a window that is up but sends no heartbeat is a hung page
                self.miss["ui"] += 1
                if self.miss["ui"] >= 2:
                    self.miss["ui"] = 0
                    self.restart_kiosk(f"page silent for {m.group(1)}s", kill=True)
            else:
                self.miss["ui"] = 0
        if not sh("pgrep -f '[.]venv/bin/python -m jarvis[.]orb'"):
            if time.time() >= self.grace.get("orb", 0):
                self.miss["orb"] += 1
                if self.miss["orb"] >= 2:
                    self.miss["orb"] = 0
                    if "orb" not in self.was_up or self.note_restart("orb", "process missing"):
                        self.grace["orb"] = time.time() + 12
                        spawn([os.path.join(ROOT, "bin", "jarvis-orb")], "orb")
        else:
            self.miss["orb"] = 0
            self.was_up.add("orb")
        if "handsfree=on" in st and "muted=off" in st:
            m = re.search(r"mic=(\w+)", st)
            if m and m.group(1) == "stale":
                self.miss["mic"] += 1
                if self.miss["mic"] >= 3:
                    self.miss["mic"] = 0
                    if self.note_restart("mic loop", "stalled"):
                        ctl("listen restart")
            else:
                self.miss["mic"] = 0
        if not sh("pgrep -f 'claude -p --input-format stream-json'") and time.time() - self.started > 60:
            self.miss["brain"] += 1
            if self.miss["brain"] >= 3:
                self.miss["brain"] = 0
                if self.note_restart("brain", "process missing"):
                    ctl("brainwarm")

    def run(self):
        lock = open(os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "jarvis-watchdog.lock"), "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("the watchdog is already running")
            return 0
        setup_log()
        try:
            os.unlink(STOP_FLAG)                       # starting the watchdog means: bring Jarvis up
        except OSError:
            pass
        log.info("watchdog started")
        signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
        if not self.server_ok():                       # at login: bring everything up in a clean state
            self.restart_server("not running at start", first=True)
        while True:
            try:
                self.tick()
            except Exception as e:                     # the supervisor itself must never die
                log.info("tick error: %s", type(e).__name__)
            time.sleep(CHECK_S)


if __name__ == "__main__":
    sys.exit(Watchdog().run())
