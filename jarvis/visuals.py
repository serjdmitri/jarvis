"""Pictures for "show me ..." answers, keyless: Wikimedia Commons photos first, then the Wikipedia page image. -> a gallery card.
Only https image URLs from Wikimedia (upload / thumb) are ever put on a card."""
import html
import json
import re
import time
import urllib.parse
import urllib.request

OK_HOSTS = ("https://upload.wikimedia.org/", "https://thumb.wikimedia.org/")
UA = {"User-Agent": "jarvis-local/1.0 (personal voice assistant)"}
_cache = {}                                     # query -> (time, cards): a few minutes, so asking twice is instant


def _get(url, timeout=6):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.load(r)


def _clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _commons(q, n):
    out = []
    j = _get("https://commons.wikimedia.org/w/api.php?action=query&format=json&generator=search&gsrnamespace=6&gsrlimit=50&gsrsearch="
             + urllib.parse.quote(q) + "&prop=imageinfo&iiprop=url|mime|extmetadata&iiurlwidth=900&iiextmetadatafilter=ImageDescription|ObjectName", 6)
    for p in sorted((j.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 99)):
        ii = (p.get("imageinfo") or [{}])[0]
        u = ii.get("thumburl") or ii.get("url")
        if not u or ii.get("mime") not in ("image/jpeg", "image/png") or not u.startswith(OK_HOSTS):
            continue
        md = ii.get("extmetadata") or {}
        cap = _clean((md.get("ObjectName") or {}).get("value") or (md.get("ImageDescription") or {}).get("value") or p.get("title", "")[5:])[:70]
        out.append({"src": u, "caption": re.sub(r"\.(jpe?g|png)$", "", cap, flags=re.I)})
        if len(out) >= n:
            break
    return out


def _wikipedia(q, n):
    out = []
    j = _get("https://en.wikipedia.org/w/api.php?action=query&format=json&generator=search&gsrlimit=10&prop=pageimages&piprop=thumbnail&pilimit=10&pithumbsize=900&gsrsearch=" + urllib.parse.quote(q), 6)
    for p in sorted((j.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 99)):
        u = (p.get("thumbnail") or {}).get("source")
        if u and u.startswith(OK_HOSTS):
            out.append({"src": u, "caption": p.get("title", "")[:70]})
        if len(out) >= n:
            break
    return out


def images(query, n=6):
    """-> [{"src","caption"}] (may be empty). Photos from Commons and the lead pictures of matching Wikipedia pages, fetched in parallel."""
    import concurrent.futures as cf
    q = re.sub(r"\s+", " ", query).strip()[:80]
    hit = _cache.get(q.lower())
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    res = []
    with cf.ThreadPoolExecutor(2) as ex:
        futs = [ex.submit(_wikipedia, q, n), ex.submit(_commons, q, n)]
        for f in futs:
            try:
                res.append(f.result(timeout=8))
            except Exception as e:
                print("[images] source failed:", type(e).__name__, flush=True)
                res.append([])
    out, seen = [], set()
    for src in (res[0][:2], res[1], res[0][2:]):                  # a page picture or two first (they are the clearest), then Commons photos
        for im in src:
            if im["src"] not in seen:
                seen.add(im["src"]); out.append(im)
    out = out[:n]
    _cache[q.lower()] = (time.time(), out)
    return out


def gallery_card(query, n=6):
    ims = images(query, n)
    if not ims:
        return None
    return {"type": "gallery", "title": query[:50], "images": ims, "full": True}
