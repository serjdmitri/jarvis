"""Product prices must trace to a fetched page; a made-up price is rejected; affiliate params are stripped; unsafe links dropped. A fake brain and fake store pages: no network.
Run: .venv/bin/python tests/product_test.py"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from jarvis import product
R = []
def rec(n, ok, extra=""): R.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n, extra)
PAGES = {"https://store-a.example/pi4": "Raspberry Pi 4 Model B 4GB  Price: $45.00 In stock", "https://store-b.example/pi4": "Raspberry Pi 4 on sale now for $39.99!", "https://store-c.example/pi4": "Out of stock. Was $55.00",
         "https://store-d.example/big": "Laptop $1,299.00 free shipping", "https://amazon.example/pi4": "Raspberry Pi 4 $41.50"}
fetch = lambda u: PAGES[u.split("?")[0]] if u.split("?")[0] in PAGES else (_ for _ in ()).throw(OSError("down"))
rec("exact price on the page is verified", product.verify_price("https://store-a.example/pi4", "$45.00", fetch))
rec("price with no cents verified", product.verify_price("https://store-a.example/pi4", "$45", fetch))
rec("a MADE-UP price is rejected", not product.verify_price("https://store-a.example/pi4", "$25.00", fetch))
rec("a price that is only a substring ($5.00 in $45.00) is rejected", not product.verify_price("https://store-a.example/pi4", "$5.00", fetch))
rec("price larger with commas", product.verify_price("https://store-d.example/big", "$1,299", fetch))
rec("unreachable page: not verified", not product.verify_price("https://down.example/x", "$1.00", fetch))
rec("not a dollar amount: rejected", product.norm_price("about 20 bucks") is None and product.norm_price("€20") is None)
rec("http link refused", product.clean_url("http://store.example/x") is None)
rec("link with a password refused", product.clean_url("https://u:p@store.example/x") is None)
u = product.clean_url("https://amazon.example/dp/B0?tag=abc-20&linkCode=ur2&ref_=x&utm_source=a&th=1&color=red")
rec("affiliate and tracking params removed, real ones kept", u == "https://amazon.example/dp/B0?color=red", u)
def brain(prompt, web=False, timeout=0):
    return [{"store": "Best Buy", "title": "Pi 4 kit", "price": "$39.99", "url": "https://store-b.example/pi4?tag=evil-20"},
            {"store": "Amazon", "title": "Pi 4 4GB", "price": "$41.50", "url": "https://amazon.example/pi4"},
            {"store": "Maker", "title": "Pi 4", "price": "$25.00", "url": "https://store-a.example/pi4"},          # made up: page says 45.00
            {"store": "Shop C", "title": "Pi 4", "price": None, "url": "https://store-c.example/pi4"},
            {"store": "Evil", "title": "x", "price": "$1.00", "url": "http://evil.example/p"},                     # not https
            {"store": "Dup", "title": "x", "price": "$39.99", "url": "https://store-b.example/pi4"}]               # same page again (tag removed)
rows = product.lookup("raspberry pi 4", fetch=fetch, ask=brain)
by = {r["store"]: r for r in rows}
rec("verified prices kept, with the time", by["Amazon"]["price"] == "$41.50" and by["Best Buy"]["price"] == "$39.99" and by["Amazon"]["asof"], str([(r["store"], r["price"]) for r in rows]))
rec("made-up price shows 'price not found' (None)", by["Maker"]["price"] is None and by["Shop C"]["price"] is None)
rec("http link dropped", "Evil" not in by)
rec("duplicate page dropped after cleaning the tracking param", "Dup" not in by)
rec("verified first, cheapest first", [r["store"] for r in rows][:2] == ["Best Buy", "Amazon"], str([r["store"] for r in rows]))
rec("no affiliate tag in any link", all("tag=" not in r["url"] for r in rows))
c = product.card("raspberry pi 4", rows); txt = " ".join(s["text"] for s in c["steps"])
rec("card says price not found where it could not verify", "price not found" in txt and "$39.99" in txt and "as of" in txt)
rec("only OPEN PAGE buttons (no buy / cart / order)", all(b["label"] == "OPEN PAGE" for s in c["steps"] for b in s["buttons"]))
print(f"{sum(R)}/{len(R)} passed")
