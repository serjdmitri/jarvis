"""Weather from Open-Meteo (free, no key): now, 24 hours, 5 days, sunrise and sunset, wind, rain chance. Cached 10 minutes per place; a stale copy is shown (with its time) when the network is down.
The place is where you really are (the iPhone's fix first: see maps.my_location), or a place you name. -> a "weather" card; only the one-line summary is spoken."""
import json
import re
import threading
import time
import urllib.request
from datetime import datetime

from . import settings

TTL = 600
_cache = {}                                      # (lat2, lon2, imperial) -> (t, data)
_lock = threading.Lock()
WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
       82: "heavy showers", 85: "snow showers", 86: "snow showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail"}


def kind(code):
    """-> the icon kind the card draws: sun, partly, cloud, fog, drizzle, rain, snow, storm."""
    return ("sun" if code in (0, 1) else "partly" if code == 2 else "cloud" if code == 3 else "fog" if code in (45, 48) else "drizzle" if code in (51, 53, 55, 56, 57) else
            "rain" if code in (61, 63, 65, 66, 67, 80, 81, 82) else "snow" if code in (71, 73, 75, 77, 85, 86) else "storm" if code >= 95 else "cloud")


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "jarvis-local-assistant/0.1"}), timeout=8) as r:
        return json.load(r)


def fetch(lat, lon, imperial=False, force=False):
    """-> (data dict, fetched_at) or raises OSError when there is nothing at all."""
    key = (round(lat, 2), round(lon, 2), bool(imperial))
    with _lock:
        hit = _cache.get(key)
    if hit and not force and time.time() - hit[0] < TTL:
        return hit[1], hit[0]
    q = (f"https://api.open-meteo.com/v1/forecast?latitude={lat:.4f}&longitude={lon:.4f}&timezone=auto&forecast_days=6&forecast_hours=26"
         "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m,precipitation,relative_humidity_2m,is_day"
         "&hourly=temperature_2m,weather_code,precipitation_probability"
         "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset"
         + ("&temperature_unit=fahrenheit&wind_speed_unit=mph" if imperial else ""))
    try:
        d = _get(q)
    except Exception:
        if hit:
            return hit[1], hit[0]                    # the network is down: the old copy, labelled with its own time
        raise OSError("no weather")
    with _lock:
        _cache[key] = (time.time(), d)
    return d, time.time()


def _hhmm(iso):
    return datetime.fromisoformat(iso).strftime("%-I:%M %p").replace(" AM", "a").replace(" PM", "p") if iso else ""


def card(name, lat, lon, label=None, imperial=False, force=False):
    d, t = fetch(lat, lon, imperial, force)
    c, dl, h = d["current"], d["daily"], d["hourly"]
    u, wu = ("°F", "mph") if imperial else ("°C", "km/h")
    now_h = datetime.fromisoformat(c["time"]).replace(minute=0)
    hours = []
    for i, ts in enumerate(h["time"]):
        if datetime.fromisoformat(ts) >= now_h and len(hours) < 12:
            hours.append({"t": datetime.fromisoformat(ts).strftime("%-I%p").lower(), "temp": round(h["temperature_2m"][i]), "kind": kind(h["weather_code"][i]), "pop": h["precipitation_probability"][i] or 0})
    days = [{"name": datetime.fromisoformat(dl["time"][i]).strftime("%a"), "hi": round(dl["temperature_2m_max"][i]), "lo": round(dl["temperature_2m_min"][i]), "kind": kind(dl["weather_code"][i]),
             "desc": WMO.get(dl["weather_code"][i], "unsettled"), "pop": dl["precipitation_probability_max"][i] or 0} for i in range(min(5, len(dl["time"])))]
    return {"type": "weather", "wide": 380, "small": True, "title": f"Weather · {name}", "place": label or name, "unit": u, "wind_unit": wu, "temp": round(c["temperature_2m"]), "feels": round(c["apparent_temperature"]),
            "desc": WMO.get(c["weather_code"], "unsettled"), "kind": kind(c["weather_code"]), "night": not c.get("is_day", 1), "wind": round(c["wind_speed_10m"]), "wind_dir": round(c.get("wind_direction_10m") or 0),
            "humidity": c["relative_humidity_2m"], "pop": days[0]["pop"] if days else 0, "sunrise": _hhmm(dl["sunrise"][0]), "sunset": _hhmm(dl["sunset"][0]), "hours": hours, "days": days,
            "updated": time.strftime("%H:%M", time.localtime(t)), "stale": time.time() - t > TTL * 3}


def where(place=None):
    """-> (name, lat, lon, label) for a named place or where you are (iPhone fix, home, Wi-Fi, IP), or None."""
    from . import maps
    if place:
        g = maps.geocode(place)
        return (g["name"], g["lat"], g["lon"], g.get("label")) if g else None
    me = maps.my_location()
    if not me:
        return None
    name = me["label"]
    if me["source"] in ("iphone", "home", "today") and settings.get("home_area"):
        parts = [x.strip() for x in settings.get("home_area").split(",")]
        name = parts[1] if re.search(r"\d", parts[0]) and len(parts) > 1 else parts[0]            # a town, not your street address, on the card
    return (name, me["lat"], me["lon"], None)


def summary(c, tomorrow=False):
    """One short spoken sentence."""
    if tomorrow and len(c["days"]) > 1:
        x = c["days"][1]
        return f"Tomorrow: {x['desc']}, a high of {x['hi']} and a low of {x['lo']}" + (f", with a {x['pop']} percent chance of rain." if x["pop"] >= 20 else ".")
    x = c["days"][0]
    return f"It's {c['temp']} degrees and {c['desc']}, with a high of {x['hi']} today." + (f" {x['pop']} percent chance of rain." if x["pop"] >= 30 else "")


def prefetch(imperial=None):
    """Warm the cache for where you are (called in the background; one request)."""
    try:
        w = where()
        if w:
            fetch(w[1], w[2], settings.get("units") == "imperial" if imperial is None else imperial)
    except Exception:
        pass
