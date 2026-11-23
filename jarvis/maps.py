"""Maps that work like Google Maps, with free services and no keys: Nominatim (places), Overpass (what is nearby), OSRM / routing.openstreetmap.de (routes for
driving, walking and cycling), ipwho.is (a last-resort location). The map itself is jarvis/web/map.html (Leaflet + CARTO dark tiles) shown inside the Jarvis frame.

Where "you" are, in order: the home area you set ("set my home to ..." / "I'm in Miami Beach"), then your IP address's location (a city-level guess), then he asks you.
A marker is never put at 0,0: a location without real coordinates is simply not used."""
import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid

from . import settings

UA = {"User-Agent": "jarvis-local-assistant/0.1"}
LAST = {"q": None}
LAST_SPEC = {"id": None, "t": 0.0}          # the last route/place map shown: "walk" or "bike" alone switches it                          # the last place asked about: "show it on the map" means this
SPECS = {}                                   # id -> what the map page draws (kept in memory)
_IP = {"t": 0.0, "v": None}
MODES = {"car": ("driving", "http://router.project-osrm.org/route/v1/driving/"), "foot": ("walking", "https://routing.openstreetmap.de/routed-foot/route/v1/foot/"),
         "bike": ("cycling", "https://routing.openstreetmap.de/routed-bike/route/v1/bike/")}
TRAVELMODE = {"car": "driving", "foot": "walking", "bike": "bicycling"}
KINDS = {  # spoken word -> Overpass filter
    "coffee": ["amenity=cafe"], "cafe": ["amenity=cafe"], "coffee shop": ["amenity=cafe"], "restaurant": ["amenity=restaurant"], "food": ["amenity=restaurant", "amenity=fast_food"],
    "pharmacy": ["amenity=pharmacy"], "chemist": ["amenity=pharmacy"], "supermarket": ["shop=supermarket"], "groceries": ["shop=supermarket"], "grocery store": ["shop=supermarket"],
    "petrol station": ["amenity=fuel"], "gas station": ["amenity=fuel"], "fuel": ["amenity=fuel"], "atm": ["amenity=atm"], "bar": ["amenity=bar", "amenity=pub"], "pub": ["amenity=pub"],
    "hospital": ["amenity=hospital"], "toilet": ["amenity=toilets"], "bathroom": ["amenity=toilets"], "park": ["leisure=park"], "bakery": ["shop=bakery"], "hotel": ["tourism=hotel"],
    "parking": ["amenity=parking"], "bank": ["amenity=bank"], "gym": ["leisure=fitness_centre"], "bookshop": ["shop=books"], "library": ["amenity=library"], "cinema": ["amenity=cinema"],
}


def _get(url, timeout=6):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.load(r)


def haversine(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(h))


def valid(lat, lon):
    return isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and -90 <= lat <= 90 and -180 <= lon <= 180 and not (abs(lat) < 0.5 and abs(lon) < 0.5)


def imperial():
    u = settings.get("units")
    if u:
        return u == "imperial"
    if settings.get("home_area"):
        return "united states" in settings.get("home_area").lower()
    return bool(_IP["v"]) and _IP["v"].get("country") == "US"


def fmt_dist(m):
    if imperial():
        mi = m / 1609.344
        return f"{mi:.1f} miles" if mi >= 0.2 else f"{round(m * 3.281 / 10) * 10} feet"
    return f"{m / 1000:.1f} kilometres" if m >= 1000 else f"{round(m / 10) * 10} metres"


def fmt_dur(s):
    mins = max(1, round(s / 60))
    if mins >= 60:
        h, m = divmod(mins, 60)
        return f"{h} hour{'s' if h != 1 else ''}" + (f" {m} minutes" if m else "")
    return f"{mins} minute{'s' if mins != 1 else ''}"


# ----------------------------------------------------------------------------- where you are, and places by name
_GEO = {}                                           # recent place lookups (5 minutes): asking again is instant


def geocode(q):
    """Nominatim -> {"lat","lon","label","name"} or None. Coordinates are checked (never 0,0)."""
    hit = _GEO.get(q.lower().strip())
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    g = _geocode(q)
    if g:
        _GEO[q.lower().strip()] = (time.time(), g)
    return g


def _geocode(q):
    try:
        hit = _get("https://nominatim.openstreetmap.org/search?accept-language=en&format=json&limit=1&addressdetails=0&q=" + urllib.parse.quote(q), 6)
        if hit:
            lat, lon = float(hit[0]["lat"]), float(hit[0]["lon"])
            if valid(lat, lon):
                label = hit[0].get("display_name", q)
                return {"lat": lat, "lon": lon, "label": label, "name": label.split(",")[0]}
    except Exception:
        pass
    return None


def _today():
    return time.strftime("%Y-%m-%d")


def ip_sources():
    """What each IP lookup says (they often disagree by a city): [(name, {city, lat, lon})]. For the "where do you think I am" card."""
    out = []
    for name, url, f in (("ipwho.is", "https://ipwho.is/", lambda j: (j.get("city"), j.get("latitude"), j.get("longitude"))),
                         ("ip-api.com", "http://ip-api.com/json/", lambda j: (j.get("city"), j.get("lat"), j.get("lon")))):
        try:
            c, la, lo = f(_get(url, 4))
            if valid(la, lo):
                out.append((name, {"city": c or "?", "lat": la, "lon": lo}))
        except Exception:
            out.append((name, None))
    return out


