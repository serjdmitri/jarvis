"""Map tiles through the Go: the page asks 127.0.0.1, this fetches from the real tile servers (keyless, allowlisted hosts only), keeps what it fetched on disk (100 MB at most, oldest removed
first, in cache/tiles, outside git) and serves the copy when the network is down. Nothing about you is sent: only the tile path, a plain User-Agent and no cookies.
Sources: OpenFreeMap vector tiles (style, sprite, fonts, tiles), OpenStreetMap raster tiles, Esri World Dark Gray. (CARTO's dark tiles now answer 200 with the same 2.5 KB placeholder image for every tile: unusable without a key, not used.) Usage policies: OpenFreeMap is free for all uses, OSM's
tile server allows light use with a real User-Agent (this is one person's map, cached), Esri is used only as a fallback."""
import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

DIR = config.ROOT / "cache" / "tiles"
MAX_BYTES = 100 * 1024 * 1024
UA = "jarvis-local-assistant/0.1 (personal desk assistant; cached tiles)"
SOURCES = {
    "ofm": "https://tiles.openfreemap.org/",
    "osm": "https://tile.openstreetmap.org/",
    "esri": "https://services.arcgisonline.com/arcgis/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/",
}
OK_PATH = re.compile(r"^[A-Za-z0-9_./%{}\- ]{1,200}$")
TYPES = {".pbf": "application/x-protobuf", ".png": "image/png", ".json": "application/json", ".jpg": "image/jpeg", ".webp": "image/webp"}
_lock = threading.Lock()
_size = {"n": None}
STATS = {"hit": 0, "miss": 0, "stale": 0, "fail": 0}
LAST = []                                  # the last few (source, status) pairs: evidence for the tests


def _file(src, path):
    return DIR / (src + "-" + hashlib.sha1(path.encode()).hexdigest()[:24])


def cache_size():
    try:
        return sum(f.stat().st_size for f in DIR.glob("*") if f.is_file())
    except OSError:
        return 0


def _evict():
    with _lock:
        files = sorted((f for f in DIR.glob("*") if f.is_file()), key=lambda f: f.stat().st_atime)
        total = sum(f.stat().st_size for f in files)
        for f in files:
            if total <= MAX_BYTES:
                break
            total -= f.stat().st_size
            f.unlink(missing_ok=True)


def clear():
    n = 0
    for f in DIR.glob("*"):
        try:
            f.unlink(); n += 1
        except OSError:
            pass
    return n


def get(src, path, fresh_s=7 * 86400):
    """-> (bytes, content_type, how) with how in hit | miss | stale; raises LookupError when the source is unknown, OSError when there is nothing at all."""
    if src not in SOURCES or ".." in path or not OK_PATH.match(path):
        raise LookupError("not a map source")
    f = _file(src, path)
    ctype = TYPES.get("." + path.rsplit(".", 1)[-1].lower(), "application/octet-stream") if "." in path.rsplit("/", 1)[-1] else "image/png"
    have = f.is_file()
    if have and time.time() - f.stat().st_mtime < fresh_s:
        try:
            data = f.read_bytes()
            f.touch()
            STATS["hit"] += 1
            LAST.append((src, "hit")); del LAST[:-12]
            return data, ctype, "hit"
        except OSError:
            pass
    url = SOURCES[src] + urllib.parse.quote(path, safe="/%")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = r.read(3_000_000)
            ct = r.headers.get("Content-Type", "").split(";")[0] or ctype
            status = r.status
        LAST.append((src, status)); del LAST[:-12]
        if status == 200 and data:
            DIR.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
            STATS["miss"] += 1
            if _size["n"] is None or time.time() % 20 < 1:
                threading.Thread(target=_evict, daemon=True).start()
            return data, ct, "miss"
    except urllib.error.HTTPError as e:
        LAST.append((src, e.code)); del LAST[:-12]
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        LAST.append((src, "down")); del LAST[:-12]
    if have:                                                # the network is down or refused: an old copy is better than a hole
        STATS["stale"] += 1
        return f.read_bytes(), ctype, "stale"
    STATS["fail"] += 1
    raise OSError("no tile")


def style(origin, kind="dark"):
    """OpenFreeMap's dark style with its sprite, fonts and tile URLs pointed at this server (so they are cached), recoloured to the Jarvis navy. -> dict."""
    data, _, _ = get("ofm", "styles/" + kind, fresh_s=86400)
    st = json.loads(data)
    tj, _, _ = get("ofm", "planet", fresh_s=3600)
    tiles = json.loads(tj)["tiles"][0].replace("https://tiles.openfreemap.org/", origin + "/api/tc/ofm/")
    for name, src in st["sources"].items():
        if name == "openmaptiles":
            src.pop("url", None)
            src["tiles"] = [tiles]
            src["maxzoom"] = 14
        elif "url" in src:                                  # the natural-earth shaded relief: only its own tiles
            try:
                j, _, _ = get("ofm", src["url"].replace("https://tiles.openfreemap.org/", ""), fresh_s=86400)
                t = json.loads(j)["tiles"][0].replace("https://tiles.openfreemap.org/", origin + "/api/tc/ofm/")
                src.pop("url", None)
                src["tiles"] = [t]
            except Exception:
                pass
    st["sprite"] = origin + "/api/tc/ofm/sprites/ofm_f384/ofm"
    st["glyphs"] = origin + "/api/tc/ofm/fonts/{fontstack}/{range}.pbf"
    return st
