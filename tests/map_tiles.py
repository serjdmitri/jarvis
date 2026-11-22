"""Map tiles through the Go: status codes at several zoom levels (the evidence PROMPT23 item 2 asks for), the disk cache, and offline use of cached tiles. Run: .venv/bin/python tests/map_tiles.py [port]"""
import sys, urllib.request, urllib.error, time, shutil, tempfile, pathlib, os
os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="tiletest-"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from jarvis import tilecache as tc
tc.DIR = pathlib.Path(tempfile.mkdtemp(prefix="tilecache-"))
ok = bad = 0
def check(name, cond, extra=""):
    global ok, bad
    ok += bool(cond); bad += not cond
    print(("PASS " if cond else "FAIL ") + name, extra)
# raster tiles around Miami at zoom 2..19 from each source
import math
def xy(lat, lon, z):
    n = 2 ** z; return int((lon + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
for src, fmt in (("osm", "{z}/{x}/{y}.png"), ("esri", "{z}/{y}/{x}")):
    for z in (2, 8, 14, 17, 19):
        if src == "esri" and z > 16: continue
        x, y = xy(25.7743, -80.1937, z)
        p = fmt.format(z=z, x=x, y=y)
        try:
            d, ct, how = tc.get(src, p); check(f"{src} z{z}", len(d) > 100 and ct.startswith("image"), f"{len(d)} bytes {how} status {tc.LAST[-1][1]}")
        except OSError as e:
            check(f"{src} z{z}", False, str(e))
        time.sleep(.15)
# vector tile + style + font
x, y = xy(25.7743, -80.1937, 14)
import json
j = json.loads(tc.get("ofm", "planet")[0]); ver = j["tiles"][0].split("/planet/")[1].split("/")[0]
d, ct, how = tc.get("ofm", f"planet/{ver}/14/{x}/{y}.pbf"); check("vector tile z14", len(d) > 500, f"{len(d)} bytes {how}")
d, ct, how = tc.get("ofm", "fonts/Noto Sans Regular/0-255.pbf"); check("font", len(d) > 1000, f"{len(d)} bytes")
# cached copy is used when the network is down
tc.SOURCES["osm"] = "http://127.0.0.1:9/"          # unreachable
x, y = xy(25.7743, -80.1937, 17)
d, ct, how = tc.get("osm", f"2/{xy(25.7743,-80.1937,2)[0]}/{xy(25.7743,-80.1937,2)[1]}.png", fresh_s=0)
check("offline: stale copy served", how == "stale" and len(d) > 100, how)
try:
    tc.get("osm", "9/1/1.png"); check("offline: unknown tile fails cleanly", False)
except OSError:
    check("offline: unknown tile fails cleanly", True)
for bad_path in ("../etc/passwd", "a/b/../../c"):
    try: tc.get("osm", bad_path); check("path escape refused " + bad_path, False)
    except LookupError: check("path escape refused " + bad_path, True)
try: tc.get("evil", "1/1/1.png"); check("unknown source refused", False)
except LookupError: check("unknown source refused", True)
# cap
tc.MAX_BYTES = 1000; tc._evict(); check("cache cap evicts", tc.cache_size() <= 1000 + 60000, str(tc.cache_size()))
print(f"{ok} passed, {bad} failed"); shutil.rmtree(tc.DIR, ignore_errors=True)
