"""Claude Code status endpoint + notifier rules. Needs bin/jarvis-testserver running (port 8799). Run: .venv/bin/python tests/claudestat_test.py"""
import asyncio, glob, json, os, sys, time, pathlib, aiohttp
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
T = sorted(glob.glob("/tmp/jarvis-test.*"))[0]
R = []
def rec(n, ok, extra=""): R.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n, extra)
async def main():
    base = "http://127.0.0.1:8799"
    async with aiohttp.ClientSession() as s, s.ws_connect(base + "/ws", headers={"Origin": base}) as w:
        r = await s.post(base + "/hook/not-the-token", json={"event": "done"}); rec("wrong token: 404", r.status == 404)
        tok = open(T + "/data/hook_token").read().strip(); rec("token file mode 600", oct(os.stat(T + "/data/hook_token").st_mode & 0o777) == "0o600")
        async def got(ev, msg=""):
            r = await s.post(f"{base}/hook/{tok}", json={"event": ev, "who": "claude", "message": msg}); ok = r.status == 200
            for _ in range(300):
                try: m = await asyncio.wait_for(w.receive_json(), 0.5)
                except asyncio.TimeoutError: break
                if m.get("t") == "claudestat": return ok, m
            return ok, None
        for ev, want in (("working", "working"), ("needs", "needs"), ("done", "done"), ("fail", "error"), ("end", "idle")):
            ok, m = await got(ev, "Run tests?" if ev == "needs" else ""); rec(f"event {ev} -> {want} broadcast", ok and m and m["state"] == want, str(m and m.get("msg")))
        r = await s.post(f"{base}/hook/{tok}", json={"event": "rm -rf /"}); rec("unknown event refused (400)", r.status == 400)
        r = await s.post(f"{base}/hook/{tok}", data="not json"); rec("bad body refused (400)", r.status == 400)
        r = await s.post(f"{base}/hook/{tok}", json={"event": "needs", "message": "x" * 500 + "\n\x00"}); 
        ok, m = True, None
        for _ in range(10):
            try: m = await asyncio.wait_for(w.receive_json(), 0.5)
            except asyncio.TimeoutError: break
            if m.get("t") == "claudestat": break
        rec("long text cut to 120, control chars removed", m and len(m["msg"]) <= 120 and "\x00" not in m["msg"])
    # notifier rules (no network: toggles and quiet hours decide before anything is sent)
    os.environ["XDG_CONFIG_HOME"] = T + "/cfg"
    from jarvis import notifier, settings
    rec("claude category off by default", notifier.send("claude", "t", "m")["message"].endswith("are off"))
    rec("safe_text blocks links", notifier.safe_text("see https://x.example/a?b=1") == "Something needs you on the Go.")
    rec("safe_text blocks email addresses", notifier.safe_text("from bob@example.com") == "Something needs you on the Go.")
    rec("safe_text cuts to 140", len(notifier.safe_text("a" * 400)) == 140)
    settings.update(notify_quiet_from="22:00", notify_quiet_to="07:00", notify_briefing=True)
    at = lambda h: time.mktime(time.strptime(f"2026-11-21 {h}", "%Y-%m-%d %H:%M"))
    rec("quiet hours 23:00 inside", notifier.in_quiet(at("23:00"))); rec("quiet hours 12:00 outside", not notifier.in_quiet(at("12:00"))); rec("quiet hours 06:30 inside (overnight)", notifier.in_quiet(at("06:30")))
    rec("briefing skipped in quiet hours", notifier.send("briefing", "Morning", "Weather and 2 reminders", now=at("23:30"))["message"] == "quiet hours")
    rec("unknown category refused", not notifier.send("spam", "a", "b")["ok"])
    print(f"{sum(R)}/{len(R)} passed")
asyncio.run(main())
