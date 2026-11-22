"""The camera source decision, in-process, with a STUB screenshot (no screenshot of your screen is ever taken). Run: .venv/bin/python tests/camera_source.py"""
import os, sys, tempfile
os.environ["JARVIS_DATA_DIR"] = tempfile.mkdtemp(); os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jarvis import core, phonecam, camera, settings, prefs
SHOTS = []
core.screenshot = lambda kind="screen": (SHOTS.append(kind), "/tmp/stub-shot.jpg")[1]
class A:                                    # just enough of the assistant for grab()
    source = "auto"; last_source = ""
    grab = core.Assistant.grab; camera_chip = core.Assistant.camera_chip
    def __init__(self): self.chips = []
def run(name, fn, want):
    a = A(); import jarvis.opener as op; chips = []; op.NOTIFY = lambda m: chips.append(m)
    try: res = ("ok", fn(a), a.last_source)
    except RuntimeError as e: res = ("err", str(e), a.last_source)
    ok = want(res, SHOTS, chips); print(("PASS " if ok else "FAIL ") + name + "  ->  " + str(res[2] or res[1])[:80] + f"  shots={SHOTS} chips={len(chips)}"); SHOTS.clear(); return ok
R = []
camera.usb_webcams = lambda: []                     # no USB webcam anywhere
phonecam.grab = lambda timeout=8: "/tmp/phone.jpg"
settings.update(phone_cam_url="http://192.168.1.235:4747/video")
R.append(run("phone saved and reachable: the phone camera, no screenshot", lambda a: a.grab(), lambda r, s, c: r[2] == "phone camera" and not s))
phonecam.grab = lambda timeout=8: (_ for _ in ()).throw(RuntimeError("timed out"))
R.append(run("phone saved but unreachable: an error with the reason, NEVER the desktop", lambda a: a.grab(), lambda r, s, c: r[0] == "err" and "can't reach the phone camera" in r[1] and not s))
R.append(run("... even with source=camera / phone / auto", lambda a: (setattr(a, "source", "phone"), a.grab())[1], lambda r, s, c: r[0] == "err" and not s))
R.append(run("explicit screen request with a phone saved: the desktop, on purpose", lambda a: a.grab(explicit_screen=True), lambda r, s, c: r[2] == "screenshot of the desktop" and s == ["screen"]))
R.append(run("source SCREEN chosen: the desktop", lambda a: (setattr(a, "source", "screen"), a.grab())[1], lambda r, s, c: r[2] == "screenshot of the desktop"))
settings.update(phone_cam_url=None)
R.append(run("no camera at all: the desktop and it says so, warnings off -> no chip", lambda a: a.grab(), lambda r, s, c: r[2] == "screenshot of the desktop" and not c))
settings.update(camera_warnings=True)
R.append(run("no camera, warnings ON -> exactly one quiet chip", lambda a: a.grab(), lambda r, s, c: len(c) == 1 and c[0]["t"] == "health"))
R.append(run("camera button with no camera: an error and a chip (once per 10 minutes), no desktop", lambda a: a.grab(camera_only=True), lambda r, s, c: r[0] == "err" and not s))
camera.usb_webcams = lambda: ["/dev/video0"]; camera.capture = lambda: "/tmp/usb.jpg"
R.append(run("USB webcam only when no phone is saved", lambda a: a.grab(), lambda r, s, c: r[2] == "USB camera" and not s))
print(f"\n{sum(R)}/{len(R)} passed"); sys.exit(0 if all(R) else 1)
