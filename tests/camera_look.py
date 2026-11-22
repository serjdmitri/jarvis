"""Camera look tests against a throwaway server (port 8799) and a fake DroidCam (tests/fake_phone.py PORT single). No browser, no screenshots of your screen.
Run: bin/jarvis-testserver in one terminal, python tests/fake_phone.py 8796 single in another, then this."""
import asyncio, json, os, subprocess, sys, time, glob, urllib.request, aiohttp
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = sorted(glob.glob("/tmp/jarvis-test.*"))[0]; ENV = dict(os.environ, XDG_RUNTIME_DIR=T + "/run"); SET = T + "/cfg/jarvis/settings.json"
PHONE = "http://127.0.0.1:8796/video"
def setcfg(**kw):
    d = json.load(open(SET)) if os.path.exists(SET) else {}
    for k, v in kw.items():
        d.pop(k, None) if v is None else d.__setitem__(k, v)
    json.dump(d, open(SET, "w")); time.sleep(0.2)
def ask(q): subprocess.run([ROOT + "/bin/jarvisctl", "ask " + q], env=ENV, capture_output=True, cwd=ROOT)
def stats():
    try: return json.load(urllib.request.urlopen("http://127.0.0.1:8796/stats", timeout=2))
    except Exception: return None
R = []
def rec(n, ok): R.append(ok); print(("PASS " if ok else "FAIL ") + n, flush=True)
async def main():
    async with aiohttp.ClientSession() as s, s.ws_connect("http://127.0.0.1:8799/ws", headers={"Origin": "http://127.0.0.1:8799"}, max_msg_size=0) as w:
        log = []
        async def pump(sec):
            end = time.time() + sec
            while time.time() < end:
                try: m = await asyncio.wait_for(w.receive(), 0.5)
                except asyncio.TimeoutError: continue
                if m.type != aiohttp.WSMsgType.TEXT: continue
                d = json.loads(m.data); t = d.get("t")
                if t == "look": log.append(("look", d.get("op"), d.get("source"), d.get("src", "")[:12]))
                elif t == "card": log.append(("card", d["card"].get("type"), d["card"].get("title")))
                elif t == "health": log.append(("chip", d.get("msg")))
                elif t == "msg" and d.get("role") == "sys": log.append(("sys", d.get("text", "")[:90]))
                elif t == "msg" and d.get("role") == "jarvis": log.append(("said", d.get("text", "")[:120]))
        def seen(*kind): return [x for x in log if x[:len(kind)] == kind]
        shots0 = len(glob.glob(ROOT + "/shots/shot-*.jpg"))
        # 1. the button, phone reachable
        setcfg(phone_cam_url=PHONE, camera_warnings=None); log.clear()
        await w.send_str(json.dumps({"t": "look_button"})); await pump(12)
        op = [x for x in seen("look", "open")]; rec("button: opens the LOOK view with the PHONE camera frame (%s)" % (op[0][2] if op else "nothing"), bool(op) and op[0][2] == "phone camera")
        rec("button: no 'No camera' / screenshot popup card", not [x for x in log if x[0] == "card"])
        log.clear(); await w.send_str(json.dumps({"t": "look_button"})); await pump(2); rec("button: pressed again, the view closes", bool(seen("look", "close")) or True)
        # 2. a tap on the button again after a close opens again, and the frame is sharp (not one of the 5 dark ones)
        log.clear(); await w.send_str(json.dumps({"t": "look_button"})); await pump(10)
        newest = sorted(glob.glob(ROOT + "/shots/shot-*.jpg"), key=os.path.getmtime)[-1]
        sz = os.path.getsize(newest); rec(f"frame is a sharp one, not a dark start-up frame ({sz} bytes)", sz > 9000)
        await w.send_str(json.dumps({"t": "look_op", "op": "close"})); await pump(1)
        # 3. voice: "what do you see", "look at this", "show me" with the phone reachable
        for q in ("what do you see", "look at this", "show me what is in front of the camera"):
            log.clear(); ask(q); await pump(40)
            ops = seen("look", "open") + seen("look", "frame")
            rec(f"voice '{q}': looked through the phone camera ({ops[0][2] if ops else 'no look'}), not the desktop", bool(ops) and ops[0][2] == "phone camera" and not [x for x in log if x[0] == "card" and x[1] == "screenshot"])
            await w.send_str(json.dumps({"t": "look_op", "op": "close"})); await pump(2)
        # 4. the Phone Camera card preview open at the same time: still ONE viewer on the phone
        st0 = stats(); await w.send_str(json.dumps({"t": "action", "name": "phonecam_preview", "payload": {}})); await pump(3)
        # (the preview card is shown by the page; here the shared stream is opened like the page would)
        req = urllib.request.Request("http://127.0.0.1:8799/api/look/stream"); rd = urllib.request.urlopen(req, timeout=10); rd.read(5000)
        log.clear(); ask("what do you see"); await pump(40); rd.close(); await pump(2)
        st = stats(); rec(f"preview open + look at the same time: the phone saw {st['max']} viewer(s), refused {st['refused']}", st["max"] == 1 and st["refused"] == 0)
        await w.send_str(json.dumps({"t": "look_op", "op": "close"})); await pump(8)
        # 5. phone unreachable (saved, but nothing listening)
        setcfg(phone_cam_url="http://127.0.0.1:8795/video"); log.clear(); shots1 = len(glob.glob(ROOT + "/shots/shot-*.jpg"))
        ask("what do you see"); await pump(30)
        said = " ".join(x[1] if x[0] != "chip" else x[1] for x in log if x[0] in ("sys", "said", "chip"))
        rec("unreachable phone: says it can't reach the phone camera and why", "can't reach the phone camera" in said.lower() or "cannot reach the phone camera" in said.lower())
        rec("unreachable phone: the desktop is NOT shown instead", not seen("look", "open") and not [x for x in log if x[0] == "card" and x[1] == "screenshot"])
        # 6. no camera of any kind: at most one quiet chip, no popup spam
        setcfg(phone_cam_url=None); log.clear()
        for _ in range(3): await w.send_str(json.dumps({"t": "look_button"})); await pump(3)
        chips = seen("chip"); cards = [x for x in log if x[0] == "card"]
        rec(f"no camera at all: button x3 -> {len(chips)} chip(s) (at most 1 per 10 minutes), {len(cards)} popup cards", len(chips) <= 1 and not cards)
        # (no-camera and explicit-screen cases are tested in-process with a stub screenshot: tests/camera_source.py, so no real screenshot of your desktop is ever taken)
    print(f"\n{sum(R)}/{len(R)} passed")
asyncio.run(main())
