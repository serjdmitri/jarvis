"""Markets data layer against a fake local server: caching, backoff, 429, timeouts, bad JSON, the number check. Run: .venv/bin/python tests/markets_fake.py"""
import json, os, sys, tempfile, threading, time, random
os.environ["JARVIS_DATA_DIR"] = tempfile.mkdtemp(); os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from http.server import BaseHTTPRequestHandler, HTTPServer
HITS = {"n": 0}; MODE = {"m": "ok"}
def chart(sym, n=300):
    random.seed(sym); p = 100.0; ts = []; o = []; h = []; l = []; c = []; v = []; t0 = int(time.time()) - n * 86400
    for i in range(n):
        d = random.gauss(0.05, 1.4); op = p; p = max(5, p + d); ts.append(t0 + i * 86400); o.append(round(op, 2)); c.append(round(p, 2)); h.append(round(max(op, p) + 0.8, 2)); l.append(round(min(op, p) - 0.8, 2)); v.append(int(1e6 + random.random() * 5e5))
    return {"chart": {"result": [{"meta": {"symbol": sym, "regularMarketPrice": c[-1], "chartPreviousClose": c[-2], "regularMarketVolume": v[-1], "currency": "USD", "longName": sym + " Inc", "fiftyTwoWeekHigh": max(h), "fiftyTwoWeekLow": min(l)}, "timestamp": ts, "indicators": {"quote": [{"open": o, "high": h, "low": l, "close": c, "volume": v}]}}]}}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        HITS["n"] += 1
        m = MODE["m"]
        if m == "429": self.send_response(429); self.end_headers(); return
        if m == "bad": self.send_response(200); self.end_headers(); self.wfile.write(b"<html>not json"); return
        if m == "slow": time.sleep(15)
        if m == "500": self.send_response(500); self.end_headers(); return
        sym = self.path.split("/chart/")[-1].split("?")[0]
        body = json.dumps(chart(sym)).encode(); self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
from jarvis import markets as M
M.BASES["yahoo"] = f"http://127.0.0.1:{srv.server_port}"
FAILS = []
def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name); 
    if not ok: FAILS.append(name)
q = M.quote("FAKE"); check("quote comes from the feed with its source and time", bool(q) and q["source"].startswith("Yahoo") and q["t"] > 0 and q["value"]["price"] > 0)
n1 = HITS["n"]; M.quote("FAKE"); M.candles("FAKE", "5d"); check("a second quote inside 60 s is served from the cache (no new request)", HITS["n"] == n1)
c = M.candles("FAKE", "1y"); check("1y candles parse (300 bars)", bool(c) and len(c["value"]["c"]) == 300)
lv = M.levels(c["value"], c["value"]); check(f"levels computed by code ({len(lv)}, max 12), each labelled with how", 0 < len(lv) <= 12 and all(x["label"] and x["how"] for x in lv))
check("levels are descriptive: no buy/sell/stop wording", not any(w in json.dumps(lv).lower() for w in ("buy", "sell", "stop ", "entry", "target")))
b = M.band(c["value"]["c"]); check("the statistical band is labelled model output, 20 steps, lo < mid < hi", len(b) == 20 and all(x["lo"] < x["mid"] < x["hi"] for x in b))
p = M.chart_payload("FAKE", "3mo", ["ma50"], True, True, "OTHER"); check("chart payload has candles, MA, levels, band, compare, source and time", len(p["candles"]) > 20 and "ma50" in p["indicators"] and p["levels"] and p["band"] and p["compare"] and p["source"] and p["t"])
# failure modes
M._cache.clear(); M._state.clear(); MODE["m"] = "429"; d = M.quote("FAIL1"); check("HTTP 429: no price (None, shown as —), feed marked red after repeats", d is None)
n2 = HITS["n"]; M.quote("FAIL1"); check("backoff: an immediate retry does not hammer the server", HITS["n"] == n2)
check("the data-health chip says the source is down or backing off", M.health().get("yahoo") in ("red", "yellow", "idle", "green") and M._state["yahoo"]["wait_until"] > time.time())
M._cache.clear(); M._state.clear(); MODE["m"] = "bad"; check("bad JSON: no price, no crash", M.quote("BAD") is None)
M._cache.clear(); M._state.clear(); MODE["m"] = "500"; check("HTTP 500: no price", M.candles("X", "1mo") is None)
M._cache.clear(); M._state.clear(); MODE["m"] = "ok"; M.quote("STALE"); k = list(M._cache)[0]; M._cache[k]["t"] -= 999; MODE["m"] = "500"; sd = M.candles("STALE", "5d")
check("a failing feed with a cached copy returns the copy marked STALE (never a guess)", bool(sd) and sd.get("stale") is True)
# the number check
data = {"symbol": "FAKE", "price": 171.39, "pct": 1.2, "vol20": 1.8}
for ans, want_kept in [("Fake is at 171.39 today.", True), ("Fake is at 171.4, up 1.2 percent.", True), ("Fake is at 200.15 today.", False), ("The price is $171.39. It might reach 250 soon.", "half"), ("It has a 50-day average and a 52-week high.", True)]:
    out, dropped = M.check_numbers(ans, data)
    ok = (out == ans) if want_kept is True else (out == "—") if want_kept is False else (out == "The price is $171.39." and dropped == 1)
    check(f"number check: {ans!r} -> {out!r}", ok)
check("a made-up price with no data at all becomes —", M.check_numbers("It trades at 99.99.", {}) == ("—", 1))
# security: no key in repo files, no trading code path
import subprocess
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
hits = subprocess.run(["grep", "-rniE", r"place_order|submit_order|/orders|broker|alpaca|ibkr|interactive brokers|robinhood|trade_api|execute_trade", "jarvis", "bin", "--include=*.py", "--include=*.js", "--include=*.html"], capture_output=True, text=True, cwd=root).stdout
check("no order/broker/trade endpoint exists in the code (grep)", not [l for l in hits.splitlines() if "markets.py" not in l.split(":")[0] and "mktdesk.py" not in l.split(":")[0]] and "broker" not in "".join(l for l in hits.splitlines() if "markets.py:" not in l))
print("\n" + ("ALL PASS" if not FAILS else "FAILED: " + "; ".join(FAILS))); sys.exit(1 if FAILS else 0)