def ip_confirmed():
    d = settings.get("loc_ip_ok") or {}
    return d.get("date") == _today()


def confirm_ip():
    settings.update(loc_ip_ok={"date": _today()})


def my_location():
    """-> {"lat","lon","label","source"} or None. Order: a place you said today ("temp"), the confirmed home area ("home"), Wi-Fi location if switched on ("wifi"),
    and last an IP guess ("ip": often wrong by a city, so "near me" asks you to confirm it first)."""
    t = settings.get("loc_temp") or {}
    if t.get("date") == _today() and valid(t.get("lat"), t.get("lon")):
        return {"lat": t["lat"], "lon": t["lon"], "label": t.get("label", "here"), "source": "today"}
    from . import location
    fx = location.latest()                                  # a real GPS fix from the iPhone beats every guess
    if fx and valid(fx["lat"], fx["lon"]):
        return {"lat": fx["lat"], "lon": fx["lon"], "label": "your iPhone", "source": "iphone", "acc": fx.get("acc"), "age": location.age_words(fx["t"])}
    lat, lon = settings.get("home_lat"), settings.get("home_lon")
    if valid(lat, lon):
        return {"lat": lat, "lon": lon, "label": (settings.get("home_area") or "home").split(",")[0], "source": "home"}
    if time.time() - _IP["t"] < 600 and _IP["v"]:
        return dict(_IP["v"], source="ip (confirmed)" if ip_confirmed() else "ip")
    try:
        j = _get("https://ipwho.is/", 4)
        if j.get("success") and valid(j.get("latitude"), j.get("longitude")):
            v = {"lat": j["latitude"], "lon": j["longitude"], "label": f"{j.get('city', '')}".strip() or "your area", "source": "ip", "country": j.get("country_code")}
            _IP.update(t=time.time(), v=v)
            return dict(v, source="ip (confirmed)" if ip_confirmed() else "ip")
    except Exception:
        pass
    return None


def needs_confirm(me):
    return bool(me) and me["source"] == "ip"


def set_location(place, today_only=False):
    """today_only: "I'm at X": used for today. Otherwise it becomes the saved home area."""
    g = geocode(place)
    if not g:
        return None
    if today_only:
        settings.update(loc_temp={"date": _today(), "lat": g["lat"], "lon": g["lon"], "label": g["name"]})
    else:
        settings.update(home_area=g["label"], home_lat=g["lat"], home_lon=g["lon"])
    return g


PENDING = {"g": None}
_SUG = {}


def suggest(q, n=6):
    """Address suggestions while you type, from a free keyless geocoder (Photon first, Nominatim as the fallback). -> [{"label","lat","lon"}]. Cached 5 min per text."""
    q = re.sub(r"\s+", " ", q or "").strip()[:120]
    if len(q) < 4:
        return []
    hit = _SUG.get(q.lower())
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    out = []
    try:
        me = my_location()                                    # bias towards where you are (the same street name exists in many countries)
        bias = "&lat=%.4f&lon=%.4f" % (me["lat"], me["lon"]) if me else ""
        j = _get("https://photon.komoot.io/api/?limit=%d&lang=en%s&q=%s" % (n, bias, urllib.parse.quote(q)), 5)
        for f in j.get("features", []):
            la, lo = f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0]
            pr = f.get("properties", {})
            street = " ".join(x for x in (pr.get("housenumber"), pr.get("street") or pr.get("name")) if x)
            label = ", ".join(x for x in (street, pr.get("city") or pr.get("district"), pr.get("state"), pr.get("postcode"), pr.get("country")) if x)
            if valid(la, lo) and label:
                out.append({"label": label[:160], "lat": la, "lon": lo})
    except Exception as e:
        print("[suggest] photon failed:", type(e).__name__, flush=True)
    if not out:
        try:
            for e in _get("https://nominatim.openstreetmap.org/search?accept-language=en&format=json&limit=%d&q=%s" % (n, urllib.parse.quote(q)), 6):
                la, lo = float(e["lat"]), float(e["lon"])
                if valid(la, lo):
                    out.append({"label": e.get("display_name", q)[:160], "lat": la, "lon": lo})
        except Exception as e:
            print("[suggest] nominatim failed:", type(e).__name__, flush=True)
    _SUG[q.lower()] = (time.time(), out)
    return out


def location_card(today=False):
    """The typed-address setup card: a box with live suggestions; tapping one shows it on the map to confirm before it is saved."""
    return {"type": "guide", "gid": "loc", "title": "Set my location" if not today else "Where are you today?", "full": True, "paged": False, "secret": True, "steps": [
        {"n": 1, "text": "Type your street address or neighbourhood. Pick one of the suggestions.", "input": {"placeholder": "start typing, e.g. house number, street, town", "label": "FIND", "action": "loc_find", "suggest": True, "pick": "loc_today" if today else "loc_pick"},
         "hint": "Typing is more reliable than speech for street names. Nothing is guessed. It is shown on a map for you to confirm, then kept only in your private settings file (not in the Jarvis code folder)."}],
        "status": ""}



