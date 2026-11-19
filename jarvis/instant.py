"""Instant answers with no brain call: unit and currency conversion, time in another city. (Simple maths and the local time/date are in skills.py.)
Sports scores and flight status need a live data service (and usually a key), so they are NOT built."""
import json
import re
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

UNITS = {  # name -> (kind, factor to the base unit)
    "mm": ("len", .001), "millimetre": ("len", .001), "millimeter": ("len", .001), "cm": ("len", .01), "centimetre": ("len", .01), "centimeter": ("len", .01), "m": ("len", 1), "metre": ("len", 1), "meter": ("len", 1),
    "km": ("len", 1000), "kilometre": ("len", 1000), "kilometer": ("len", 1000), "inch": ("len", .0254), "foot": ("len", .3048), "feet": ("len", .3048), "yard": ("len", .9144), "mile": ("len", 1609.344),
    "g": ("mass", .001), "gram": ("mass", .001), "kg": ("mass", 1), "kilo": ("mass", 1), "kilogram": ("mass", 1), "ounce": ("mass", .0283495), "pound": ("mass", .45359237), "lb": ("mass", .45359237), "stone": ("mass", 6.35029),
    "ml": ("vol", .001), "millilitre": ("vol", .001), "milliliter": ("vol", .001), "litre": ("vol", 1), "liter": ("vol", 1), "gallon": ("vol", 3.78541), "quart": ("vol", .946353), "pint": ("vol", .473176), "cup": ("vol", .236588),
    "tablespoon": ("vol", .0147868), "teaspoon": ("vol", .00492892), "kph": ("speed", 1), "mph": ("speed", 1.609344), "knot": ("speed", 1.852), "mps": ("speed", 3.6),
}
TEMP = {"celsius": "c", "centigrade": "c", "fahrenheit": "f", "kelvin": "k"}
CITIES = {"new york": "America/New_York", "los angeles": "America/Los_Angeles", "chicago": "America/Chicago", "miami": "America/New_York", "denver": "America/Denver", "san francisco": "America/Los_Angeles",
          "seattle": "America/Los_Angeles", "toronto": "America/Toronto", "vancouver": "America/Vancouver", "mexico city": "America/Mexico_City", "sao paulo": "America/Sao_Paulo", "buenos aires": "America/Argentina/Buenos_Aires",
          "london": "Europe/London", "dublin": "Europe/Dublin", "paris": "Europe/Paris", "berlin": "Europe/Berlin", "madrid": "Europe/Madrid", "rome": "Europe/Rome", "amsterdam": "Europe/Amsterdam", "athens": "Europe/Athens",
          "moscow": "Europe/Moscow", "istanbul": "Europe/Istanbul", "cairo": "Africa/Cairo", "johannesburg": "Africa/Johannesburg", "lagos": "Africa/Lagos", "nairobi": "Africa/Nairobi", "dubai": "Asia/Dubai",
          "mumbai": "Asia/Kolkata", "delhi": "Asia/Kolkata", "bangkok": "Asia/Bangkok", "singapore": "Asia/Singapore", "hong kong": "Asia/Hong_Kong", "shanghai": "Asia/Shanghai", "beijing": "Asia/Shanghai",
          "tokyo": "Asia/Tokyo", "seoul": "Asia/Seoul", "sydney": "Australia/Sydney", "melbourne": "Australia/Melbourne", "canberra": "Australia/Sydney", "brisbane": "Australia/Brisbane", "perth": "Australia/Perth",
          "auckland": "Pacific/Auckland", "honolulu": "Pacific/Honolulu", "anchorage": "America/Anchorage", "reykjavik": "Atlantic/Reykjavik", "lisbon": "Europe/Lisbon", "stockholm": "Europe/Stockholm", "oslo": "Europe/Oslo", "tel aviv": "Asia/Jerusalem"}
CURRENCY = {"dollar": "USD", "dollars": "USD", "usd": "USD", "us dollar": "USD", "us dollars": "USD", "euro": "EUR", "euros": "EUR", "eur": "EUR", "pound": "GBP", "pounds": "GBP", "gbp": "GBP", "sterling": "GBP",
            "yen": "JPY", "jpy": "JPY", "australian dollar": "AUD", "australian dollars": "AUD", "aud": "AUD", "canadian dollar": "CAD", "canadian dollars": "CAD", "cad": "CAD", "swiss franc": "CHF", "francs": "CHF",
            "yuan": "CNY", "rupee": "INR", "rupees": "INR", "inr": "INR", "peso": "MXN", "pesos": "MXN", "krona": "SEK", "won": "KRW", "new zealand dollar": "NZD", "nzd": "NZD", "real": "BRL", "reais": "BRL"}


