"""iPhone location endpoint + how it is used (PROMPT26 0.10). A fake POST with the secret; wrong secret; bad numbers; age and accuracy; priority over home/IP; nothing in logs.
Run: .venv/bin/python tests/location_test.py"""
import asyncio, io, json, os, sys, tempfile, time, pathlib, contextlib
os.environ["JARVIS_DATA_DIR"] = tempfile.mkdtemp(prefix="loctest-")
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="loccfg-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from jarvis import voiceapi, location, maps, settings
R = []
def rec(n, ok, extra=""): R.append(ok); print(("PASS " if ok else "FAIL ") + n, extra)
async def main():
    sec = voiceapi.secret()
    app = web.Application(); api = voiceapi.Api(None, lambda *a: None)
    app.router.add_route("*", "/{secret}/location", api.location)
    out = io.StringIO()
    async with TestClient(TestServer(app)) as c:
        with contextlib.redirect_stdout(out):
            voiceapi._last.update(t=0, hour=[])
            r = await c.post(f"/{sec}/location", json={"lat": 25.7617, "lon": -80.1918, "acc": 12.5}); rec("good fix accepted", r.status == 200 and (await r.text()) == "ok")
            voiceapi._last.update(t=0, hour=[])
            r = await c.post("/wrongsecret/location", json={"lat": 1, "lon": 2}); rec("wrong secret: 404, no hint", r.status == 404)
            voiceapi._last.update(t=0, hour=[])
            r = await c.get(f"/{sec}/location"); rec("GET: 404", r.status == 404)
            voiceapi._last.update(t=0, hour=[])
            r = await c.post(f"/{sec}/location", json={"lat": 95, "lon": 10}); rec("latitude 95 rejected", r.status == 400)
            voiceapi._last.update(t=0, hour=[])
            r = await c.post(f"/{sec}/location", json={"lat": 0, "lon": 0}); rec("0,0 rejected", r.status == 400)
            voiceapi._last.update(t=0, hour=[])
            r = await c.post(f"/{sec}/location", data="not a location"); rec("garbage rejected", r.status == 400)
            voiceapi._last.update(t=0, hour=[])
            r = await c.post(f"/{sec}/location", data="25.76,-80.19"); rec("plain 'lat,lon' text accepted", r.status == 200)
            voiceapi._last.update(t=0, hour=[])
            r = await c.post(f"/{sec}/location", json={"lat": 25.7617, "lon": -80.1918, "acc": 12.5, "t": time.time() - 180}); rec("fix with its own time accepted", r.status == 200)
    me = maps.my_location(); rec("my_location prefers the phone fix", me and me["source"] == "iphone" and abs(me["lat"] - 25.7617) < 1e-4, str({k: me[k] for k in ("source", "acc", "age")}))
    rec("age is shown ('3 min ago')", me["age"] == "3 min ago", me["age"])
    sp = maps.me_spec(); rec("map spec carries accuracy + source", sp["origin"]["acc"] == 12.5 and sp["origin"]["source"] == "iphone" and not sp.get("approx"))
    settings.update(home_area="Test Town", home_lat=10.0, home_lon=10.0)
    rec("phone fix still beats the saved home area", maps.my_location()["source"] == "iphone")
    d = json.load(open(location.FILE)); old = dict(d, t=time.time() - 13 * 3600); json.dump(old, open(location.FILE, "w"))
    rec("a fix older than 12 h is ignored (home area used)", maps.my_location()["source"] == "home")
    mode = oct(os.stat(location.FILE).st_mode & 0o777); rec("file mode 600", mode == "0o600", mode)
    rec("only the latest fix is stored (one small file, no history)", sorted(p.name for p in pathlib.Path(os.environ["JARVIS_DATA_DIR"]).glob("location*")) == ["location.json"])
    rec("nothing printed to the log", "25.76" not in out.getvalue() and "-80.19" not in out.getvalue())
    rec("forget works", location.forget() and location.latest() is None)
    print(f"{sum(R)}/{len(R)} passed")
asyncio.run(main())