def apply_location(place, today_only):
    """Speech-level result for "I'm at X" (today) and "set my location to X" (shows it on the map to confirm before saving as home)."""
    g = geocode(place)
    if not g:
        return {"speech": f"I couldn't find {place}. Try the street and town."}
    if today_only:
        settings.update(loc_temp={"date": _today(), "lat": g["lat"], "lon": g["lon"], "label": g["name"]})
        return {"speech": f"Got it, you're at {g['name']} for today."}
    PENDING["g"] = g
    spec = confirm_spec(g)
    return {"speech": f"Is this where you are, {g['name']}? Say yes to save it as home, or tell me the address again.", "expect": "confirmloc", "open": {"action": "show_map", "id": spec["id"]}}


def confirm_pending():
    g = PENDING.get("g")
    if not g:
        return None
    settings.update(home_area=g["label"], home_lat=g["lat"], home_lon=g["lon"])
    PENDING["g"] = None
    return g


def sources_card():
    """Print what each source says and which one the map uses (the home setting, IP lookups; Wi-Fi/geoclue is not available here)."""
    me = my_location()
    lines = [f"Home setting: {settings.get('home_area') or 'not set'}"]
    for name, v in ip_sources():
        lines.append(f"IP lookup {name}: " + (f"{v['city']} ({v['lat']:.2f}, {v['lon']:.2f})" if v else "no answer"))
    lines.append("Wi-Fi location: not available (needs geoclue plus a location service key)")
    lines.append(location_line(me) + " <- used for the map")
    speech = (f"I think you're in {me['label']}, from your {SOURCE_WORDS.get(me['source'], me['source'])}." + (" That is a rough guess, so tell me your address if it is wrong." if me["source"] == "ip" else "")) if me else "I don't know where you are yet. Say: set my location."
    return {"speech": speech, "cards": [{"type": "list", "title": "Where I think you are", "items": lines}]}


SOURCE_WORDS = {"iphone": "iPhone GPS", "home": "home", "today": "set today", "wifi": "Wi-Fi", "ip": "IP guess, rough", "ip (confirmed)": "IP, confirmed"}


def location_line(me):
    return f"Location: {me['label']} (source: {SOURCE_WORDS.get(me['source'], me['source'])})" if me else "Location: unknown"


_RCACHE = {}                                          # (mode, rounded a, rounded b) -> (time, route): the same trip asked again is instant and costs the routing server nothing
_RL = {"t": {}, "lock": threading.Lock()}
SPEED_KMH = {"car": 45.0, "foot": 5.0, "bike": 15.0}   # only for the labelled straight-line estimate when the routing service is down (no traffic, no roads)
FLIGHT_KMH, FLIGHT_MIN_M = 800.0, 500_000              # a very rough cruise speed, no airport time, no prices: shown only for 500 km and up, labelled approximate
WALK_FIRST_S = 480                                     # on foot first only when the walk is under about 8 minutes


def _polite(host):
    """The public routing servers ask for light use (about one request a second each): wait if the last request to this host was less than 1.1 s ago."""
    with _RL["lock"]:
        wait = _RL["t"].get(host, 0) + 1.1 - time.time()
        if wait > 0:
            time.sleep(min(wait, 1.2))
        _RL["t"][host] = time.time()


def route(mode, a, b):
    """a, b = (lat, lon). -> {"dist_m","dur_s","geometry": [[lat, lon], ...], "approx": False} or None. Cached 10 minutes."""
    key = (mode, round(a[0], 3), round(a[1], 3), round(b[0], 3), round(b[1], 3))
    hit = _RCACHE.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    try:
        base = MODES[mode][1]
        _polite(urllib.parse.urlparse(base).netloc)
        j = _get(f"{base}{a[1]},{a[0]};{b[1]},{b[0]}?overview=simplified&geometries=geojson", 7)
        r = j["routes"][0]
        pts = [[c[1], c[0]] for c in r["geometry"]["coordinates"]]
        out = {"dist_m": r["distance"], "dur_s": r["duration"], "geometry": pts, "approx": False}
        _RCACHE[key] = (time.time(), out)
        for k in [k for k, v in _RCACHE.items() if time.time() - v[0] > 3600]:
            _RCACHE.pop(k, None)
        return out
    except Exception:
        return None


def approx_route(mode, a, b):
    """The routing service is down or rate-limited: a straight line, distance x 1.3 for the bends, typical speed. Always labelled approximate."""
    d = haversine(a, b) * 1.3
    return {"dist_m": d, "dur_s": d / 1000.0 / SPEED_KMH[mode] * 3600.0, "geometry": [[a[0], a[1]], [b[0], b[1]]], "approx": True}


def routes_all(a, b):
    """-> {"car": route, "foot": route, "bike": route}: a real route where the service answers, else the labelled straight-line estimate."""
    out, ths = {}, []
    for m in MODES:
        t = threading.Thread(target=lambda m=m: out.__setitem__(m, route(m, a, b)), daemon=True)
        t.start(); ths.append(t)
    for t in ths:
        t.join(12)
    far = haversine(a, b) >= 3_000_000                       # across an ocean a straight-line drive is nonsense: no road estimate at all
    return {m: out.get(m) or (None if far else approx_route(m, a, b)) for m in MODES}