def _unit(name):
    n = name.strip().lower().rstrip(".")
    n = re.sub(r"\b(metres|meters|inches|feet|miles|pounds|ounces|grams|kilos|litres|liters|gallons|yards|knots|cups)\b", lambda m: {"inches": "inch", "feet": "foot"}.get(m.group(1), m.group(1)[:-1]), n)
    n = re.sub(r"^(?:square |cubic )", "", n)
    if n in TEMP or n.replace("degrees ", "") in TEMP:
        return ("temp", TEMP[n.replace("degrees ", "")])
    n = {"kilometres per hour": "kph", "kilometers per hour": "kph", "miles per hour": "mph", "kilometers an hour": "kph", "miles an hour": "mph", "metres per second": "mps", "meters per second": "mps"}.get(n, n)
    if n in UNITS:
        return UNITS[n][0], n
    if n.endswith("s") and n[:-1] in UNITS:
        return UNITS[n[:-1]][0], n[:-1]
    return None


def convert(value, a, b):
    ua, ub = _unit(a), _unit(b)
    if not ua or not ub or ua[0] != ub[0]:
        return None
    if ua[0] == "temp":
        c = value if ua[1] == "c" else (value - 32) * 5 / 9 if ua[1] == "f" else value - 273.15
        return c if ub[1] == "c" else c * 9 / 5 + 32 if ub[1] == "f" else c + 273.15
    return value * UNITS[ua[1]][1] / UNITS[ub[1]][1]


def fmt(x):
    return f"{x:.0f}" if abs(x) >= 100 else f"{x:.2f}".rstrip("0").rstrip(".") if abs(x) >= 1 else f"{x:.3g}"


def currency(amount, a, b):
    """Daily reference rates from frankfurter.app (free, no key). None if offline or unknown."""
    ca, cb = CURRENCY.get(a.strip().lower()), CURRENCY.get(b.strip().lower())
    if not ca or not cb:
        return None
    if ca == cb:
        return amount
    try:
        with urllib.request.urlopen(f"https://api.frankfurter.app/latest?amount={amount}&from={ca}&to={cb}", timeout=5) as r:
            return json.load(r)["rates"][cb]
    except Exception:
        return None


def city_time(city):
    tz = CITIES.get(city.strip().lower())
    if not tz:
        return None
    now = datetime.now(ZoneInfo(tz))
    mine = datetime.now().astimezone()
    diff = (now.utcoffset() - mine.utcoffset()).total_seconds() / 3600
    rel = "the same time as here" if diff == 0 else f"{abs(diff):g} hour{'s' if abs(diff) != 1 else ''} {'ahead of' if diff > 0 else 'behind'} you"
    day = "" if now.date() == mine.date() else (" tomorrow" if now.date() > mine.date() else " yesterday")
    return f"It's {now.strftime('%-I:%M %p')}{day} in {city.strip().title()}, {rel}."


def parse_command(text):
    """-> {"speech": ...} or None."""
    low = text.lower().strip(" .?!")
    m = re.fullmatch(r"(?:what(?:'s| is) the )?time (?:is it )?in ([a-z ]+?)(?: right now| now| at the moment)?|what time is it in ([a-z ]+?)(?: right now| now)?|what(?:'s| is) the time in ([a-z ]+)", low)
    if m:
        city = next(g for g in m.groups() if g)
        return {"speech": city_time(city) or f"I don't know the time zone for {city.title()}."} if city.strip() in CITIES else None
    m = re.fullmatch(r"(?:how (?:much|many) is |what(?:'s| is) |convert )?(\d+(?:\.\d+)?|a|an|one) ([a-z ]+?) (?:in|to|into) ([a-z ]+?)", low)
    if m:
        val = 1.0 if m.group(1) in ("a", "an", "one") else float(m.group(1))
        a, b = m.group(2), m.group(3)
        if CURRENCY.get(a.strip()) and CURRENCY.get(b.strip()):
            r = currency(val, a, b)
            return {"speech": f"{fmt(val)} {a} is about {fmt(r)} {b}, at today's rate."} if r is not None else {"speech": "I couldn't get the exchange rate just now."}
        r = convert(val, a, b)
        if r is not None:
            return {"speech": f"{fmt(val)} {a} is {fmt(r)} {b}."}
    m = re.fullmatch(r"how (?:much|many) ([a-z ]+?) (?:is|are|in) (\d+(?:\.\d+)?) ([a-z ]+)", low)
    if m:
        r = convert(float(m.group(2)), m.group(3), m.group(1))
        if r is not None:
            return {"speech": f"{fmt(float(m.group(2)))} {m.group(3)} is {fmt(r)} {m.group(1)}."}
    m = re.fullmatch(r"(?:how (?:much|many) )([a-z ]+?) (?:is|are|in) (?:in )?(?:a|an|one|1) ([a-z ]+)", low)
    if m:
        r = convert(1.0, m.group(2), m.group(1))
        if r is not None:
            return {"speech": f"One {m.group(2)} is {fmt(r)} {m.group(1)}."}
    return None
