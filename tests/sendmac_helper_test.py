"""send to my Mac: confirmed by the helper / not confirmed (falls back and SAYS so) / helper down. Uses the in-process fake Assistant methods only. Run: .venv/bin/python tests/sendmac_helper_test.py"""
import os, sys, tempfile, pathlib
T = tempfile.mkdtemp(prefix="smh-"); os.environ.update(XDG_CONFIG_HOME=T + "/x", JARVIS_DATA_DIR=T + "/d")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from jarvis import core, machelper, mac
R = []
def rec(n, ok, extra=""): R.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n, extra)
class A:                                   # just enough of the Assistant for run_mac
    def __init__(s): s.said, s.msgs = [], []
    def say(s, t, wait=False): s.said.append(t)
    def on_msg(s, k, t): s.msgs.append(t)
    on_card = None; timers = []
    run_mac = core.Assistant.run_mac
a = A(); f = pathlib.Path(__file__).resolve().parent.parent / "shots" / "smh-test.png"; f.parent.mkdir(exist_ok=True); f.write_bytes(b"\x89PNG" + b"1" * 100)
fallback = []
mac.send = lambda p: (fallback.append(p), {"ok": True, "message": "sent", "label": "x", "id": None})[1]
machelper.status = lambda force=False: "ready"
machelper.save_file = lambda path, name=None: {"ok": True, "name": "shot.png", "quarantined": False}
core.sounds.play = lambda *a, **k: None
r = a.run_mac({"file": str(f)}); rec("helper confirmed: says 'Saved to your Mac's Downloads: shot.png', no ntfy", r["ok"] and a.said and a.said[-1] == "Saved to your Mac's Downloads: shot.png" and not fallback, str(a.said))
a = A(); machelper.save_file = lambda path, name=None: {"ok": False, "why": "the Mac did not confirm the save"}
r = a.run_mac({"file": str(f)}); rec("not confirmed: no 'Saved' claim, says it failed, falls back to the old path", not any("Saved to your Mac" in x for x in a.said) and any("did not confirm" in m for m in a.msgs) and fallback, str(a.msgs))
a = A(); fallback.clear(); machelper.status = lambda force=False: "asleep"
r = a.run_mac({"file": str(f)}); rec("helper down: old path used, no false claim", fallback and not any("Saved to your Mac" in x for x in a.said))
a = A(); fallback.clear(); machelper.status = lambda force=False: "ready"; machelper.save_file = lambda path, name=None: {"ok": True, "name": "run.command", "quarantined": True}
r = a.run_mac({"file": str(f)}); rec("quarantined file is reported as such", a.said and "Quarantine" in a.said[-1] and "not opened" in a.said[-1], a.said[-1])
a = A(); called = []; machelper.save_file = lambda path, name=None: (called.append(path), {"ok": True, "name": "k", "quarantined": False})[1]
r = a.run_mac({"file": os.path.expanduser("~/.ssh/id_ed25519")}); rec("audit: a private key path is refused before the helper is ever asked", not r["ok"] and not called and a.said)
r = a.run_mac({"file": "/etc/passwd"}); rec("audit: a file outside the home folder is refused", not r["ok"] and not called)
f.unlink()
print(f"{sum(R)}/{len(R)} passed")
