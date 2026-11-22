"""News, fast and fresh, from free public RSS feeds. A background thread refreshes the topic feeds (world, politics, tech, sports) every 12 minutes and keeps them cached, so "show the news" opens
instantly from memory. Politics mixes sources from different outlets on purpose. Any other topic is a Google News search feed (fetched when you ask, cached 10 minutes). Nothing is invented:
every headline, source, time and picture comes from a feed. Only the first headline is spoken. -> "news" cards."""
import html
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from . import settings

UA = {"User-Agent": "jarvis-local-assistant/0.1 (news reader)"}
REFRESH_S = 12 * 60
FEEDS = {
    "world": [("BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"), ("NPR", "https://feeds.npr.org/1004/rss.xml"), ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
              ("The Guardian", "https://www.theguardian.com/world/rss")],
    "politics": [("NPR", "https://feeds.npr.org/1014/rss.xml"), ("BBC", "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"), ("Fox News", "https://moxie.foxnews.com/google-publisher/politics.xml"),
                 ("The Guardian", "https://www.theguardian.com/us-news/us-politics/rss"), ("The Hill", "https://thehill.com/rss/syndicator/19109")],
    "tech": [("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"), ("The Verge", "https://www.theverge.com/rss/index.xml"), ("BBC", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
             ("Hacker News", "https://hnrss.org/frontpage")],
    "sports": [("BBC Sport", "https://feeds.bbci.co.uk/sport/rss.xml"), ("ESPN", "https://www.espn.com/espn/rss/news"), ("NPR", "https://feeds.npr.org/1055/rss.xml")],
}
TOPIC_WORDS = {"world": "world", "international": "world", "global": "world", "politics": "politics", "political": "politics", "tech": "tech", "technology": "tech", "science": "tech",
               "sports": "sports", "sport": "sports", "football": "sports", "basketball": "sports", "soccer": "sports"}
_data = {}                                        # topic -> {"t": fetched_at, "items": [...]}
_lock = threading.Lock()
LAST = {"items": [], "t": 0.0}                    # what the card shows now: "open that one" / "the second one" refers to this


def _text(e, *names):
    for n in names:
        x = e.find(n)
        if x is not None and (x.text or "").strip():
            return html.unescape(re.sub(r"<[^>]+>", " ", x.text)).strip()
    return ""


def _image(it):
    for tag in ("{http://search.yahoo.com/mrss/}thumbnail", "{http://search.yahoo.com/mrss/}content"):
        for x in it.findall(tag):
            u = x.get("url") or ""
            if u.startswith("https://") and (x.get("medium") in (None, "image") or re.search(r"\.(?:jpe?g|png|webp)", u, re.I)):
                return u
    for x in it.findall("enclosure"):
        if (x.get("type") or "").startswith("image/") and (x.get("url") or "").startswith("https://"):
            return x.get("url")
    m = re.search(r'<img[^>]+src=["\'](https://[^"\']+)', html.unescape(_text_raw(it)))
    return m.group(1) if m else ""


def _text_raw(it):
    for n in ("description", "{http://purl.org/rss/1.0/modules/content/}encoded"):
        x = it.find(n)
        if x is not None and x.text:
            return x.text
    return ""


def _when(it):
    for n in ("pubDate", "{http://purl.org/dc/elements/1.1/}date", "published", "updated"):
        v = _text(it, n)
        if v:
            try:
                return parsedate_to_datetime(v).timestamp()
            except Exception:
                try:
                    from datetime import datetime
                    return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
                except Exception:
                    pass
    return 0.0


def parse(xml_bytes, source, limit=8):
    """RSS or Atom bytes -> [{"title","link","source","t","image"}]. Garbage in gives an empty list."""
    try:
        root = ET.fromstring(xml_bytes[:1_500_000])
    except ET.ParseError:
        return []
    out = []
    for it in root.iter():
        tag = it.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        title = _text(it, "title", "{http://www.w3.org/2005/Atom}title")
        link = _text(it, "link")
        if not link:
            for l in it.findall("{http://www.w3.org/2005/Atom}link"):
                link = l.get("href") or link
        if not title or not link.startswith("https://"):
            continue
        out.append({"title": re.sub(r"\s+", " ", title)[:200], "link": link, "source": source, "t": _when(it), "image": _image(it)})
        if len(out) >= limit:
            break
    return out


def _fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=6) as r:
        return r.read(1_500_000)


def refresh(topic, feeds=None):
    items = []
    for src, url in (feeds or FEEDS.get(topic, [])):
        try:
            items += parse(_fetch(url), src)
        except Exception:
            continue
    # mix the sources (a round robin), newest first inside each, so no single outlet fills the list
    by = {}
    for it in sorted(items, key=lambda x: -x["t"]):
        by.setdefault(it["source"], []).append(it)
    mixed, i = [], 0
    while any(by.values()) and len(mixed) < 24:
        for k in list(by):
            if by[k]:
                mixed.append(by[k].pop(0))
        i += 1
    seen, out = set(), []
    for it in mixed:
        key = re.sub(r"\W+", "", it["title"].lower())[:50]
        if key not in seen:
            seen.add(key); out.append(it)
    if out:
        with _lock:
            _data[topic] = {"t": time.time(), "items": out}
    return out


