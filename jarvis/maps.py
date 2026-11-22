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
LAST = {"q": None}                          # the last place asked about: "show it on the map" means this
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
        hit = _get("https://nominatim.openstreetmap.org/search?format=json&limit=1&addressdetails=0&q=" + urllib.parse.quote(q), 6)
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
            for e in _get("https://nominatim.openstreetmap.org/search?format=json&limit=%d&q=%s" % (n, urllib.parse.quote(q)), 6):
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


def route(mode, a, b):
    """a, b = (lat, lon). -> {"dist_m","dur_s","geometry": [[lat, lon], ...]} or None."""
    try:
        base = MODES[mode][1]
        j = _get(f"{base}{a[1]},{a[0]};{b[1]},{b[0]}?overview=simplified&geometries=geojson", 7)
        r = j["routes"][0]
        pts = [[c[1], c[0]] for c in r["geometry"]["coordinates"]]
        return {"dist_m": r["distance"], "dur_s": r["duration"], "geometry": pts}
    except Exception:
        return None


def routes_all(a, b):
    out, ths = {}, []
    for m in MODES:
        t = threading.Thread(target=lambda m=m: out.__setitem__(m, route(m, a, b)), daemon=True)
        t.start(); ths.append(t)
    for t in ths:
        t.join(9)
    return {m: out.get(m) for m in MODES}


NOM_TERM = {"coffee": "cafe", "coffee shop": "cafe", "food": "restaurant", "groceries": "supermarket", "grocery store": "supermarket", "petrol station": "fuel", "gas station": "fuel",
            "bathroom": "toilets", "toilet": "toilets", "chemist": "pharmacy"}
_OVERPASS = {"down_until": 0.0}


def _nominatim_places(term, lat, lon, radius):
    term = NOM_TERM.get(term.lower().strip(), term)
    d = radius / 111000
    box = f"{lon - d * 1.4},{lat + d},{lon + d * 1.4},{lat - d}"
    out = []
    for e in _get("https://nominatim.openstreetmap.org/search?format=json&limit=15&bounded=1&viewbox=" + box + "&q=" + urllib.parse.quote(term), 8):
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


def new_spec(**kw):
    sid = uuid.uuid4().hex[:10]
    spec = {"id": sid, "mode": "car", "origin": None, "dest": None, "routes": {}, "places": [], "title": "", "imperial": imperial(), "created": time.time()}
    spec.update(kw)
    for old in [k for k, v in SPECS.items() if time.time() - v["created"] > 3600]:
        SPECS.pop(old, None)
    SPECS[sid] = spec
    return spec


def directions_spec(dest_name, mode="car"):
    """-> (spec, speech) or (None, error speech). "the nearest bank" routes to the nearest one."""
    me = my_location()
    m = re.fullmatch(r"(?:the )?(?:nearest|closest) (.+)", dest_name.strip(), re.I)
    g = None
    if m and me:
        ps = places(m.group(1), me["lat"], me["lon"])
        if ps:
            g = {"lat": ps[0]["lat"], "lon": ps[0]["lon"], "name": ps[0]["name"]}
    g = g or geocode(dest_name)
    if not g:
        return None, f"I couldn't find {dest_name}."
    if not me:
        return None, "I don't know where you are yet. Tell me, for example: I'm in Miami Beach."
    rts = routes_all((me["lat"], me["lon"]), (g["lat"], g["lon"]))
    if not rts.get(mode):
        mode = next((m for m in MODES if rts.get(m)), None)
        if not mode:
            return None, "I couldn't get a route there just now."
    spec = new_spec(mode=mode, origin={"lat": me["lat"], "lon": me["lon"], "label": me["label"], "source": me["source"]},
                    dest={"lat": g["lat"], "lon": g["lon"], "label": g["name"]}, routes=rts, title="Route to " + g["name"])
    spec["gmaps"] = gmaps_link((me["lat"], me["lon"]), (g["lat"], g["lon"]), mode)
    r = rts[mode]
    approx = " (I'm guessing where you are from your internet address; say I'm in, and your town, to fix it.)" if me["source"] == "ip" else ""
    return spec, f"{fmt_dur(r['dur_s'])} {'by car' if mode == 'car' else 'on foot' if mode == 'foot' else 'by bike'}, {fmt_dist(r['dist_m'])}.{approx}"


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
    m = re.search(r"\b(?:how long (?:does it take )?to (?:(walk|cycle|bike|drive|get)(?: there)? to )?|how far is |how far to |directions? to |route to |navigate to |take me to |get me to |how do i get to |how long to (?:(walk|cycle|bike|drive) to )?)(.+)$", low)
    if m:
        dest = t[m.start(3):m.end(3)].strip(" .?!") if m.group(3) else None
        mode = {"walk": "foot", "cycle": "bike", "bike": "bike", "drive": "car", "get": "car"}.get(m.group(1) or m.group(2) or "", "car")
        for w, md in MODE_WORDS.items():
            if re.search(r"\b" + w + r"\b", low):
                mode = md
        if dest and not re.fullmatch(r"(?:the )?(?:orb|corner|jarvis|rose)", dest.lower()):
            return {"kind": "directions", "dest": re.sub(r"^(?:the )", "", dest), "mode": mode}
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
