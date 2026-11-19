"""Desktop helpers inside Jarvis: LAUNCHER (search apps and your files), CLIPBOARD history (memory only, secrets skipped) and the SCRATCHPAD (data/scratch.txt), and named LAYOUTS (which Jarvis windows to open together)."""
import json
import os
import re
import subprocess
import threading
import time
from collections import deque

from . import config, memory, opener, terminal

SCRATCH = config.ROOT / "data" / "scratch.txt"
LAYOUTS = config.ROOT / "data" / "layouts.json"
CLIP = deque(maxlen=30)
_clip = {"proc": None}
TOOLS = {"terminal": {"kind": "term"}, "claude": {"kind": "term", "preset": "claude"}, "files": {"kind": "files"}, "launcher": {"kind": "launch"}, "scratchpad": {"kind": "scratch"}, "clipboard": {"kind": "clip"}}


def launcher_search(q, limit=8):
    q = re.sub(r"[^\w .-]", " ", q.lower()).strip()
    if len(q) < 2:
        return []
    out = []
    for name, did in opener.desktop_apps().items():
        if q in name:
            out.append({"kind": "app", "name": name.title(), "id": did})
    out.sort(key=lambda x: (not x["name"].lower().startswith(q), len(x["name"])))
    out = out[:5]
    try:
        r = subprocess.run(["find", terminal.HOME, "-maxdepth", "4", "-not", "-path", "*/.*", "-not", "-path", "*/node_modules/*", "-not", "-path", "*/__pycache__/*", "-not", "-name", "*.pyc", "-not", "-path", "*/.venv/*", "-iname", f"*{q}*"], capture_output=True, text=True, timeout=4)
        for p in r.stdout.splitlines()[:limit]:
            if terminal.under_home(p):
                out.append({"kind": "dir" if os.path.isdir(p) else "file", "name": os.path.basename(p)[:60], "path": p})
    except Exception:
        pass
    return out[:limit + 5]


# ----------------------------------------------------------------------------- clipboard history (memory only)
def start_clip_watch():
    """wl-paste --watch: every new text copy goes in the history (text only, max 2 KB, anything that looks like a password or card is skipped, never written to disk)."""
    if _clip["proc"] or not os.path.exists("/usr/bin/wl-paste"):
        return
    try:
        p = subprocess.Popen(["wl-paste", "--watch", "sh", "-c", 'cat; printf "\\0"'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return
    _clip["proc"] = p

    def read():
        buf = b""
        while True:
            c = p.stdout.read1(4096) if hasattr(p.stdout, "read1") else p.stdout.read(1)
            if not c:
                break
            buf += c
            while b"\0" in buf:
                item, buf = buf.split(b"\0", 1)
                t = item.decode("utf-8", "ignore").strip()
                if t and len(t) <= 2000 and not memory.looks_secret(t) and (not CLIP or CLIP[0] != t):
                    CLIP.appendleft(t)
    threading.Thread(target=read, daemon=True).start()


def clip_lines():
    return [(t[:90] + ("…" if len(t) > 90 else "")).replace("\n", " ") for t in list(CLIP)[:12]]


# ----------------------------------------------------------------------------- scratchpad
def scratch_get():
    try:
        return SCRATCH.read_text()[:20000]
    except OSError:
        return ""


def scratch_put(text):
    SCRATCH.parent.mkdir(exist_ok=True)
    SCRATCH.write_text(text[:20000])


# ----------------------------------------------------------------------------- layouts
def _lay():
    try:
        return json.loads(LAYOUTS.read_text())
    except (OSError, ValueError):
        return {}


def save_layout(name, wins):
    """wins: [{"kind","preset","rect"}] from the page; only the Jarvis tools are kept."""
    clean = [{"kind": w["kind"], "preset": w.get("preset"), "rect": w.get("rect")} for w in wins if w.get("kind") in ("term", "files", "launch", "scratch", "clip")][:6]
    d = _lay()
    d[re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()[:30] or "layout"] = clean
    LAYOUTS.parent.mkdir(exist_ok=True)
    LAYOUTS.write_text(json.dumps(d))
    return len(clean)


def get_layout(name):
    d = _lay()
    n = re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()
    return d.get(n)


def layout_names():
    return sorted(_lay())


def parse_command(text):
    low = text.lower().strip(" .?!")
    if re.fullmatch(r"(?:please )?(?:open|show|start|bring up) (?:the |my )?(?:launcher|app launcher|app search)|launch(?:er)?", low):
        return {"kind": "tool", "tool": "launcher"}
    m = re.fullmatch(r"(?:please )?(?:open|show|start|bring up) (?:the |my )?(scratchpad|scratch pad|clipboard(?: history)?|terminal|files)", low)
    if m and m.group(1) in ("scratchpad", "scratch pad", "clipboard", "clipboard history"):
        return {"kind": "tool", "tool": "scratchpad" if "scratch" in m.group(1) else "clipboard"}
    m = re.fullmatch(r"(?:please )?(?:save|remember) (?:this|these|my|the current) (?:window )?(?:layout|setup|windows)(?: as| called)? (.+)", low)
    if m:
        return {"kind": "save", "name": m.group(1)}
    m = re.fullmatch(r"(?:please )?(?:open|load|start|switch to|bring up) (?:my |the )?(.+?) (?:layout|mode|setup|workspace)", low)
    if m:
        return {"kind": "open", "name": m.group(1)}
    if re.fullmatch(r"(?:what|which|list|show) (?:are )?(?:my )?(?:layouts|saved layouts|window layouts)", low):
        return {"kind": "list"}
    return None