def search_feed(topic):
    q = urllib.parse.quote(topic[:60])
    return [("Google News", f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en")]


def get(topic):
    """-> (items, fetched_at). Cached topics come back at once; an unknown topic is fetched now (cached 10 minutes)."""
    topic = (topic or "world").lower().strip()
    with _lock:
        hit = _data.get(topic)
    if hit and (time.time() - hit["t"] < 3 * REFRESH_S or topic in FEEDS):
        return hit["items"], hit["t"]
    items = refresh(topic, FEEDS.get(topic) or search_feed(topic))
    with _lock:
        hit = _data.get(topic)
    return (items, hit["t"]) if hit else ([], 0.0)


def ago(t):
    if not t:
        return ""
    s = max(0, int(time.time() - t))
    return "just now" if s < 90 else f"{s // 60} min ago" if s < 3600 else f"{s // 3600} h ago" if s < 86400 else f"{s // 86400} d ago"


def card(topic, n=8):
    items, fetched = get(topic)
    if not items:
        return None
    shown = items[:n]
    LAST.update(items=shown, t=time.time())
    return {"type": "news", "wide": 420, "small": True, "title": f"News · {topic.title()}", "topic": topic, "updated": time.strftime("%H:%M", time.localtime(fetched)),
            "items": [{"title": it["title"], "source": it["source"], "ago": ago(it["t"]), "image": it["image"], "link": it["link"]} for it in shown]}


def prefetch_loop(stop=lambda: False):
    """Background: warm the four topics, then every 12 minutes (and your own topics in settings "news_topics"). One request at a time, cheap."""
    time.sleep(20)                                           # after start-up has settled
    while not stop():
        for topic in list(FEEDS) + [t for t in (settings.get("news_topics") or []) if isinstance(t, str)][:6]:
            try:
                refresh(topic, FEEDS.get(topic) or search_feed(topic))
            except Exception:
                pass
            time.sleep(1.5)
        for _ in range(REFRESH_S):
            if stop():
                return
            time.sleep(1)


def start():
    threading.Thread(target=prefetch_loop, daemon=True).start()


# ----------------------------------------------------------------------------- what you say
def parse_command(text):
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    m = re.fullmatch(r"(?:please )?(?:(?:show|open|give|read|get|bring up|pull up|tell|find|what(?:'s| is| are)|any)(?: me)?(?: the| my| some)?)?(?: (?:latest|top|current|daily|today's|morning|new))*(?: ([a-z]+))?"
                     r" (?:news|headlines)(?: (?:about|on|for|from) (?:the )?(.+))?", " " + low if not low.startswith(("show", "open", "give", "read", "get", "bring", "pull", "tell", "find", "what", "any", "please")) else low)
    if not m:
        return None
    if re.fullmatch(r"(?:good|bad|big|great|sad|old|some) news", low):
        return None                                          # an exclamation, not a request
    kind, about = (m.group(1) or "").strip(), (m.group(2) or "").strip()
    if kind in ("the", "my", "some", "latest", "top", "current", "me", "all", "daily", "morning", "any", "good", "bad", "fake", "breaking", "local", "today", "new"):
        kind = "" if kind != "local" else kind
    topic = about or TOPIC_WORDS.get(kind) or kind or "world"
    if topic in ("today", "now", "tonight", "local"):
        topic = "world"
    return {"kind": "show", "topic": TOPIC_WORDS.get(topic, topic)[:60]}


ORD = {"first": 0, "1st": 0, "one": 0, "second": 1, "2nd": 1, "two": 1, "third": 2, "3rd": 2, "three": 2, "fourth": 3, "four": 3, "fifth": 4, "five": 4}


def open_command(text):
    """"open that one" / "open the second story" while a news card is on screen -> the item, or None."""
    if time.time() - LAST["t"] > 900 or not LAST["items"]:
        return None
    low = text.lower().strip(" .?!")
    m = re.fullmatch(r"(?:please )?(?:open|show|read|go to)(?: me)?(?: the)? (?:(first|second|third|fourth|fifth|1st|2nd|3rd|top|latest|that|this)(?: one| story| article| headline)?|(?:number |story |article )(\d))(?: one| story| article| headline)?", low)
    if not m:
        return None
    w = m.group(1)
    i = int(m.group(2)) - 1 if m.group(2) else 0 if w in ("that", "this", "top", "latest") else ORD.get(w, 0)
    return LAST["items"][i] if 0 <= i < len(LAST["items"]) else None