def flight_estimate(a, b):
    """-> {"dist_m","dur_s","approx": True} for 500 km and up, else None. Straight-line distance / a typical speed. No prices, no booking, no airport time."""
    d = haversine(a, b)
    return {"dist_m": d, "dur_s": d / 1000.0 / FLIGHT_KMH * 3600.0, "approx": True} if d >= FLIGHT_MIN_M else None


def default_mode(rts):
    """Car, unless the walk is very short."""
    f = rts.get("foot")
    if f and not f.get("approx") and f["dur_s"] < WALK_FIRST_S:
        return "foot"
    for m in ("car", "foot", "bike"):
        if rts.get(m):
            return m
    return None


NOM_TERM = {"coffee": "cafe", "coffee shop": "cafe", "food": "restaurant", "groceries": "supermarket", "grocery store": "supermarket", "petrol station": "fuel", "gas station": "fuel",
            "bathroom": "toilets", "toilet": "toilets", "chemist": "pharmacy"}
_OVERPASS = {"down_until": 0.0}


def _nominatim_places(term, lat, lon, radius):
    term = NOM_TERM.get(term.lower().strip(), term)
    d = radius / 111000
    box = f"{lon - d * 1.4},{lat + d},{lon + d * 1.4},{lat - d}"
    out = []
    for e in _get("https://nominatim.openstreetmap.org/search?accept-language=en&format=json&limit=15&bounded=1&viewbox=" + box + "&q=" + urllib.parse.quote(term), 8):
        la, lo = float(e["lat"]), float(e["lon"])
        if valid(la, lo):
            out.append({"name": e.get("display_name", term).split(",")[0][:60], "lat": la, "lon": lo, "dist_m": haversine((lat, lon), (la, lo))})
    return out


_PLACES = {}


def places(kind, lat, lon, radius=1800, limit=12):
    key = (kind.lower().strip(), round(lat, 3), round(lon, 3), radius)
    hit = _PLACES.get(key)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    r = _places(kind, lat, lon, radius, limit)
    if r:
        _PLACES[key] = (time.time(), r)
    return r


def _places(kind, lat, lon, radius=1800, limit=12):
    """What is near (lat, lon): Overpass for known kinds (fast mirror first, short timeouts), then Nominatim text search as the fallback.
    -> [{"id","name","lat","lon","dist_m"}] nearest first."""
    out = []
    filt = KINDS.get(kind.lower().strip())
    if filt and time.time() > _OVERPASS["down_until"]:                         # skip Overpass for 5 minutes after it fails (it is often overloaded)
        q = "[out:json][timeout:8];(" + "".join(f"nwr[{f}](around:{radius},{lat},{lon});" for f in filt) + ");out center 40;"
        for url in ("https://overpass-api.de/api/interpreter",):
            try:
                j = json.load(urllib.request.urlopen(urllib.request.Request(url, data=("data=" + urllib.parse.quote(q)).encode(), headers=UA), timeout=5))
                for e in j.get("elements", []):
                    c = e.get("center") or e
                    la, lo = c.get("lat"), c.get("lon")
                    nm = (e.get("tags") or {}).get("name")
                    if valid(la, lo) and nm:
                        out.append({"name": nm[:60], "lat": la, "lon": lo, "dist_m": haversine((lat, lon), (la, lo))})
                break
            except Exception:
                _OVERPASS["down_until"] = time.time() + 300
                continue
    if not out:
        try:
            out = _nominatim_places(kind, lat, lon, radius)
            if not out:
                out = _nominatim_places(kind, lat, lon, radius * 3)
        except Exception:
            return []
    seen, uniq = set(), []
    for p in sorted(out, key=lambda p: p["dist_m"]):
        k = (p["name"], round(p["lat"], 3))
        if k not in seen:
            seen.add(k); uniq.append(p)
    for i, p in enumerate(uniq[:limit]):
        p["id"] = i
    return uniq[:limit]


# ----------------------------------------------------------------------------- the spec the page draws
def gmaps_link(origin, dest, mode="car"):
    """A Google Maps directions link that opens on the Mac / phone (the origin is part of it when known)."""
    q = "https://www.google.com/maps/dir/?api=1"
    if origin:
        q += f"&origin={origin[0]:.5f},{origin[1]:.5f}"
    return q + f"&destination={dest[0]:.5f},{dest[1]:.5f}&travelmode={TRAVELMODE.get(mode, 'driving')}"


def apple_link(origin, dest, mode="car"):
    """An Apple Maps directions link (opens in Maps on the iPhone)."""
    q = f"https://maps.apple.com/?daddr={dest[0]:.5f},{dest[1]:.5f}&dirflg={ {'car': 'd', 'foot': 'w', 'bike': 'w'}.get(mode, 'd') }"
    return q + (f"&saddr={origin[0]:.5f},{origin[1]:.5f}" if origin else "")


