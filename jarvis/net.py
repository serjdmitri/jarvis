"""Is the internet reachable? A 1.5 s TCP probe to the brain's API host, cached for 10 s, so an offline Jarvis answers at once instead of waiting a minute."""
import socket
import time

_C = {"t": 0.0, "ok": True}


def online(force=False):
    if not force and time.time() - _C["t"] < 10:
        return _C["ok"]
    ok = False
    for host in ("api.anthropic.com", "claude.ai"):
        try:
            socket.create_connection((host, 443), timeout=1.5).close()
            ok = True
            break
        except OSError:
            continue
    _C.update(t=time.time(), ok=ok)
    return ok
