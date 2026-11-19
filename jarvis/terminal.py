"""A real terminal inside Jarvis (xterm.js in a window, a pty behind it) and the FILES window's API. For YOU typing only: nothing in the assistant (the brain, intents, actions) can reach this module.
Security: the server listens on 127.0.0.1 only; every use needs a random token made at server start (kept in memory, handed to the page over its own websocket when you open the window, never in a URL,
a log or a file); the websocket's Host and Origin must be this page; nothing starts until you open it; at most 3 sessions."""
import asyncio
import fcntl
import hmac
import json
import os
import pty
import secrets
import signal
import struct
import subprocess
import termios
from pathlib import Path

TOKEN = secrets.token_urlsafe(32)
MAX_SESSIONS = 3
SESSIONS = set()
HOME = os.path.realpath(os.path.expanduser("~"))
IMG = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def token_ok(t):
    return isinstance(t, str) and hmac.compare_digest(t, TOKEN)


def under_home(path):
    """-> the real path if it is inside your home folder, else None."""
    try:
        real = os.path.realpath(os.path.expanduser(str(path)))
    except (OSError, ValueError):
        return None
    return real if real == HOME or real.startswith(HOME + os.sep) else None


def _winsize(fd, cols, rows):
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", max(2, min(500, rows)), max(2, min(1000, cols)), 0, 0))
    except OSError:
        pass


def _argv(preset):
    sh = os.environ.get("SHELL") or "/bin/bash"
    if preset == "claude":
        return [sh, "-ic", 'claude; exec "$SHELL" -i']          # Claude Code, and a normal shell when you leave it
    return [sh, "-i"]


async def handle(ws, first_text, loop):
    """One terminal session on an accepted websocket (the first message already carried the token). Input comes only from this socket."""
    from aiohttp import WSMsgType
    try:
        hello = json.loads(first_text[1:])
    except ValueError:
        await ws.close()
        return
    if len(SESSIONS) >= MAX_SESSIONS:
        await ws.send_str("\x00" + json.dumps({"error": "too many terminals open"}))
        await ws.close()
        return
    cwd = under_home(hello.get("dir") or HOME)
    cwd = cwd if cwd and os.path.isdir(cwd) else HOME
    preset = "claude" if hello.get("preset") == "claude" else "shell"
    master, slave = pty.openpty()
    env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor")
    for k in ("LD_PRELOAD",):
        env.pop(k, None)
    proc = subprocess.Popen(_argv(preset), stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=env, start_new_session=True, close_fds=True,
                            preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
    os.close(slave)
    _winsize(master, int(hello.get("cols") or 80), int(hello.get("rows") or 24))
    os.set_blocking(master, False)
    SESSIONS.add(proc)
    q = asyncio.Queue()

    def on_read():
        try:
            data = os.read(master, 65536)
        except OSError:
            data = b""
        q.put_nowait(data)
        if not data:
            loop.remove_reader(master)
    loop.add_reader(master, on_read)

    async def pump():
        while True:
            data = await q.get()
            if not data:
                await ws.send_str("\x00" + json.dumps({"exit": proc.poll()}))
                return
            await ws.send_bytes(data)
    task = asyncio.ensure_future(pump())
    try:
        async for m in ws:
            if m.type != WSMsgType.TEXT:
                continue
            if m.data.startswith("\x00"):                      # control: resize
                try:
                    r = json.loads(m.data[1:]).get("resize")
                    if r:
                        _winsize(master, int(r[0]), int(r[1]))
                except (ValueError, TypeError, IndexError):
                    pass
            else:
                os.write(master, m.data.encode("utf-8", "ignore"))      # your keystrokes, nothing else
    finally:
        task.cancel()
        try:
            loop.remove_reader(master)
        except Exception:
            pass
        SESSIONS.discard(proc)
        try:
            os.killpg(proc.pid, signal.SIGHUP)
        except (ProcessLookupError, PermissionError):
            pass
        await asyncio.sleep(0.3)
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            os.close(master)
        except OSError:
            pass
        proc.wait()


# ----------------------------------------------------------------------------- FILES
_thumbs = {}


def listing(path, hidden=False):
    real = under_home(path or HOME)
    if not real or not os.path.isdir(real):
        return None
    ents = []
    try:
        for e in sorted(os.scandir(real), key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower())):
            if e.name.startswith(".") and not hidden:
                continue
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            d = e.is_dir(follow_symlinks=False)
            ext = os.path.splitext(e.name)[1].lower()
            ents.append({"name": e.name[:120], "dir": d, "size": 0 if d else st.st_size, "img": (not d and ext in IMG and st.st_size < 30_000_000)})
            if len(ents) >= 400:
                break
    except OSError:
        return None
    parent = os.path.dirname(real) if real != HOME else None
    return {"path": real, "home": real == HOME, "parent": parent, "entries": ents}


def thumb(path):
    """-> jpeg bytes (192 px wide) for an image under your home folder, or None. Cached by modification time."""
    real = under_home(path)
    if not real or not os.path.isfile(real) or os.path.splitext(real)[1].lower() not in IMG:
        return None
    mt = os.path.getmtime(real)
    hit = _thumbs.get(real)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", real, "-vf", "scale=192:-2", "-frames:v", "1", "-f", "mjpeg", "-q:v", "6", "-"], capture_output=True, timeout=8).stdout
    except Exception:
        return None
    if len(out) < 100:
        return None
    if len(_thumbs) > 200:
        _thumbs.pop(next(iter(_thumbs)))
    _thumbs[real] = (mt, out)
    return out
