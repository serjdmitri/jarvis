"""The Markets Desk: a READ-ONLY research and awareness desk. Ideas only from "Cloud9 Markets" (one master routing spoken words to specialist seats, charts that obey the voice,
every number from a real feed, analysis kept apart from action); everything here is written fresh.

HARD RULES (they override everything): it does NOT place, queue, simulate or prepare trades, has no broker login, no key with trading rights, no buy or sell button and no way to move
money; "buy 10 Tesla" gets the no-trading reply. It gives no personal advice ("should I buy Nvidia" gets the not-advice reply); it explains structure and context. Forecast output is
statistical history only, always labelled "MODEL OUTPUT, NOT A PREDICTION", never a price target. "Educational, not financial advice" sits in the footer and is spoken once per session.

Seats are NOT processes or models: they are named roles (a label, a colour, a short style line, the data they may read) inside the one brain. The master (this router) turns words into a
structured, validated, clamped route {seat, intent, symbol, range, ...}; code runs only the matching allowlisted action; an unknown seat or intent does nothing.
Data: every value carries its source and time (Datum). A feed that fails shows "—" and "price feed is down": nothing is ever filled in from the model's memory, and spoken answers are
checked so that any number not found in the data they were given is dropped. Keyless sources first (Yahoo Finance chart and search endpoints, unofficial but what yfinance itself uses; SEC EDGAR);
Finnhub (free key, saved mode 600 outside git, never printed) for quotes and news when present. Quotes are cached 60 s, news 15 min, profiles a day; no polling faster than a minute."""
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config, settings

NOTADVICE = "Educational, not financial advice."
UA = "Mozilla/5.0 (X11; Linux x86_64) jarvis-local-assistant"
BASES = {"yahoo": os.environ.get("JARVIS_MKT_YAHOO") or "https://query1.finance.yahoo.com", "finnhub": "https://finnhub.io/api/v1", "edgar": "https://data.sec.gov", "edgar_www": "https://www.sec.gov"}
TTL = {"quote": 60, "candles": 300, "news": 900, "profile": 86400, "search": 3600, "movers": 300, "filings": 3600}
LEDGER = config.DATA / "market_ledger.jsonl"
ALERTS = config.DATA / "market_alerts.json"
ECON = config.DATA / "econ_calendar.json"
KEYFILE = Path(settings.PATH).parent / "finnhub_key"

SEATS = {
    "MASTER": {"name": "MASTER", "color": "#9cc2ff", "role": "routes and merges, speaks in the normal voice"},
    "MACRO": {"name": "MACRO", "color": "#4be39a", "role": "rates, central bank dates, inflation and jobs releases, the calendar"},
    "FLOW": {"name": "FLOW", "color": "#ffd84a", "role": "public disclosures only, always labelled with the filing date"},
    "SCOUT": {"name": "SCOUT", "color": "#ff9a3c", "role": "what is moving today"},
    "ANALYST": {"name": "ANALYST", "color": "#a77bff", "role": "what a company does, results and risks in plain words"},
    "CHARTIST": {"name": "CHARTIST", "color": "#4fd8ff", "role": "owns the chart: symbol, range, indicators, levels"},
    "FORECAST": {"name": "FORECAST", "color": "#ff7bc8", "role": "statistical summary of the past, never a prediction"},
    "RISK": {"name": "RISK", "color": "#ff4b5c", "role": "what could go wrong; cannot be turned off for market answers"},
    "LEDGER": {"name": "LEDGER", "color": "#9aa8bd", "role": "journal of what the desk said, and data feed health"},
}
INTENTS = ("open_desk", "pull_up", "range", "chart_type", "indicator", "levels", "compare", "zoom", "back", "watch_add", "watch_remove", "watch_list", "alert_add", "briefing", "movers", "macro",
           "flow", "analyst", "forecast", "risk", "ledger", "health", "sources", "close", "refuse_trade", "refuse_advice", "session")
SEAT_OF = {"open_desk": "MASTER", "pull_up": "CHARTIST", "range": "CHARTIST", "chart_type": "CHARTIST", "indicator": "CHARTIST", "levels": "CHARTIST", "compare": "CHARTIST", "zoom": "CHARTIST", "back": "CHARTIST",
           "watch_add": "MASTER", "watch_remove": "MASTER", "watch_list": "MASTER", "alert_add": "MASTER", "briefing": "MACRO", "movers": "SCOUT", "macro": "MACRO", "flow": "FLOW", "analyst": "ANALYST",
           "forecast": "FORECAST", "risk": "RISK", "ledger": "LEDGER", "health": "LEDGER", "sources": "LEDGER", "close": "MASTER", "refuse_trade": "MASTER", "refuse_advice": "MASTER", "session": "MACRO"}
RANGES = {"1d": ("1d", "5m"), "5d": ("5d", "30m"), "1mo": ("1mo", "1d"), "3mo": ("3mo", "1d"), "6mo": ("6mo", "1d"), "1y": ("1y", "1d"), "5y": ("5y", "1wk")}


# ----------------------------------------------------------------------------- data layer: every value has a source and a time
class Datum(dict):
    """{"value", "source", "t"}"""


_cache, _state = {}, {}
_lock = threading.RLock()
_last_call = {"t": 0.0}


