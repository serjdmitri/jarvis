"""A7: maps by voice (how long / how far / show me / go to / walk), nearest match, alternatives, car default + short walk, flight estimate, no-traffic wording, fallback, cache, preload. Fake network.
Run: .venv/bin/python tests/maps_a7_test.py"""
import os, sys, time, tempfile, json
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="jt-cfg-")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from jarvis import maps, skills, settings, tilecache, opener
R = []
def rec(n, ok): R.append(ok); print(("PASS " if ok else "FAIL ") + n, flush=True)
ME = (25.79, -80.13)
settings.update(home_lat=ME[0], home_lon=ME[1], home_area="Miami Beach, Florida, United States", units="imperial")
HITS = []; OSRM_DOWN = {"v": False}
def hit(name, lat, lon, road, cls="amenity", typ="cafe", bbox=None): return {"name": name, "lat": str(lat), "lon": str(lon), "display_name": f"{name}, {road}", "address": {"road": road}, "class": cls, "type": typ, "boundingbox": bbox}
def fake_get(url, timeout=6):
    HITS.append(url)
    if "nominatim" in url:
        q = url.split("q=")[-1].lower()
        if "starbucks" in q and "bounded=1" in url: return [hit("Starbucks", 25.92, -80.12, "Collins Avenue"), hit("Starbucks", 25.80, -80.13, "Lincoln Road"), hit("Starbucks", 25.85, -80.15, "Alton Road")]
        if "airport" in q and "bounded=1" in url: return [hit("Miami International Airport", 25.795, -80.287, "NW 42nd Ave", "aeroway", "aerodrome")]
        if "bank" in q and "bounded=1" in url: return [hit("Chase", 25.7915, -80.1305, "Lincoln Road", "amenity", "bank")]
        if "bounded=1" in url: return []
        if "tokyo" in q: return [hit("Tokyo", 35.68, 139.76, "", "boundary", "administrative", ["35.5", "35.8", "139.5", "139.9"])]
        if "new%20york" in q or "new york" in q: return [hit("New York", 40.71, -74.0, "", "boundary", "administrative", ["40.5", "40.9", "-74.3", "-73.7"])]
        if "orlando" in q: return [hit("Orlando", 28.54, -81.38, "", "boundary", "administrative", ["28.3", "28.6", "-81.6", "-81.2"])]
        if "paris" in q: return [hit("Paris", 48.85, 2.35, "", "boundary", "administrative", ["48.8", "48.9", "2.2", "2.5"])]
        if "eiffel" in q: return [hit("Eiffel Tower", 48.8584, 2.2945, "Avenue Anatole France", "tourism", "attraction")]
        if "sleep" in q: return [hit("Sleep Inn", 40.0, -75.0, "Main", "tourism", "hotel")] if False else [dict(hit("sleep", 40.0, -75.0, "x", "highway", "residential"))]
        return []
    if "router.project-osrm" in url or "routed-" in url:
        if OSRM_DOWN["v"]: raise OSError("down")
        a = url.split("/")[-1].split("?")[0].split(";"); (lo1, la1), (lo2, la2) = [tuple(map(float, x.split(","))) for x in a]
        d = maps.haversine((la1, lo1), (la2, lo2)) * 1.25
        if d > 4_000_000: raise OSError("no route")
        sp = 13.0 if "driving" in url else (1.4 if "foot" in url else 4.2)
        return {"routes": [{"distance": d, "duration": d / sp, "geometry": {"coordinates": [[lo1, la1], [lo2, la2]]}}]}
    return {}
maps._get = fake_get; maps.my_location = lambda: {"lat": ME[0], "lon": ME[1], "label": "Miami Beach", "source": "home"}
maps._polite = lambda h: None
TILES = []
tilecache.get = lambda src, path, **k: (TILES.append(path), (b"{\"tiles\":[\"https://tiles.openfreemap.org/planet/v1/{z}/{x}/{y}.pbf\"]}" if path == "planet" else b"x"), "", "")[1:]
# ---- phrases
P = [("how long will it take to get to Starbucks", "directions"), ("how far is the airport", "directions"), ("show me Tokyo", "place_view"), ("go to Paris", "place_view"), ("take me to Starbucks", "directions"),
     ("directions to Starbucks", "directions"), ("time to get to the airport", "directions"), ("show the Eiffel Tower", "place_view"), ("how long would it take to walk to the bank", "directions")]
for t, k in P:
    r = maps.parse_command(t); rec(f"'{t}' -> {k}" + (f" (mode {r.get('mode')})" if r else ""), bool(r) and r["kind"] == k)
