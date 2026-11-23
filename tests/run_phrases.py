"""Runs tests/phrases against the throwaway test server (bin/jarvis-testserver must be running; the brain is switched off there so a phrase that reaches the model answers at once and is labelled "brain").
PASS or FAIL per phrase. Phrases that would change the real system (volume, power) are not in the file.   Run: .venv/bin/python tests/run_phrases.py [filter]"""
import asyncio, glob, json, os, subprocess, sys, time, aiohttp
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = sorted(glob.glob("/tmp/jarvis-test.*"))[0]; ENV = dict(os.environ, XDG_RUNTIME_DIR=T + "/run")
open(T + "/fake-brain", "w").close()
def _ctl(c): return subprocess.run([ROOT + "/bin/jarvisctl", c], env=ENV, capture_output=True, cwd=ROOT, text=True).stdout
async def ctl(c): return await asyncio.to_thread(_ctl, c)        # off the event loop: the websocket must keep answering the server's pings
CAM_OPEN = {"close the camera", "clear highlights", "scan everything on my desk", "stop looking"}
async def one(ph, want):
    """A fresh websocket per phrase (the server closes idle clients; a long run must not depend on one connection)."""
    async with aiohttp.ClientSession() as s, s.ws_connect("http://127.0.0.1:8799/ws", headers={"Origin": "http://127.0.0.1:8799"}, max_msg_size=0) as w:
        await ctl("testlook " + ("open" if ph in CAM_OPEN else "closed"))
        while True:                                           # drain old messages
            try: await asyncio.wait_for(w.receive(), 0.05)
            except asyncio.TimeoutError: break
        await ctl("ask " + ph); got = None; end = time.time() + 9
        while time.time() < end and not got:
            try: m = await asyncio.wait_for(w.receive(), 0.3)
            except asyncio.TimeoutError: continue
            if m.type != aiohttp.WSMsgType.TEXT: continue
            d = json.loads(m.data)
            if d.get("t") == "heard" and d.get("route"): got = d["route"]
        return got or "(nothing)"
async def main():
    rows = [l.rstrip("\n").split("\t") for l in open(ROOT + "/tests/phrases") if l.strip() and not l.startswith("#")]
    if len(sys.argv) > 1: rows = [r for r in rows if sys.argv[1] in r[0]]
    ok = bad = 0
    for ph, want in rows:
        try: got = await one(ph, want)
        except Exception as e: got = "(error " + type(e).__name__ + ")"
        if got.startswith("brain:"): got = "brain"
        good = got == want
        ok += good; bad += not good
        print(("PASS " if good else "FAIL ") + f"{ph!r:60} -> {got}" + ("" if good else f"   (wanted {want})"), flush=True)
        for _ in range(40):                                   # let the turn finish (idle again) before the next phrase
            await asyncio.sleep(0.25)
            if "[idle]" in await ctl("status"): break
        await asyncio.sleep(0.4)
    print(f"RESULT {ok} pass, {bad} fail of {ok + bad}")
    sys.exit(1 if bad else 0)
asyncio.run(main())
