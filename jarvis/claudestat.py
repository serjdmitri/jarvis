"""Claude Code / Codex status on the Go: working, needs me, done, failed. Real hooks, no screen scraping.
Claude Code can run a command on events (settings.json "hooks": Notification when it needs you, Stop when it finishes, PreToolUse while it works). Codex has a `notify` program in its
config. Either one runs bin/jarvis-hook, which posts a tiny JSON to http://127.0.0.1:<port>/hook/<token> (localhost only; the token is made on first use, mode 600, outside git).
This file only RECEIVES: it never changes Claude Code's or Codex's settings (the exact lines to add are shown in a card for you to approve). ALLOW / DECLINE from Jarvis is NOT built:
that needs a permission hook that holds the terminal prompt open and answers it, which cannot be tested safely here and must never be answered by voice or the model."""
import hmac
import json
import os
import re
import secrets
import time

from . import config

FILE = config.DATA / "hook_token"
STATE = {"state": "idle", "who": "", "msg": "", "ts": 0.0}
EVENTS = {"working": "working", "pre_tool": "working", "needs": "needs", "notification": "needs", "permission": "needs", "done": "done", "stop": "done", "fail": "error", "error": "error",
          "blocked": "error", "end": "idle", "clear": "idle"}


def token():
    try:
        return FILE.read_text().strip()
    except OSError:
        pass
    config.DATA.mkdir(parents=True, exist_ok=True)
    t = secrets.token_urlsafe(24)
    fd = os.open(FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(t + "\n")
    return t


def check(tok):
    return bool(tok) and hmac.compare_digest(tok, token())


def update(body):
    """A hook's JSON -> the new state dict, or None if it is not understood. Text is cut to 120 characters and stripped of control characters."""
    if not isinstance(body, dict):
        return None
    ev = EVENTS.get(str(body.get("event") or body.get("hook_event_name") or "").lower().replace("-", "_").replace("pretooluse", "pre_tool").replace("sessionend", "end"))
    if not ev:
        return None
    who = "codex" if str(body.get("who") or "").lower() == "codex" else "claude"
    msg = re.sub(r"[\x00-\x1f]+", " ", str(body.get("message") or body.get("text") or ""))[:120].strip()
    STATE.update(state=ev, who=who, msg=msg, ts=time.time())
    return dict(STATE)


def snippet(port):
    """The exact change for ~/.claude/settings.json, shown for approval (nothing is written)."""
    cmd = f"{config.ROOT}/bin/jarvis-hook"
    h = lambda ev: [{"hooks": [{"type": "command", "command": f"{cmd} {ev}"}]}]
    return json.dumps({"hooks": {"Notification": h("needs"), "Stop": h("done"), "PreToolUse": h("working")}}, indent=2)