rec("'show me my reminders', 'show me pictures of cats', 'show me the weather' are NOT maps", all(maps.parse_command(t) is None for t in ("show me my reminders", "show me pictures of cats", "show me the weather")))
# ---- Starbucks: nearest, which one, alternatives, default car, times for all three, no-traffic wording
t0 = time.time(); r = skills.maps_command("how long will it take to get to Starbucks"); dt = time.time() - t0
sid = r["open"]["id"]; spec = maps.SPECS[sid]
rec(f"nearest Starbucks picked and named with its street ({r['speech'][:70]}...)", "Starbucks on Lincoln Road" in r["speech"] and abs(spec["dest"]["lat"] - 25.80) < 1e-6)
rec("alternatives kept behind the arrows (3, nearest first)", len(spec["alts"]) == 3 and spec["alts"][0]["dist_m"] <= spec["alts"][1]["dist_m"] <= spec["alts"][2]["dist_m"] and "arrows" in r["speech"])
rec(f"default mode: car (walk is long); all three times present; 'no live traffic' said plainly", spec["mode"] == "car" and all(spec["routes"][m] for m in ("car", "foot", "bike")) and "no live traffic" in r["speech"].lower())
rec("no flight estimate for a short trip", spec["flight"] is None)
rec(f"done in {dt:.2f}s with fake network; destination tiles preloaded ({len(TILES)} tile requests)", len(TILES) >= 20)
n = len(HITS); r2 = skills.maps_command("how long to Starbucks"); rec("the same trip again hits the route cache (no new route requests)", sum(1 for u in HITS[n:] if "routed" in u or "osrm" in u) == 0)
# alternatives
sp = maps.pick_alt(sid, 1); rec("arrow: next match recomputes the routes", sp and sp["alt_i"] == 1 and sp["dest"]["lat"] != 25.80)
# ---- very short trip: walk first
r = skills.maps_command("how long to walk to the bank"); sp = maps.SPECS[r["open"]["id"]]
rec(f"very short trip: on foot first ({r['speech'][:60]})", sp["mode"] == "foot")
r = skills.maps_command("how far is the airport"); sp = maps.SPECS[r["open"]["id"]]
rec(f"medium trip stays on car ({sp['mode']})", sp["mode"] == "car")
# ---- voice mode switch
r = skills.maps_command("walk"); rec(f"'walk' switches the open route to walking ({r['speech'][:50]})", r and maps.SPECS[maps.LAST_SPEC['id']]["mode"] == "foot")
r = skills.maps_command("bike"); rec("'bike' and 'car' work too", r and maps.SPECS[maps.LAST_SPEC['id']]["mode"] == "bike" and skills.maps_command("car") and maps.SPECS[maps.LAST_SPEC['id']]["mode"] == "car")
# ---- far trip: flight estimate
r = skills.maps_command("how long to New York"); sp = maps.SPECS[r["open"]["id"]]
rec(f"far trip (~{sp['flight']['dist_m']/1000:.0f} km): flight estimate, labelled approximate ({[p for p in r['speech'].split('. ') if 'air' in p.lower()][:1]})", sp["flight"] and "approximate" in r["speech"].lower())
r = skills.maps_command("how long to Tokyo"); sp = maps.SPECS[r["open"]["id"]]
rec("across an ocean: no made-up drive or walk, only the flight", sp["flight"] and not any(sp["routes"].values()) and "by car" not in r["speech"])
# ---- routing down: labelled straight-line estimate
OSRM_DOWN["v"] = True; maps._RCACHE.clear()
r = skills.maps_command("how long to Starbucks"); sp = maps.SPECS[r["open"]["id"]]
rec(f"routing service down: straight-line estimate, said plainly ({r['speech'][:80]})", sp["routes"]["car"]["approx"] and "rough" in r["speech"].lower())
OSRM_DOWN["v"] = False
# ---- places around the world
r = skills.maps_command("show me Tokyo"); sp = maps.SPECS[r["open"]["id"]]
rec(f"'show me Tokyo': a flying map with the city framed, distance said ({r['speech']})", sp["fly"] and sp["dest"]["bbox"] and "Tokyo" in r["speech"] and "kilometres" in r["speech"] or "miles" in r["speech"])
r = skills.maps_command("go to Paris"); rec("'go to Paris'", r and "Paris" in r["speech"] and r.get("open"))
r = skills.maps_command("show the Eiffel Tower"); rec("'show the Eiffel Tower'", r and "Eiffel Tower" in r["speech"])
rec("'go to sleep' is not a place: falls through to the other skills", skills.maps_command("go to sleep") is None)
# ---- the page uses the new pieces
mp = open(ROOT + "/jarvis/web/map.html").read(); ap = open(ROOT + "/jarvis/web/app.js").read(); css = open(ROOT + "/jarvis/web/style.css").read()
rec("map page: float mode, fly-to arc scaled by distance, chips, flight line, arrows", all(k in mp for k in ("float", "flyTo", "curve: 1.6", "AIR ~", "aprev", "NO LIVE TRAFFIC")))
rec("floating map: no backdrop-filter / blur anywhere in it", "backdrop-filter" not in mp and "blur(" not in mp and "backdrop-filter" not in css.split("#mapfloat")[-1])
print("RESULT", "ALL PASS" if all(R) else "FAILURES"); sys.exit(0 if all(R) else 1)