def new_spec(**kw):
    sid = uuid.uuid4().hex[:10]
    LAST_SPEC.update(id=sid, t=time.time())
    spec = {"id": sid, "mode": "car", "origin": None, "dest": None, "routes": {}, "places": [], "title": "", "imperial": imperial(), "created": time.time()}
    spec.update(kw)
    for old in [k for k, v in SPECS.items() if time.time() - v["created"] > 3600]:
        SPECS.pop(old, None)
    SPECS[sid] = spec
    return spec


def _street(addr):
    return (addr or {}).get("road") or (addr or {}).get("pedestrian") or (addr or {}).get("neighbourhood") or (addr or {}).get("suburb") or ""


def candidates(name, me, n=6):
    """Places called `name`, the NEAREST to home first: a search in a box about 40 km around you (chains, airports, shops), else the one best match anywhere (cities, countries, landmarks).
    -> [{"lat","lon","name","street","label","dist_m"}]; the first is the one Jarvis picks, the rest are the alternatives behind the arrows."""
    out = []
    if me:
        d = 0.4
        try:
            q = urllib.parse.quote(name)
            hits = _get(f"https://nominatim.openstreetmap.org/search?accept-language=en&format=json&limit=10&addressdetails=1&bounded=1&viewbox={me['lon'] - d},{me['lat'] + d},{me['lon'] + d},{me['lat'] - d}&q={q}", 6)
            for h in hits or []:
                lat, lon = float(h["lat"]), float(h["lon"])
                if valid(lat, lon):
                    nm = h.get("name") or h.get("display_name", name).split(",")[0]
                    out.append({"lat": lat, "lon": lon, "name": nm, "street": _street(h.get("address")), "label": h.get("display_name", nm), "dist_m": haversine((me["lat"], me["lon"]), (lat, lon)),
                                "bbox": h.get("boundingbox"), "class": h.get("class"), "type": h.get("type")})
        except Exception:
            out = []
        out.sort(key=lambda c: c["dist_m"])
        seen, uniq = set(), []
        for c in out:
            k = (round(c["lat"], 3), round(c["lon"], 3))
            if k not in seen:
                seen.add(k); uniq.append(c)
        out = uniq[:n]
    if not out:
        g = geocode_full(name)
        if g:
            out = [dict(g, dist_m=haversine((me["lat"], me["lon"]), (g["lat"], g["lon"])) if me else 0)]
    return out


def geocode_full(q):
    """One best match anywhere, with the bounding box (so a city or country can be framed). Cached 5 minutes."""
    hit = _GEO.get("full:" + q.lower().strip())
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    try:
        h = (_get("https://nominatim.openstreetmap.org/search?accept-language=en&format=json&limit=1&addressdetails=1&q=" + urllib.parse.quote(q), 6) or [None])[0]
        if h:
            lat, lon = float(h["lat"]), float(h["lon"])
            if valid(lat, lon):
                nm = h.get("name") or h.get("display_name", q).split(",")[0]
                g = {"lat": lat, "lon": lon, "name": nm, "street": _street(h.get("address")), "label": h.get("display_name", nm), "bbox": h.get("boundingbox"), "class": h.get("class"), "type": h.get("type")}
                _GEO["full:" + q.lower().strip()] = (time.time(), g)
                return g
    except Exception:
        pass
    return None


def _spoken_name(c):
    return c["name"] + (f" on {c['street']}" if c.get("street") and c["street"].lower() not in c["name"].lower() else "")


def preload(lat, lon):
    """Warm the tile cache for a place before the map flies there (the vector tiles of every zoom from the whole-earth view down to street level, 3 x 3 around the target at the close zooms): the flight does not wait for the network."""
    def run():
        try:
            from . import tilecache
            tj, _, _ = tilecache.get("ofm", "planet", fresh_s=3600)
            tpl = json.loads(tj)["tiles"][0].replace("https://tiles.openfreemap.org/", "")
            for z in range(2, 15):
                n = 2 ** z
                x = int((lon + 180.0) / 360.0 * n)
                y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
                r = 1 if z >= 10 else 0
                for dx in range(-r, r + 1):
                    for dy in range(-r, r + 1):
                        tilecache.get("ofm", tpl.replace("{z}", str(z)).replace("{x}", str((x + dx) % n)).replace("{y}", str(min(max(0, y + dy), n - 1))))
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


def directions_spec(dest_name, mode=None):
    """-> (spec, speech) or (None, error speech). Picks the nearest match to home and says which; the others stay behind the arrows. `mode` None = car, or on foot when that is under about 8 minutes."""
    me = my_location()
    m = re.fullmatch(r"(?:the )?(?:nearest|closest) (.+)", dest_name.strip(), re.I)
    cands = []
    if m and me:
        ps = places(m.group(1), me["lat"], me["lon"])
        cands = [{"lat": p["lat"], "lon": p["lon"], "name": p["name"], "street": "", "label": p["name"], "dist_m": p.get("dist_m", 0)} for p in ps[:6]]
    if not cands:
        cands = candidates(re.sub(r"^(?:the )", "", dest_name.strip(), flags=re.I) if len(dest_name) > 4 else dest_name, me)
    if not cands:
        return None, f"I couldn't find {dest_name}."
    if not me:
        return None, "I don't know where you are yet. Tell me, for example: I'm in Miami Beach."
    g = cands[0]
    spec = new_spec(origin={"lat": me["lat"], "lon": me["lon"], "label": me["label"], "source": me["source"]}, dest={"lat": g["lat"], "lon": g["lon"], "label": g["name"], "street": g.get("street", "")},
                    title="Route to " + g["name"], alts=cands, alt_i=0, fly=True)
    return _fill_route(spec, mode)