def key():
    try:
        k = KEYFILE.read_text().strip()
        return k if re.fullmatch(r"[A-Za-z0-9]{10,60}", k) else None
    except OSError:
        return None


def save_key(v):
    v = (v or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{10,60}", v):
        return False, "That does not look like a Finnhub key (letters and numbers only)."
    KEYFILE.parent.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(KEYFILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(v + "\n")
    os.chmod(KEYFILE, 0o600)
    return True, "Finnhub key saved."


def health():
    """-> {source: "green"|"yellow"|"red"|"idle"} for the data-health chip."""
    out = {}
    for src, st in _state.items():
        if st.get("fail", 0) >= 2 or (st.get("err_t") and st.get("err_t", 0) > st.get("ok_t", 0) and time.time() - st["err_t"] < 300):
            out[src] = "red"
        elif st.get("ok_t") and time.time() - st["ok_t"] > 900:
            out[src] = "yellow"
        elif st.get("ok_t"):
            out[src] = "green"
        else:
            out[src] = "idle"
    return out


def fetch(source, url, kind, params=None, headers=None, parse="json"):
    """One cached, rate-limited, backed-off call -> (Datum or None, error text). The cached copy is returned (marked stale) when the feed is failing. Never raises."""
    ttl = TTL.get(kind, 60)
    ck = (source, url)
    now = time.time()
    with _lock:
        hit = _cache.get(ck)
        st = _state.setdefault(source, {"fail": 0, "ok_t": 0, "err_t": 0, "wait_until": 0})
        if hit and now - hit["t"] < ttl:
            return hit["datum"], None
        if now < st["wait_until"]:                                   # backing off after a failure or a 429
            return (Datum(hit["datum"], stale=True), "feed backing off") if hit else (None, "feed backing off")
        gap = 1.0 - (now - _last_call["t"])                           # the queue: one provider call a second at most
        if gap > 0:
            time.sleep(gap)
        _last_call["t"] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            raw = r.read(3_000_000)
        data = json.loads(raw) if parse == "json" else raw.decode("utf-8", "ignore")
        d = Datum(value=data, source=source, t=time.time())
        with _lock:
            _cache[ck] = {"t": d["t"], "datum": d}
            st.update(fail=0, ok_t=d["t"])
        return d, None
    except urllib.error.HTTPError as e:
        why = f"{source} answered {e.code}"
        wait = 60 if e.code == 429 else 5
    except (urllib.error.URLError, OSError, TimeoutError):
        why, wait = f"{source} did not answer", 5
    except ValueError:
        why, wait = f"{source} sent something unreadable", 5
    with _lock:
        st["fail"] += 1
        st["err_t"] = time.time()
        st["wait_until"] = time.time() + min(300, wait * (2 ** (st["fail"] - 1)))      # exponential backoff, five minutes at most
        hit = _cache.get(ck)
    return (Datum(hit["datum"], stale=True), why) if hit else (None, why)


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def quote(sym):
    """-> Datum with {"price","change","pct","prev","volume"} or None. Finnhub when a key is saved, else Yahoo's chart meta (labelled)."""
    k = key()
    if k:
        d, err = fetch("finnhub", f"{BASES['finnhub']}/quote?symbol={urllib.parse.quote(sym)}&token={k}", "quote")
        v = (d or {}).get("value") or {}
        if d and _num(v.get("c")) and v.get("c") != 0:
            return Datum(value={"price": v["c"], "change": v.get("d"), "pct": v.get("dp"), "prev": v.get("pc"), "volume": None}, source="Finnhub", t=d["t"], stale=d.get("stale", False))
    c = candles(sym, "5d")
    if c and c["value"]["meta"].get("price") is not None:
        m = c["value"]["meta"]
        prev = m.get("prev")
        ch = (m["price"] - prev) if prev else None
        return Datum(value={"price": m["price"], "change": ch, "pct": (ch / prev * 100) if prev else None, "prev": prev, "volume": m.get("volume")}, source="Yahoo Finance (unofficial)", t=c["t"], stale=c.get("stale", False))
    return None


def candles(sym, rng="3mo"):
    """-> Datum {"value": {"t":[...], "o","h","l","c","v", "meta": {...}}} or None (Yahoo chart endpoint, no key)."""
    r, iv = RANGES.get(rng, RANGES["3mo"])
    url = f"{BASES['yahoo']}/v8/finance/chart/{urllib.parse.quote(sym)}?range={r}&interval={iv}"
    d, err = fetch("yahoo", url, "candles")
    try:
        res = d["value"]["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        ts = res["timestamp"]
        rows = [(t, q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]) for i, t in enumerate(ts) if all(_num(q[k][i]) is not None for k in ("open", "high", "low", "close"))]
        if not rows:
            return None
        meta = res.get("meta", {})
        return Datum(value={"t": [r[0] for r in rows], "o": [r[1] for r in rows], "h": [r[2] for r in rows], "l": [r[3] for r in rows], "c": [r[4] for r in rows], "v": [r[5] or 0 for r in rows],
                            "meta": {"price": _num(meta.get("regularMarketPrice")), "prev": _num(meta.get("chartPreviousClose")) or _num(meta.get("previousClose")), "volume": _num(meta.get("regularMarketVolume")),
                                     "currency": meta.get("currency"), "name": meta.get("longName") or meta.get("shortName"), "hi52": _num(meta.get("fiftyTwoWeekHigh")), "lo52": _num(meta.get("fiftyTwoWeekLow"))}},
                     source="Yahoo Finance (unofficial)", t=d["t"], stale=d.get("stale", False))
    except (KeyError, IndexError, TypeError):
        return None


def search(q):
    """-> list of {"symbol","name"} equities/ETFs/crypto from Yahoo's search (no key)."""
    d, _ = fetch("yahoo", f"{BASES['yahoo']}/v1/finance/search?q={urllib.parse.quote(q)}&quotesCount=6&newsCount=0", "search")
    out = []
    for x in ((d or {}).get("value") or {}).get("quotes", []) or []:
        if x.get("quoteType") in ("EQUITY", "ETF", "CRYPTOCURRENCY", "INDEX") and x.get("symbol") and "=F" not in x["symbol"]:
            out.append({"symbol": x["symbol"], "name": x.get("shortname") or x.get("longname") or x["symbol"]})
    return out


def news(sym=None, n=5):
    k = key()
    if k and sym:
        to, fr = date.today(), date.today() - timedelta(days=7)
        d, _ = fetch("finnhub", f"{BASES['finnhub']}/company-news?symbol={urllib.parse.quote(sym)}&from={fr}&to={to}&token={k}", "news")
        items = (d or {}).get("value")
        if isinstance(items, list):
            return Datum(value=[{"title": i.get("headline", "")[:140], "t": i.get("datetime")} for i in items[:n]], source="Finnhub", t=d["t"])
    d, _ = fetch("yahoo", f"{BASES['yahoo']}/v1/finance/search?q={urllib.parse.quote(sym or 'stock market')}&quotesCount=0&newsCount={n}", "news")
    items = ((d or {}).get("value") or {}).get("news")
    return Datum(value=[{"title": (i.get("title") or "")[:140], "t": i.get("providerPublishTime")} for i in items[:n]], source="Yahoo Finance (unofficial)", t=d["t"]) if items else None


def movers(kind="day_gainers", n=5):
    d, _ = fetch("yahoo", f"{BASES['yahoo']}/v1/finance/screener/predefined/saved?scrIds={kind}&count={n}", "movers")
    try:
        qs = d["value"]["finance"]["result"][0]["quotes"]
        return Datum(value=[{"symbol": q["symbol"], "name": (q.get("shortName") or q["symbol"])[:30], "pct": _num(q.get("regularMarketChangePercent")), "price": _num(q.get("regularMarketPrice"))} for q in qs[:n]],
                     source="Yahoo Finance (unofficial)", t=d["t"], stale=d.get("stale", False))
    except (KeyError, IndexError, TypeError):
        return None


def filings(sym):
    """Recent public SEC filings (EDGAR) with their FILING dates -> Datum list, or None. Needs a contact string in settings (edgar_contact): EDGAR requires a descriptive User-Agent."""
    contact = settings.get("edgar_contact")
    if not contact:
        return "needs_contact"
    hdr = {"User-Agent": f"JarvisLocalAssistant {contact}"}
    d, _ = fetch("edgar", f"{BASES['edgar_www']}/files/company_tickers.json", "profile", headers=hdr)
    cik = None
    for v in ((d or {}).get("value") or {}).values():
        if str(v.get("ticker", "")).upper() == sym.upper():
            cik = str(v["cik_str"]).zfill(10)
    if not cik:
        return None
    s, _ = fetch("edgar", f"{BASES['edgar']}/submissions/CIK{cik}.json", "filings", headers=hdr)
    try:
        r = s["value"]["filings"]["recent"]
        items = [{"form": r["form"][i], "date": r["filingDate"][i]} for i in range(min(40, len(r["form"]))) if r["form"][i] in ("4", "8-K", "10-K", "10-Q", "13F-HR", "SC 13G", "SC 13D", "144")][:6]
        return Datum(value=items, source="SEC EDGAR", t=s["t"])
    except (KeyError, TypeError):
        return None


# ----------------------------------------------------------------------------- symbols
NAMES = {"apple": "AAPL", "microsoft": "MSFT", "nvidia": "NVDA", "tesla": "TSLA", "amazon": "AMZN", "google": "GOOGL", "alphabet": "GOOGL", "meta": "META", "facebook": "META", "netflix": "NFLX", "amd": "AMD",
         "intel": "INTC", "disney": "DIS", "boeing": "BA", "coca cola": "KO", "coke": "KO", "pepsi": "PEP", "walmart": "WMT", "costco": "COST", "starbucks": "SBUX", "nike": "NKE", "visa": "V",
         "mastercard": "MA", "jpmorgan": "JPM", "bank of america": "BAC", "goldman sachs": "GS", "exxon": "XOM", "chevron": "CVX", "pfizer": "PFE", "johnson and johnson": "JNJ", "ford": "F",
         "general motors": "GM", "uber": "UBER", "airbnb": "ABNB", "paypal": "PYPL", "oracle": "ORCL", "salesforce": "CRM", "adobe": "ADBE", "ibm": "IBM", "qualcomm": "QCOM", "broadcom": "AVGO",
         "palantir": "PLTR", "coinbase": "COIN", "gamestop": "GME", "spotify": "SPOT", "shopify": "SHOP", "bitcoin": "BTC-USD", "ethereum": "ETH-USD", "s and p": "^GSPC", "s&p": "^GSPC", "s and p 500": "^GSPC",
         "the s and p": "^GSPC", "nasdaq": "^IXIC", "dow": "^DJI", "dow jones": "^DJI", "russell": "^RUT", "vix": "^VIX", "gold": "GC=F", "oil": "CL=F", "spy": "SPY", "qqq": "QQQ"}
INDEXES = [("S&P 500", "^GSPC"), ("Nasdaq", "^IXIC"), ("Dow", "^DJI"), ("VIX", "^VIX")]


def resolve(name):
    """A spoken name or ticker -> (symbol, None) | (None, [candidates]) when ambiguous | (None, None) when unknown. Local list first, then Yahoo's search."""
    n = re.sub(r"[^a-z0-9&. ]", "", (name or "").lower()).strip()
    n = re.sub(r"^(?:the |a |an )", "", n)
    n = re.sub(r"\b(?:stock|shares?|inc|corp|corporation|company|ticker)\b", "", n).strip()
    if not n:
        return None, None
    if n in NAMES:
        return NAMES[n], None
    if re.fullmatch(r"[a-z]{1,5}", n) and n.upper() in set(NAMES.values()):
        return n.upper(), None
    res = search(n)
    if not res:
        return (n.upper(), None) if re.fullmatch(r"[a-z]{1,5}", n) else (None, None)
    words = set(re.findall(r"[a-z0-9]+", n))
    good = [r for r in res if words & set(re.findall(r"[a-z0-9]+", r["name"].lower()))] or res
    if len(good) > 1 and good[0]["name"].split()[0].lower() != good[1]["name"].split()[0].lower() and not (words & {g["symbol"].lower() for g in good[:1]}):
        return None, good[:3]
    return good[0]["symbol"], None


# ----------------------------------------------------------------------------- the session clock (New York time, US holidays; the table may be incomplete)
HOLIDAYS = {date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
            date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24)}


def session(now=None):
    """-> {"state": open|pre-market|after-hours|closed, "label", "countdown", "note"} from New York time."""
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    t = (now or datetime.now(ny)).astimezone(ny)

    def trading(d):
        return d.weekday() < 5 and d not in HOLIDAYS

    def at(d, h, m=0):
        return datetime(d.year, d.month, d.day, h, m, tzinfo=ny)
    d = t.date()
    if trading(d):
        o, c = at(d, 9, 30), at(d, 16)
        if o <= t < c:
            state, nxt, what = "open", c, "closes"
        elif at(d, 4) <= t < o:
            state, nxt, what = "pre-market", o, "opens"
        elif c <= t < at(d, 20):
            state, nxt, what = "after-hours", at(d, 20), "after-hours ends"
        else:
            state, nxt, what = "closed", None, "opens"
    else:
        state, nxt, what = "closed", None, "opens"
    if nxt is None:
        dd = d if (trading(d) and t < at(d, 4)) else d + timedelta(days=1)
        while not trading(dd):
            dd += timedelta(days=1)
        nxt = at(dd, 9, 30)
    secs = int((nxt - t).total_seconds())
    h, m = divmod(secs // 60, 60)
    return {"state": state, "label": state.upper(), "countdown": f"{what} in {h}h {m:02d}m" if h < 48 else f"{what} in {h // 24} days", "note": "calendar may be incomplete", "ny": t.strftime("%a %H:%M")}


# ----------------------------------------------------------------------------- computed levels, indicators, the statistical band (all by code, from the data)
def sma(vals, n):
    out = []
    for i in range(len(vals)):
        out.append(round(sum(vals[i - n + 1:i + 1]) / n, 4) if i >= n - 1 else None)
    return out


def levels(c1y, c_range):
    """Descriptive horizontal levels (max 12): 52-week high and low, recent swing highs and lows, the 50 and 200 day averages. Each: {"price","label","how"}. Not signals."""
    out = []
    cl, hi, lo = c1y["c"], c1y["h"], c1y["l"]
    if len(cl) >= 20:
        out.append({"price": round(max(hi[-252:]), 2), "label": "52-WEEK HIGH", "how": "highest high in the last 252 trading days"})
        out.append({"price": round(min(lo[-252:]), 2), "label": "52-WEEK LOW", "how": "lowest low in the last 252 trading days"})
    for n in (50, 200):
        if len(cl) >= n:
            out.append({"price": round(sum(cl[-n:]) / n, 2), "label": f"{n}-DAY AVERAGE", "how": f"mean of the last {n} closes"})
    w = 5
    sh, sl = [], []
    for i in range(w, len(hi) - w):
        if hi[i] == max(hi[i - w:i + w + 1]):
            sh.append(hi[i])
        if lo[i] == min(lo[i - w:i + w + 1]):
            sl.append(lo[i])
    last = cl[-1] if cl else 0
    for lab, arr in (("RESISTANCE", [x for x in sh[-12:] if x > last]), ("SUPPORT", [x for x in sl[-12:] if x < last])):
        picked = []
        for x in sorted(arr, key=lambda v: abs(v - last)):
            if all(abs(x - p) / max(p, 1e-9) > 0.015 for p in picked):
                picked.append(x)
            if len(picked) == 3:
                break
        out += [{"price": round(p, 2), "label": lab, "how": "a recent swing " + ("high" if lab == "RESISTANCE" else "low") + " (5 bars either side)"} for p in picked]
    return out[:12]


def band(closes, days=20):
    """A statistical view of the past, NOT a prediction: a straight-line trend through the last 60 closes with an 80 percent range from its own daily volatility. -> list of {"i","lo","mid","hi"}."""
    c = closes[-60:]
    if len(c) < 20:
        return []
    rets = [math.log(c[i] / c[i - 1]) for i in range(1, len(c)) if c[i - 1] > 0 and c[i] > 0]
    sd = math.sqrt(sum((r - sum(rets) / len(rets)) ** 2 for r in rets) / max(1, len(rets) - 1))
    n = len(c)
    xm, ym = (n - 1) / 2, sum(c) / n
    slope = sum((i - xm) * (c[i] - ym) for i in range(n)) / max(1e-9, sum((i - xm) ** 2 for i in range(n)))
    out = []
    for k in range(1, days + 1):
        mid = c[-1] + slope * k
        w = 1.2816 * sd * c[-1] * math.sqrt(k)
        out.append({"i": k, "lo": round(mid - w, 2), "mid": round(mid, 2), "hi": round(mid + w, 2)})
    return out


def stats(cl):
    """Plain context numbers from closes: day change, 20-day volatility (daily percent), distance from the 52-week high."""
    if len(cl) < 3:
        return {}
    rets = [(cl[i] / cl[i - 1] - 1) * 100 for i in range(max(1, len(cl) - 20), len(cl)) if cl[i - 1]]
    m = sum(rets) / len(rets)
    return {"vol20": round(math.sqrt(sum((r - m) ** 2 for r in rets) / max(1, len(rets) - 1)), 2), "from_high": round((cl[-1] / max(cl[-252:]) - 1) * 100, 1)}


def chart_payload(sym, rng="3mo", indicators=(), want_levels=False, want_band=False, compare=None):
    """Everything the chart draws, computed here, each with a source and time. -> dict or {"error"}."""
    c = candles(sym, rng)
    if not c:
        return {"error": "price feed is down", "symbol": sym}
    v = c["value"]
    out = {"symbol": sym, "range": rng, "name": v["meta"].get("name") or sym, "source": c["source"], "t": c["t"], "stale": bool(c.get("stale")), "candles": [{"time": v["t"][i], "open": v["o"][i], "high": v["h"][i], "low": v["l"][i], "close": v["c"][i], "volume": v["v"][i]} for i in range(len(v["t"]))],
           "indicators": {}, "levels": [], "band": [], "currency": v["meta"].get("currency")}
    for ind in indicators:
        m = re.fullmatch(r"ma(\d{1,3})", ind)
        if m and 2 <= int(m.group(1)) <= 200:
            out["indicators"][ind] = [{"time": v["t"][i], "value": x} for i, x in enumerate(sma(v["c"], int(m.group(1)))) if x is not None]
    if want_levels:
        c1 = candles(sym, "1y")
        out["levels"] = levels(c1["value"] if c1 else v, v)
    if want_band:
        out["band"] = band(v["c"])
        out["band_label"] = "MODEL OUTPUT, NOT A PREDICTION: a trend line and 80 percent range from past volatility"
    if compare:
        c2 = candles(compare, rng)
        if c2:
            base, base2 = v["c"][0], c2["value"]["c"][0]
            out["compare"] = {"symbol": compare, "source": c2["source"], "series": [{"time": c2["value"]["t"][i], "value": round(x / base2 * base, 4)} for i, x in enumerate(c2["value"]["c"])]}
    return out


# ----------------------------------------------------------------------------- the check: a spoken number must come from the data it was given
NUM = re.compile(r"(?<![\w.])-?\$?\d[\d,]*(?:\.\d+)?%?")


def _variants(x):
    out = set()
    try:
        f = float(x)
    except (TypeError, ValueError):
        return out
    for nd in (0, 1, 2, 3):
        r = round(f, nd)
        out |= {f"{r:.{nd}f}", f"{abs(r):.{nd}f}", f"{r:,.{nd}f}", f"{abs(r):,.{nd}f}"}
        if nd == 0:
            out |= {str(int(r)), str(abs(int(r)))}
    return out


def numbers_in(obj):
    """Every number anywhere in a data structure (as the strings a sentence could use)."""
    found = set()
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        found |= _variants(obj)
    elif isinstance(obj, str):
        for m in NUM.findall(obj):
            found |= _variants(m.replace("$", "").replace(",", "").replace("%", ""))
    elif isinstance(obj, dict):
        for v in obj.values():
            found |= numbers_in(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            found |= numbers_in(v)
    return found


SAFE_WORDS = re.compile(r"\b(\d+)[- ](?:day|week|month|year|hour|minute)s?\b|\b(?:top|first|second|third|one|two|three|four|five)\b|\bs&p 500\b|\bs and p 500\b|\bnasdaq 100\b|\b52\b|\bq[1-4]\b|\b20\d\d\b", re.I)


def check_numbers(answer, data):
    """Keep only the sentences whose numbers all appear in `data` (a dict/list of what the desk was given). A sentence with a number the data does not contain is dropped; if nothing is
    left the answer is "—". -> (text, dropped_count). Ranges and indicator names (50-day, 3 months, 52-week) are not prices and are allowed."""
    ok = numbers_in(data)
    keep, dropped = [], 0
    for s in re.split(r"(?<=[.!?])\s+", (answer or "").strip()):
        probe = SAFE_WORDS.sub(" ", s)
        bad = [m for m in NUM.findall(probe) if m.replace("$", "").replace(",", "").replace("%", "").lstrip("-") not in ok and m.replace("$", "").replace(",", "").replace("%", "") not in ok]
        if bad:
            dropped += 1
        elif s:
            keep.append(s)
    return (" ".join(keep) if keep else "—"), dropped


# ----------------------------------------------------------------------------- the master: words -> a validated route
TRADE = re.compile(r"\b(?:buy|sell|short|purchase|trade|invest|liquidate|dump|go long|go short|place an? order|put (?:my )?(?:savings|money)|send (?:money|cash|\$)|transfer (?:money|funds)|wire)\b", re.I)
MARKETY = re.compile(r"\b(?:stock|stocks|shares?|crypto|bitcoin|ethereum|etf|savings|everything|portfolio|position|options?|calls?|puts?|money|cash|funds|dollars|shares|"
                     + "|".join(re.escape(n) for n in NAMES) + r")\b|\b\d+ (?:shares|of)\b", re.I)
TICKER = re.compile(r"\b[A-Z]{2,5}\b")                                   # case-sensitive: a spoken ticker like TSLA, not the word "milk"
ADVICE = re.compile(r"\b(?:should i|do you recommend|would you recommend|is it a good (?:buy|time|idea)|what should i (?:buy|invest)|tell me what to (?:buy|sell)|good investment|worth buying|financial advice)\b", re.I)
MARKET_WORDS = re.compile(r"\b(?:market|markets|stock|stocks|nasdaq|dow|s and p|s&p|fed|rates?|inflation|earnings|watchlist|ticker|chart|candles?|trading desk|the desk|portfolio)\b", re.I)


def _range_of(t):
    m = re.search(r"\b(?:for |over |last |past )?(?:the )?(?:last |past )?(\d+|a|one|two|three|six|five|ten) (day|week|month|year)s?\b", t)
    if m:
        n = {"a": 1, "one": 1, "two": 2, "three": 3, "six": 6, "five": 5, "ten": 10}.get(m.group(1)) or int(m.group(1))
        u = m.group(2)
        if u == "day":
            return "1d" if n <= 1 else "5d"
        if u == "week":
            return "5d" if n <= 1 else "1mo"
        if u == "month":
            return "1mo" if n <= 1 else "3mo" if n <= 3 else "6mo"
        return "1y" if n <= 1 else "5y"
    for pat, r in ((r"\btoday\b|\bintraday\b", "1d"), (r"\bthis week\b", "5d"), (r"\bthis month\b|\blast month\b", "1mo"), (r"\byear to date\b|\bytd\b|\bthis year\b", "1y"), (r"\bfive years?\b", "5y")):
        if re.search(pat, t):
            return r
    return None


def route(text):
    """-> {"seat","intent",...} (validated) or None when the words are not about the desk. Trading and advice requests come back as refusals with NO action."""
    raw = re.sub(r"\s+", " ", (text or "").strip(" .?!"))
    t = raw.lower()
    if not t or re.search(r"\b(?:on the map|directions|near me|nearest|where is|send it|to my (?:mac|phone))\b", t):
        return None
    if ADVICE.search(t) and (MARKETY.search(raw) or TICKER.search(raw) or MARKET_WORDS.search(t)):
        return validate_route({"intent": "refuse_advice"})
    if TRADE.search(t) and (MARKETY.search(raw) or TICKER.search(raw) or re.search(r"\bsend (?:money|cash)\b", t)):
        return validate_route({"intent": "refuse_trade"})
    sym = None
    for name in sorted(NAMES, key=len, reverse=True):                       # a known company or index named anywhere in the sentence
        if re.search(r"(?<![a-z])" + re.escape(name) + r"(?![a-z])", t):
            sym = name
            break
    patterns = [
        ("open_desk", r"(?:open|show|bring up|pull up|start) (?:the )?(?:markets?|desk|trading desk|markets? desk)|^markets?$|how (?:are|is) the markets?(?: doing| today)?|what(?:'s| is) the market doing"),
        ("briefing", r"(?:market|morning market) (?:briefing|brief|summary)|brief me on the markets?|markets? briefing"),
        ("session", r"is the (?:stock )?market open|when does the market (?:open|close)|market hours|is wall street open"),
        ("movers", r"what(?:'s| is) (?:hot|moving|trending)(?: today| now)?|(?:top )?(?:gainers|losers|movers)|what(?:'s| is) moving the market|most active"),
        ("macro", r"what(?:'s| is) the fed doing|(?:fed|interest) rates?|inflation|jobs (?:report|number)|economic calendar|what(?:'s| is) (?:on|coming up) (?:the )?(?:calendar|this week)|central bank|fomc|cpi"),
        ("levels", r"mark (?:the )?(?:support|resistance|levels|key levels|52[- ]week (?:high|low))|(?:support and resistance)|show (?:the )?levels"),
        ("indicator", r"add (?:the )?(\d+)[- ]day (?:moving )?average|(\d+)[- ]day (?:moving )?average|add volume|show volume|moving average"),
        ("chart_type", r"(?:switch to |show |make it )?(?:a )?(line|candle(?:stick)?s?|candles) (?:chart)?$|switch to (?:a )?(?:line|candle(?:stick)?)"),
        ("compare", r"compare (.+?) (?:and|with|to|versus|vs\.?) (.+)"),
        ("zoom", r"zoom (in|out)"),
        ("back", r"^go back$|^back$|previous (?:chart|symbol)"),
        ("watch_add", r"add (.+?) to (?:my )?watch ?list"),
        ("watch_remove", r"(?:remove|take) (.+?) (?:from|off) (?:my )?watch ?list|remove (.+?) from the watch ?list"),
        ("watch_list", r"what(?:'s| is) on my watch ?list|(?:show|list|read) (?:me )?(?:my )?watch ?list"),
        ("alert_add", r"tell me (?:if|when) (.+?) (?:moves?|drops?|falls?|rises?|jumps?|reports?|changes?)(?: by)? ?(\d+(?:\.\d+)?)? ?(?:percent|%)?(.*)"),
        ("forecast", r"(?:forecast|projection|where could|statistical (?:range|view)|volatility range|how far could)"),
        ("flow", r"(?:insider|congress(?:ional)?|senators?|politicians?|big funds?|hedge funds?) (?:trades?|buying|selling|filings?|disclosures?)|(?:public )?(?:disclosures|filings) for"),
        ("risk", r"what could go wrong|risks? (?:for|of|with)|how risky|is it risky|what are the risks"),
        ("ledger", r"what (?:did|have) you (?:tell|told|say|said)|(?:show|open) (?:the )?(?:ledger|journal|history of (?:the )?desk)|desk journal"),
        ("health", r"(?:data|feed) (?:health|status)|are the feeds (?:up|ok|working)|how are the data feeds"),
        ("sources", r"(?:show |what are )?(?:the )?sources|where did that (?:come|number)|how do you know"),
        ("close", r"close (?:the )?(?:desk|markets?)|hide (?:the )?(?:desk|markets?)"),
        ("analyst", r"what do you think (?:of|about) (.+)|tell me about (.+?)(?: stock| company)?$|what (?:does|is) (.+?) (?:do|company)|how is (.+?) doing|(?:analy[sz]e|explain) (.+)"),
        ("pull_up", r"(?:pull up|show me|show|open|chart|bring up|look at|go to|switch to) (.+)"),
    ]
    for intent, pat in patterns:
        m = re.search(pat, t)
        if not m:
            continue
        r = {"intent": intent}
        if intent in ("open_desk", "briefing", "session", "movers", "macro", "ledger", "health", "sources", "close", "back"):
            if intent == "macro" and not (MARKET_WORDS.search(t) or re.search(r"\b(fed|inflation|jobs|calendar|fomc|cpi|rates?)\b", t)):
                continue
            if intent == "movers" and "market" not in t and not re.search(r"\b(hot|moving|trending|gainers|losers|movers|active)\b", t):
                continue
            return validate_route(r)
        if intent == "zoom":
            r["dir"] = m.group(1)
        elif intent == "chart_type":
            r["type"] = "line" if "line" in m.group(0) else "candle"
        elif intent == "indicator":
            mm = re.search(r"(\d+)[- ]day", t)
            r["ind"] = f"ma{mm.group(1)}" if mm else ("volume" if "volume" in t else "ma50")
            r["symbol_name"] = sym
        elif intent == "levels":
            r["symbol_name"] = sym
        elif intent == "compare":
            r["a"], r["b"] = m.group(1), m.group(2)
        elif intent in ("watch_add", "watch_remove"):
            r["name"] = next(g for g in m.groups() if g)
        elif intent == "watch_list":
            pass
        elif intent == "alert_add":
            r["name"] = m.group(1)
            r["pct"] = float(m.group(2)) if m.group(2) else None
            r["earnings"] = bool(re.search(r"report|earnings", t))
        elif intent in ("forecast", "flow", "risk"):
            r["symbol_name"] = sym or re.sub(r"^(?:.*?\b(?:for|of|on|with|about)\b )", "", t)
        elif intent == "analyst":
            r["name"] = next((g for g in m.groups() if g), sym or "")
            if not sym and not MARKET_WORDS.search(t) and r["name"].strip() not in NAMES and not (len(r["name"].split()) <= 2 and r["name"].strip() in NAMES):
                continue
        elif intent == "pull_up":
            who = m.group(1)
            if not sym and not re.search(r"\b(?:chart|stock|ticker|shares)\b", t) and not MARKET_WORDS.search(t):
                continue
            r["name"] = sym or who
            r["range"] = _range_of(t)
        r.setdefault("range", _range_of(t))
        if sym and "name" not in r:
            r["symbol_name"] = sym
        return validate_route(r)
    if sym and (MARKET_WORDS.search(t) or re.search(r"\b(?:price|trading|stock|shares?|chart|how much)\b", t)):
        return validate_route({"intent": "pull_up", "name": sym, "range": _range_of(t)})
    return None


def validate_route(r):
    """Clamp a route to the allowlist: unknown intent -> None; the seat is chosen by CODE from the intent (never taken from the words); ranges and symbols are checked."""
    if not isinstance(r, dict) or r.get("intent") not in INTENTS:
        return None
    out = {"intent": r["intent"], "seat": SEAT_OF[r["intent"]]}
    if r.get("range") in RANGES:
        out["range"] = r["range"]
    for k in ("name", "symbol_name", "a", "b", "ind", "dir", "type"):
        if r.get(k):
            out[k] = re.sub(r"[^A-Za-z0-9&.^= '-]", "", str(r[k]))[:40].strip()
    if r.get("ind") and not re.fullmatch(r"ma\d{1,3}|volume", out.get("ind", "")):
        out.pop("ind", None)
    if r.get("pct") is not None:
        try:
            out["pct"] = max(0.5, min(50.0, float(r["pct"])))
        except (TypeError, ValueError):
            pass
    if r.get("earnings"):
        out["earnings"] = True
    return out


REFUSE_TRADE = "I only read markets. I don't buy, sell or move money, and I can't place or prepare trades."
REFUSE_ADVICE = "I can't tell you what to buy or sell. I can explain the context, like where the price sits and how volatile it is. " + NOTADVICE


# ----------------------------------------------------------------------------- the watchlist, alerts, the journal
def watchlist():
    w = settings.get("watchlist")
    return [s for s in (w if isinstance(w, list) else []) if isinstance(s, str)][:30]


def watch_add(sym):
    w = watchlist()
    if sym not in w:
        w.append(sym)
    settings.update(watchlist=w[:30])
    return w


def watch_remove(sym):
    w = [s for s in watchlist() if s != sym]
    settings.update(watchlist=w)
    return w


def _jload(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def alerts():
    a = _jload(ALERTS, [])
    return a if isinstance(a, list) else []


def alert_add(sym, pct=None, earnings=False):
    a = [x for x in alerts() if not (x["sym"] == sym and x["kind"] == ("earnings" if earnings else "move"))]
    a.append({"sym": sym, "kind": "earnings" if earnings else "move", "pct": pct or 5.0, "fired": ""})
    ALERTS.parent.mkdir(exist_ok=True)
    ALERTS.write_text(json.dumps(a[-20:]))
    return a


def check_alerts():
    """Called once a minute: -> [text] for move alerts that crossed their size today (each fires once a day). Alerts only notify; they never act."""
    out, a, today = [], alerts(), date.today().isoformat()
    changed = False
    for x in a:
        if x["kind"] != "move" or x.get("fired") == today:
            continue
        q = quote(x["sym"])
        pct = ((q or {}).get("value") or {}).get("pct")
        if pct is not None and abs(pct) >= x["pct"]:
            out.append(f"{x['sym']} is {'up' if pct > 0 else 'down'} {abs(pct):.1f} percent today.")
            x["fired"] = today
            changed = True
    if changed:
        ALERTS.write_text(json.dumps(a))
    return out


def ledger_add(question, seat, symbol, quoted):
    """One line per answer: when, what was asked, which seat, and the numbers it quoted with where they came from. Kept in data/ (outside git)."""
    try:
        LEDGER.parent.mkdir(exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "q": question[:160], "seat": seat, "symbol": symbol, "quoted": quoted}, ensure_ascii=False) + "\n")
        lines = LEDGER.read_text(encoding="utf-8").splitlines()
        if len(lines) > 400:
            LEDGER.write_text("\n".join(lines[-300:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def ledger_tail(n=8):
    try:
        return [json.loads(x) for x in LEDGER.read_text(encoding="utf-8").splitlines()[-n:]]
    except (OSError, ValueError):
        return []


# ----------------------------------------------------------------------------- the economic calendar (an editable local file: "manual")
DEFAULT_ECON = [{"date": "2026-12-09", "what": "Federal Reserve rate decision (FOMC meeting ends)"}, {"date": "2027-01-27", "what": "FOMC meeting begins"}, {"date": "2027-01-28", "what": "Federal Reserve rate decision (FOMC meeting ends)"},
                {"date": "2027-03-17", "what": "FOMC meeting begins"}, {"date": "2027-03-18", "what": "Federal Reserve rate decision (FOMC meeting ends)"}]


def econ_upcoming(n=3):
    """Next calendar entries from data/econ_calendar.json (edit it to add dates). Marked manual: it is not a live feed. The monthly jobs report is usually the first Friday."""
    items = _jload(ECON, None)
    if items is None:
        ECON.parent.mkdir(exist_ok=True)
        ECON.write_text(json.dumps(DEFAULT_ECON, indent=1))
        items = DEFAULT_ECON
    today = date.today().isoformat()
    up = sorted((i for i in items if isinstance(i, dict) and str(i.get("date", "")) >= today), key=lambda i: i["date"])[:n]
    d = date.today().replace(day=1)
    while d.weekday() != 4:
        d += timedelta(days=1)
    if d.isoformat() >= today:
        up.append({"date": d.isoformat(), "what": "US jobs report (usually the first Friday of the month; check the date)"})
    return sorted(up, key=lambda i: i["date"])[:n + 1], "manual"
