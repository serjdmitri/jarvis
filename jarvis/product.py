"""Product lookup, quietly: where to get something, with prices that come from REAL pages. The brain searches the web (Amazon first; if nothing there, the maker's own site; for tech items Best Buy next;
then whichever store fits best; no store is blocked) and proposes {store, title, price, url}. Code then FETCHES each page itself and keeps a price only if that exact price text is in the page.
A price the page does not show is dropped: the row says "price not found" (never a guess). Prices are US dollars with the store and the time they were read. Affiliate or tracking parts of a link
(tag=, ref=, utm_*, ...) are removed: no affiliate or referral codes, ever. Nothing is bought, ordered, put in a cart or logged in to. A link is only shown or opened (in the Jarvis frame) on request."""
import html
import re
import time
import urllib.parse
import urllib.request

from . import webjson

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) jarvis-local-assistant", "Accept-Language": "en-US,en;q=0.8"}
TRACK = re.compile(r"^(?:tag|linkcode|ascsubtag|ref|ref_|psc|pd_rd_\w+|pf_rd_\w+|utm_\w+|affid|aff_id|affiliate\w*|irclickid|clickid|cjevent|mc_cid|mc_eid|gclid|fbclid|msclkid|source|campaign|th|keywords|qid|sr|crid|sprefix|dib\w*|content-id|smid)$", re.I)
PRICE = re.compile(r"^\$\s?(\d{1,3}(?:,\d{3})*|\d+)(?:\.(\d{2}))?$")


def clean_url(u):
    """https only, no credentials, tracking and affiliate parameters removed, or None."""
    try:
        p = urllib.parse.urlsplit(str(u).strip())
    except ValueError:
        return None
    if p.scheme != "https" or not p.hostname or p.username or p.password or " " in str(u) or len(str(u)) > 600 or not re.fullmatch(r"[A-Za-z0-9.-]+", p.hostname) or "." not in p.hostname:
        return None
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=False) if not TRACK.match(k)]
    return urllib.parse.urlunsplit(("https", p.netloc, p.path, urllib.parse.urlencode(q), ""))


def norm_price(text):
    """"$24.99" / "$ 1,299" -> "24.99" / "1299.00", or None."""
    m = PRICE.fullmatch(str(text or "").strip())
    if not m:
        return None
    return f"{int(m.group(1).replace(',', ''))}.{m.group(2) or '00'}"


def _fetch_text(url, timeout=7):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(1_500_000)
    t = html.unescape(re.sub(r"<(?:script|style)\b.*?</(?:script|style)>", " ", raw.decode("utf-8", "ignore"), flags=re.S | re.I))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))


def verify_price(url, price_text, fetch=_fetch_text):
    """True only if the page at `url` shows exactly `price_text` (as a dollar amount). Any failure is False."""
    want = norm_price(price_text)
    if not want or not clean_url(url):
        return False
    try:
        text = fetch(url)
    except Exception:
        return False
    whole, cents = want.split(".")
    pat = r"\$\s?" + r"{:,}".format(int(whole)).replace(",", ",?") + (r"(?:\." + cents + r")" if cents != "00" else r"(?:\.00)?") + r"(?!\d)"
    return re.search(pat, text) is not None


def lookup(name, fetch=_fetch_text, ask=webjson.ask_json, n=5):
    """-> [{"store","title","url","price" (verified "$24.99" or None),"asof"}], best first. Slow (a web search): run it in a thread."""
    prompt = (f"Find where to buy: {name[:100]}. Use WebSearch, then WebFetch the product pages. Order: Amazon first; if it is not on Amazon, the maker's own site; for tech items Best Buy next; "
              "then whichever stores fit best. No store is off limits. Prices in US dollars. Copy the price EXACTLY as the page shows it; if you did not see it on a page, put null. Never invent a link. "
              'Answer ONE JSON list of up to 5: [{"store":"Amazon","title":"...","price":"$24.99" or null,"url":"https://..."}] and nothing else.')
    res = ask(prompt, web=True, timeout=75)
    out, seen = [], set()
    for r in res if isinstance(res, list) else []:
        if not isinstance(r, dict):
            continue
        url = clean_url(r.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        price = r.get("price") if isinstance(r.get("price"), str) else None
        ok = bool(price) and verify_price(url, price, fetch)
        out.append({"store": re.sub(r"\s+", " ", str(r.get("store") or urllib.parse.urlsplit(url).hostname))[:30], "title": re.sub(r"\s+", " ", str(r.get("title") or ""))[:90], "url": url,
                    "price": ("$" + norm_price(price)) if ok else None, "asof": time.strftime("%-I:%M%p").lower() if ok else ""})
        if len(out) >= n:
            break
    out.sort(key=lambda x: (x["price"] is None, float(x["price"][1:]) if x["price"] else 0))      # verified prices first, cheapest first
    return out


def card(name, rows):
    steps = []
    for i, r in enumerate(rows, 1):
        price = f"{r['price']}  (as of {r['asof']})" if r["price"] else "price not found"
        steps.append({"n": i, "text": f"{r['store'].upper()}  ·  {price}" + (f"\n{r['title']}" if r["title"] else ""), "buttons": [{"label": "OPEN PAGE", "action": "open_link", "payload": {"url": r["url"]}}]})
    return {"type": "guide", "gid": "product", "wide": 400, "small": True, "title": "WHERE TO GET · " + name.upper()[:24], "paged": False,
            "status": "prices come from the store pages; nothing is bought", "steps": steps or [{"n": 1, "text": "I found no store pages for that."}]}


def parse_command(text, last_object=""):
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    if re.fullmatch(r"(?:please )?(?:where can i (?:get|buy|find) (?:one|that|this|it)|(?:find|get) me (?:another|one) (?:one)?|find me another one|where (?:do|could|can) i (?:get|buy) (?:one|that|this)|how much (?:is|does) (?:that|this|it)(?: cost)?|is there a cheaper one|what(?:'s| is) a similar one)", low):
        return {"kind": "last", "cheaper": "cheaper" in low, "price": "how much" in low}
    m = re.fullmatch(r"(?:please )?(?:where can i (?:get|buy|find)|find (?:me )?(?:a |an |the )?(?:place|store) (?:to buy|for)|how much (?:is|does|for)|price of|what(?:'s| is) the price of|show me prices for) (?:a |an |the )?(.{3,80})", low)
    if m and not re.search(r"\b(?:a place|home|my|here)\b", m.group(1)) and not re.fullmatch(r"(?:one|that|this|it)", m.group(1)):
        return {"kind": "name", "name": m.group(1)}
    return None
