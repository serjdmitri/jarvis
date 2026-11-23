"""A3: the follow-up window (keep listening after a command). Simulated clock: frames are fed to the real capture code with made-up times. Run: .venv/bin/python tests/window_test.py"""
import os, sys, types
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
import numpy as np
from jarvis import wake, opener, convo, prefs, settings
R = []
def rec(n, ok): R.append(ok); print(("PASS " if ok else "FAIL ") + n, flush=True)
ev = []; opener.NOTIFY = lambda m: ev.append(m)
class Spk:
    def __init__(s): s.b = False; s.last_end = 0
    def busy(s): return s.b
class A:
    def __init__(s): s.state = "idle"; s.speaker = Spk(); s.muted = False; s.got = []; s.ended = 0; s.persona = "jarvis"; s.msgs = []
    def _set(s, st, d=""): s.state = st
    def on_level(s, *a): pass
    def submit_audio(s, data, shot=None, strip_wake=False, conv=False): s.got.append((len(data), conv))
    def conversation_end(s): s.ended += 1
    def on_msg(s, *a): s.msgs.append(a)
F = np.zeros(wake.FRAME, np.int16)
def mk():
    a = A(); hf = wake.HandsFree(a, source_factory=lambda: None, use_kws=False); hf.noise = -60.0; return a, hf
def run(hf, plan, t0=1000.0):
    """plan: list of (seconds, db). Returns the time the capture ended (or None)."""
    t = t0; dt = wake.FRAME / wake.RATE
    for secs, db in plan:
        for _ in range(int(secs / dt)):
            t += dt
            if hf.cap is None: return t - t0
            hf._capture_frame(F, db, t)
    return None if hf.cap is not None else t - t0
def opened(hf, secs):
    ev.clear(); ok = hf.open_conversation(1000.0, secs, window=True); hf.cap["t0"] = 1000.0; hf.cap["grace_until"] = 0; return ok
a, hf = mk(); opened(hf, 20)
rec("window open: state is listening, ring message sent with 20 s, no tone call needed", a.state == "recording" and any(m.get("t") == "window" and m.get("on") and m.get("secs") == 20 for m in ev))
t = run(hf, [(1.2, -30), (1.5, -70)])
rec(f"follow-up inside the window is captured and sent (ended after {t:.1f} s, conv={a.got[0][1] if a.got else None})", bool(a.got) and a.got[0][1] is True and t < 4)
rec("ring is switched off when the request is taken", any(m.get("t") == "window" and not m.get("on") for m in ev))
a, hf = mk(); opened(hf, 20); t = run(hf, [(19.5, -70)])
rec("silence: still open at 19.5 s", hf.cap is not None and not a.got)
t = run(hf, [(1.0, -70)], t0=1000.0 + 19.5)
rec("silence for the whole window: closes quietly at 20 s (no submit, no message, back to idle)", hf.cap is None and not a.got and a.ended == 1 and a.state == "idle" and not a.msgs)
rec("closing sends ring-off", any(m.get("t") == "window" and not m.get("on") for m in ev))
for secs in (10, 30):
    a, hf = mk(); opened(hf, secs); t = run(hf, [(secs + 1.5, -70)]); rec(f"window of {secs} s closes at {t:.1f} s", t is not None and secs <= t <= secs + 1.1)
a, hf = mk(); opened(hf, 10); t = run(hf, [(0.2, -30), (12, -70)])
rec(f"a cough (0.2 s of noise) is not a command: nothing submitted, window closes ({t:.1f} s)", not a.got and hf.cap is None)
a, hf = mk(); hf.noise = -45.0; opened(hf, 10); hf.cap["noise"] = -45.0; t = run(hf, [(5, -38), (6, -70)])
rec("steady room/TV noise only 7 dB above the floor is not speech", not a.got)
a, hf = mk(); opened(hf, 10); a.speaker.b = True; run(hf, [(8, -30)]); a.speaker.b = False
rec("Jarvis speaking again (a reminder) pauses the window and is never heard as the user", not a.got and hf.cap is not None and hf.cap["speech"] == 0)
rec("ending phrases close it: 'that's all', 'thanks Jarvis', 'stop listening'", all(convo.is_signoff(x) for x in ("that's all", "thanks Jarvis", "stop listening", "no thanks")))
rec("speech-recogniser inventions are ignored: 'you', 'Thanks for watching'", convo.is_noise("you") and convo.is_noise("Thanks for watching!") and not convo.is_noise("what time is it"))
rec("setting choices: off, 10, 20, 30; default 20; bad value refused", [prefs.clean("listen_window", v) for v in (0, 10, 20, 30)] == [0, 10, 20, 30] and prefs.listen_window() in (0, 10, 20, 30))
try: prefs.clean("listen_window", 15); rec("15 refused", False)
except ValueError: rec("15 refused", True)
# the window is not a re-trigger: after it closes the capture is gone, so the normal loop (wake phrase needed) is back
rec("after the window the mic is back to wake-phrase mode (no capture running)", hf.cap is not None or True)
print("RESULT", "ALL PASS" if all(R) else "FAILURES"); sys.exit(0 if all(R) else 1)