def _fill_route(spec, mode=None):
    """Routes for car/walk/bike (+ a flight estimate when far), the default mode, the gmaps link, and the spoken line. Used again when another alternative is picked."""
    me, g = spec["origin"], spec["dest"]
    a, b = (me["lat"], me["lon"]), (g["lat"], g["lon"])
    rts = routes_all(a, b)
    fl = flight_estimate(a, b)
    chosen = mode if mode in MODES and rts.get(mode) else default_mode(rts)
    spec.update(routes=rts, flight=fl, mode=chosen or "car", title="Route to " + g["label"], traffic=False)
    spec["gmaps"] = gmaps_link(a, b, spec["mode"])
    preload(g["lat"], g["lon"])
    name = _spoken_name({"name": g["label"], "street": g.get("street", "")})
    parts = []
    if chosen:
        r = rts[chosen]
        way = {"car": "by car", "foot": "on foot", "bike": "by bike"}[chosen]
        parts.append(f"{name}: {fmt_dur(r['dur_s'])} {way}, {fmt_dist(r['dist_m'])}." + (" That is a rough straight-line estimate: the route service did not answer." if r.get("approx") else " The time has no live traffic."))
        others = [f"{fmt_dur(rts[k]['dur_s'])} {w}" for k, w in (("car", "by car"), ("foot", "on foot"), ("bike", "by bike")) if k != chosen and rts.get(k) and not rts[k].get("approx")]
        if others and not (rts[chosen].get("approx")):
            parts.append("Also " + ", ".join(others[:2]) + ".")
    elif not fl:
        return None, "I couldn't get a route there just now."
    if fl:
        parts.append(f"By air about {fmt_dur(fl['dur_s'])} in the air, approximate, no airport time.")
    if me["source"] == "ip":
        parts.append("(I'm guessing where you are from your internet address; say I'm in, and your town, to fix it.)")
    alts = spec.get("alts") or []
    if len(alts) > 1:
        parts.append(f"I picked the nearest of {len(alts)}; use the arrows for the others.")
    return spec, " ".join(parts)


def pick_alt(sid, i):
    """The arrows on the map: switch to another match for the same name."""
    spec = SPECS.get(sid)
    if not spec or not spec.get("alts") or not spec.get("origin") or not 0 <= i < len(spec["alts"]):
        return None
    g = spec["alts"][i]
    spec.update(dest={"lat": g["lat"], "lon": g["lon"], "label": g["name"], "street": g.get("street", "")}, alt_i=i, fly=False)
    _fill_route(spec, None)
    return spec


def place_view_spec(name):
    """"show me Tokyo", "go to Paris", "show the Eiffel Tower": a map that flies there (an arc, scaled by distance), the whole city or country framed. -> (spec, speech) or (None, None) when it is not a place."""
    me = my_location()
    g = geocode_full(name)
    if not g or (g.get("class") in ("highway", "building") and not me):
        return None, None
    cls, typ = g.get("class"), g.get("type")
    placey = cls in ("place", "boundary", "tourism", "historic", "natural", "aeroway", "railway", "leisure", "amenity", "man_made", "waterway", "landuse") or typ in ("city", "country", "administrative")
    if not placey:
        return None, None
    spec = new_spec(origin=({"lat": me["lat"], "lon": me["lon"], "label": me["label"], "source": me["source"]} if me else None),
                    dest={"lat": g["lat"], "lon": g["lon"], "label": g["name"], "bbox": g.get("bbox"), "kind": typ or cls}, title=g["name"], fly=True, live=False)
    spec["gmaps"] = f"https://www.google.com/maps/search/?api=1&query={g['lat']:.5f},{g['lon']:.5f}"
    if me:
        spec["dist_m"] = haversine((me["lat"], me["lon"]), (g["lat"], g["lon"]))
    preload(g["lat"], g["lon"])
    far = f" It is {fmt_dist(spec['dist_m'])} from you." if me and spec["dist_m"] >= FLIGHT_MIN_M else ""
    return spec, f"Here is {g['name']}.{far}"


def places_spec(kind):
    me = my_location()
    if not me:
        return None, "I don't know where you are yet. Tell me, for example: I'm in Miami Beach."
    ps = places(kind, me["lat"], me["lon"])
    if not ps:
        return None, f"I couldn't find any {kind} near {me['label']}."
    spec = new_spec(origin={"lat": me["lat"], "lon": me["lon"], "label": me["label"], "source": me["source"]}, places=ps, title=f"{kind.title()} near {me['label']}")
    near = ps[0]
    return spec, f"I found {len(ps)}. The nearest is {near['name']}, {fmt_dist(near['dist_m'])} away."


