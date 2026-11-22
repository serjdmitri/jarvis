"""A fake Yahoo-style data server for UI tests: python tests/fake_markets_server.py PORT"""
import json, random, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
def chart(sym, rng, n):
    random.seed(sym); p = 100.0 + (hash(sym) % 50); ts = []; o = []; h = []; l = []; c = []; v = []; t0 = int(time.time()) - n * 86400
    for i in range(n):
        d = random.gauss(0.05, 1.4); op = p; p = max(5, p + d); ts.append(t0 + i * 86400); o.append(round(op, 2)); c.append(round(p, 2)); h.append(round(max(op, p) + 0.8, 2)); l.append(round(min(op, p) - 0.8, 2)); v.append(int(1e6 + random.random() * 5e5))
    return {"chart": {"result": [{"meta": {"symbol": sym, "regularMarketPrice": c[-1], "chartPreviousClose": c[-2], "regularMarketVolume": v[-1], "currency": "USD", "longName": sym + " Inc"}, "timestamp": ts, "indicators": {"quote": [{"open": o, "high": h, "low": l, "close": c, "volume": v}]}}]}}
N = {"1d": 78, "5d": 100, "1mo": 22, "3mo": 64, "6mo": 126, "1y": 252, "5y": 260}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        path = self.path
        if "/v8/finance/chart/" in path:
            sym = path.split("/chart/")[-1].split("?")[0]; rng = path.split("range=")[-1].split("&")[0]; body = chart(sym, rng, N.get(rng, 64))
        elif "/v1/finance/search" in path:
            body = {"quotes": [{"symbol": "NVDA", "shortname": "NVIDIA Corporation", "quoteType": "EQUITY"}], "news": [{"title": "Chips rally on strong demand", "providerPublishTime": int(time.time())}]}
        elif "screener" in path:
            body = {"finance": {"result": [{"quotes": [{"symbol": s, "shortName": s + " Inc", "regularMarketChangePercent": 8.5 - i, "regularMarketPrice": 50 + i} for i, s in enumerate(["AAA", "BBB", "CCC"])]}]}}
        else:
            body = {}
        b = json.dumps(body).encode(); self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
