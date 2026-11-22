"""Is the internet reachable? A 1.5 s TCP probe to the brain's API host, cached for 10 s, so an offline Jarvis answers at once instead of waiting a minute."""
import socket
import time

_C = {"t": 0.0, "ok": True}


def _probe(host, out, i):
    try:
        socket.create_connection((host, 443), timeout=1.2).close()
        out[i] = True
    except OSError:
        out[i] = False


def online(force=False):
    """Both hosts are tried at the same time, so "no internet" is known in about 1.2 s (never 3)."""
    import os
    if os.environ.get("JARVIS_FAKE_OFFLINE") and os.path.exists(os.environ["JARVIS_FAKE_OFFLINE"]):      # tests only: a flag file makes the internet "disappear"
        return False
    if not force and time.time() - _C["t"] < 10:
        return _C["ok"]
    import threading
    hosts = ("api.anthropic.com", "claude.ai")
    out = [None, None]
    ts = [threading.Thread(target=_probe, args=(h, out, i), daemon=True) for i, h in enumerate(hosts)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(1.6)
    ok = any(out)
    _C.update(t=time.time(), ok=ok)
    return ok