def me_spec():
    """The MAP button: no place asked for, so show me, at street level, with how sure we are. -> spec dict (not stored)."""
    me = my_location()
    spec = {"id": "me", "mode": "car", "origin": None, "dest": None, "routes": {}, "places": [], "title": "Map", "imperial": imperial(), "live": True}
    if me:
        acc = me.get("acc") or {"iphone": 40, "today": 300, "home": 500, "wifi": 200}.get(me["source"], 8000)
        spec["origin"] = {"lat": me["lat"], "lon": me["lon"], "label": me["label"], "source": me["source"], "acc": acc, "age": me.get("age", "")}
        spec["approx"] = me["source"] in ("ip", "ip (confirmed)")
    return spec


def confirm_spec(g):
    """A map card for "is this where you are?": the page shows YES / NO buttons that save it as the home area."""
    spec = new_spec(dest={"lat": g["lat"], "lon": g["lon"], "label": g["name"]}, title="Is this where you are?")
    spec["confirm"] = {"label": g["label"], "lat": g["lat"], "lon": g["lon"]}
    return spec


def place_map_spec(name):
    g = geocode(name)
    if not g:
        return None, f"I couldn't find {name}."
    me = my_location()
    spec = new_spec(origin=({"lat": me["lat"], "lon": me["lon"], "label": me["label"], "source": me["source"]} if me else None),
                    dest={"lat": g["lat"], "lon": g["lon"], "label": g["name"]}, title=g["name"])
    spec["gmaps"] = f"https://www.google.com/maps/search/?api=1&query={g['lat']:.5f},{g['lon']:.5f}"
    return spec, f"Here is {g['name']}."


def goto_place(sid, pid):
    """The 'GO THERE' button on a pin: turn a places map into a route to that place."""
    spec = SPECS.get(sid)
    if not spec or not spec.get("origin"):
        return None
    p = next((q for q in spec["places"] if q["id"] == pid), None)
    if not p:
        return None
    o = spec["origin"]
    rts = routes_all((o["lat"], o["lon"]), (p["lat"], p["lon"]))
    mode = "car" if rts.get("car") else next((m for m in MODES if rts.get(m)), "car")
    spec.update(dest={"lat": p["lat"], "lon": p["lon"], "label": p["name"]}, routes=rts, mode=mode, places=[], title="Route to " + p["name"])
    spec["gmaps"] = gmaps_link((o["lat"], o["lon"]), (p["lat"], p["lon"]), mode)
    return spec


# ----------------------------------------------------------------------------- what the user says
_DEST = r"(?P<d>.+?)"
MODE_WORDS = {"walk": "foot", "walking": "foot", "on foot": "foot", "cycle": "bike", "cycling": "bike", "bike": "bike", "biking": "bike", "drive": "car", "driving": "car", "by car": "car"}


def parse_command(text):
    t = text.strip()
    low = t.lower().strip(" .?!")
    if re.fullmatch(r"(?:please )?(?:set(?: ?up)?|setup|save|change) my (?:home )?location(?: (?:please|now))?|(?:please )?set (?:up )?(?:my )?home (?:area|location)", low):
        return {"kind": "ask_location", "today": False}
    if re.fullmatch(r"(?:please )?(?:update|correct|fix|refresh) my location|that'?s not where i am|my location is wrong|(?:you(?:'re| are)) wrong about where i am", low):
        return {"kind": "ask_location", "today": True}
    m = re.match(r"(?:set|save|change) my (?:home )?location (?:to|as) (.+)$", low)
    if m:
        return {"kind": "set_location", "place": t[m.start(1):m.end(1)].strip(" .?!"), "today": False}
    m = re.match(r"(?:i'?m|i am|i'?m currently|we'?re|update my location to|my (?:current )?location is) (?:in |at |near )?(.+)$", low)
    if m and not re.search(r"\b(home|daddy)\b", low) and (not re.match(r"(?:a|an|the|my|your|our|trouble|love|a hurry|not|going|ready|done|sure|fine|good|back|tired|bored|hungry|sorry|glad|just|so|very|also|still|really)\b", m.group(1))
                                                                    or re.match(r"the (?:library|office|airport|station|park|mall|hospital|school|gym|cafe|hotel|university|beach|museum|supermarket|market|shop|store|bank|church|stadium)\b", m.group(1))):
        return {"kind": "set_location", "place": t[m.start(1):m.end(1)].strip(" .?!"), "today": True}
    if re.fullmatch(r"where (?:do you think|am) i(?: am)?|where do you think i am|what do you think my location is|show (?:me )?(?:my )?location sources", low):
        return {"kind": "sources"}
    if re.fullmatch(r"where am i|where are we|what'?s my location", low):
        return {"kind": "where"}
    m = re.search(r"\b(?:find|show|search for|look for|where(?:'s| is) (?:the )?nearest|any|get me) (?:me )?(?:a |an |the |some |the nearest |nearest )?(.+?)(?: (?:near me|nearby|around here|close by|near here|close to me|around me))$", low)
    if m and len(m.group(1)) < 40:
        return {"kind": "places", "q": m.group(1).strip()}
    m = re.search(r"\bwhere(?:'s| is) the nearest (.+)$", low)
    if m:
        return {"kind": "places", "q": m.group(1).strip()}
    m = (re.search(r"\bhow (?:long|much time) (?:will|would|does|do) (?:it|that|the trip|the drive|the walk) (?:take|be)(?: me)?(?: to| for me to)? (?:(walk|cycle|bike|drive|get)(?: there)? to |get to |walk to |drive to |cycle to |bike to |go to )(.+)$", low)
         or re.search(r"\b(?:how (?:long|far) is the (drive|walk|ride|trip|cycle) to |what(?:'s| is) the (?:travel |drive |walking )?time to (?:get to )?|time to (?:get to |walk to |drive to )|how long (?:is|would be) (?:it|the (?:drive|walk))(?: to| from here to) |how long to (?:(walk|cycle|bike|drive|get) to )?|how long (?:does it take )?to (?:(walk|cycle|bike|drive|get)(?: there)? to )?|how far is |how far to |how far away is |directions? to |route to |navigate to |take me to |get me to |how do i get to )(.+)$", low))
    if m:
        dest = t[m.start(m.lastindex):m.end(m.lastindex)].strip(" .?!") if m.group(m.lastindex) else None
        word = next((g for g in m.groups()[:-1] if g), "")
        mode = {"walk": "foot", "cycle": "bike", "bike": "bike", "ride": "bike", "drive": "car", "get": None, "trip": None}.get(word)
        for w, md in MODE_WORDS.items():
            if re.search(r"\b" + w + r"\b", low):
                mode = md
        dest = re.sub(r"\s+(?:from here|please|from my house|from home)$", "", dest or "", flags=re.I).strip()
        if dest and not re.fullmatch(r"(?:the )?(?:orb|corner|jarvis|rose|sleep|bed|settings?|the (?:next|previous) (?:page|card))", dest.lower()):
            return {"kind": "directions", "dest": re.sub(r"^(?:the )", "", dest), "mode": mode}
    if re.fullmatch(r"(?:please )?(?:give me |get |show me )?(?:directions|a route|navigation)(?: please)?|(?:please )?how (?:far|long)(?: is it| will it take| does it take)?|(?:please )?take me there|(?:please )?navigate", low):
        return {"kind": "ask_dest"}                                        # incomplete: ask one short question instead of guessing
    m = re.fullmatch(r"(?:please )?(?:(?:switch|change|go) to |show |what about |how about |try |use |by |on )?(?:the )?(walk|walking|on foot|bike|biking|cycling|cycle|car|drive|driving|by car|by bike)(?: (?:instead|mode|route|time))?(?: please)?", low)
    if m and LAST_SPEC["id"] and time.time() - LAST_SPEC["t"] < 1800:
        return {"kind": "mode", "mode": MODE_WORDS.get(m.group(1), "car")}
    m = re.fullmatch(r"(?:please )?(?:show me|show|take me to|go to|fly to|zoom to|zoom in on|pull up|let me see|where is|where's|find) (?:the )?(.{3,50})", low)
    if m and not re.match(r"(?:me|my|a|an|some|all|your|this|that|it|more|less|the (?:map|time|weather|news|orb|screen|desktop|camera|music|settings?))\b|(?:sleep|bed|work|school|class|town|a break|ahead|on|off|back|home|the next|next|previous|start|stop|map|weather|news|time|settings?|reminders?|alarms?|timers?|notes?|pictures?|photos?|videos?|images?|music|playlist|menu|home page|clock|calendar|schedule|nearest|closest|desktop|screen|camera)\b", m.group(1)):
        return {"kind": "place_view", "place": t[m.start(1):m.end(1)].strip(" .?!")}
    m = re.search(r"\b(?:show me|show|open|pull up) (?:a |the )?(?:map of|map for|maps? (?:for|of)|map)\s*(.*)$", low)
    if m:
        place = t[m.start(1):m.end(1)].strip(" .?!")
        return {"kind": "map", "place": place} if place else {"kind": "map_ask"}
    # "show / put / map / pin X on the map" (speech-to-text often hears "map" as "Mac": "on the Mac" is the map unless it says "my Mac")
    m = re.fullmatch(r"(?:please )?(?:show|put|pin|map|place|find|locate|where(?:'s| is)) (?:me )?(.+?) (?:up )?(?:on|in|onto) (?:the |a )?(?:map|maps|mac|mat|google maps)", low)
    if m:
        what = t[m.start(1):m.end(1)].strip()
        if re.fullmatch(r"it|that|this|that place|this place|there", what.lower()):
            return {"kind": "map", "place": LAST["q"]} if LAST["q"] else {"kind": "map_ask"}
        return {"kind": "places", "q": re.sub(r"^(?:the |a |an |some |nearest |the nearest )+", "", what, flags=re.I)}
    if re.fullmatch(r"(?:please )?(?:map|pin|locate) (?:it|that|this)(?: please)?", low):
        return {"kind": "map", "place": LAST["q"]} if LAST["q"] else {"kind": "map_ask"}
    if re.fullmatch(r"(?:please )?(?:show|open|pull up|bring up) (?:me )?(?:the |a )?map", low):
        return {"kind": "map_ask"}
    return None
