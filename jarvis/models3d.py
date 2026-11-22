"""Real 3D models from free, licensed sources: look one up, download it ONLY with your approval, cache it, and show it with its credit line.

Sources (no account or key needed): Wikimedia Commons (STL files; the licence of every file is in its metadata). Built and tested here.
Researched, not built: NASA 3D Resources (GLB, mostly public domain; no search API, the catalogue is a web page, so models would be added by link), NIH 3D (organs, molecules; needs
its own search page, no stable key-free API found), Smithsonian 3D (3d.si.edu answered 403 to scripts; the open-access API needs a free key), Sketchfab (downloads need an account token).
Their hosts are on the allowlist below so a link to a model on them works once you approve it, but nothing searches them.
Rules, always: https only; only the source's own hosts; only glb, gltf (single file) and stl; 15 MB at most; a triangle cap; a timeout; redirects only to allowlisted hosts; the file is never
executed (the page parses it with three.js loaders); a licence that is not clearly free means the model is not shown; every real model shows title, creator, licence and link;
the cache (~/jarvis/cache/models, 200 MB at most, in .gitignore) can be cleared; nothing is ever put in the git repo. Models from a photo or a generator are not supported at all."""
import hashlib
import json
import os
import re
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

CACHE = config.ROOT / "cache" / "models"
MAX_BYTES = 15 * 1024 * 1024
MAX_CACHE = 200 * 1024 * 1024
MAX_TRIS = 600_000                      # refuse above this (the page draws up to 100k and shows heavier ones as points)
TIMEOUT = 20
UA = "jarvis-local-assistant/0.1"
HOSTS = {"commons": ("upload.wikimedia.org", "commons.wikimedia.org"), "nasa": ("assets.science.nasa.gov", "science.nasa.gov", "nasa3d.arc.nasa.gov"),
         "nih": ("3d.nih.gov", "3dprint.nih.gov"), "smithsonian": ("3d-api.si.edu", "3d.si.edu", "ids.si.edu")}
FREE = re.compile(r"^(?:cc0|cc[- ]by(?:[- ]sa)?(?:[- ]\d(?:\.\d)?)?|public domain|pd[- ].*|cc[- ]zero)\b", re.I)
EXTS = {".glb": "glb", ".gltf": "glb", ".stl": "stl"}
NAME = re.compile(r"^[a-f0-9]{16}\.(?:glb|stl)$")


class Refused(Exception):
    """A plain-words reason a model was not used."""


def _hosts():
    return {h for hs in HOSTS.values() for h in hs}


def _req(url, accept=None):
    return urllib.request.Request(url, headers={"User-Agent": UA, **({"Accept": accept} if accept else {})})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def check_url(url, source=None):
    """-> the lower-case host; raises Refused unless it is https on an allowlisted host (of `source` when given) with an allowed file type."""
    p = urllib.parse.urlsplit(url or "")
    host = (p.hostname or "").lower()
    allowed = set(HOSTS.get(source, ())) if source else _hosts()
    if p.scheme != "https" or host not in allowed:
        raise Refused("That model is not on one of the approved sources, so I won't download it.")
    if os.path.splitext(p.path)[1].lower() not in EXTS:
        raise Refused("I only open glb, gltf and stl model files.")
    return host


def triangles(data, kind):
    """Triangle count of a model file's bytes (binary or ascii STL, GLB); 0 when it cannot be told."""
    if kind == "stl":
        if data[:5] == b"solid" and b"facet" in data[:600]:
            return data.count(b"facet normal")
        if len(data) >= 84:
            n = struct.unpack("<I", data[80:84])[0]
            return n if 84 + 50 * n == len(data) else 0
        return 0
    if data[:4] == b"glTF" and len(data) > 20:
        try:
            ln = struct.unpack("<I", data[12:16])[0]
            j = json.loads(data[20:20 + ln].decode("utf-8", "ignore"))
            total = 0
            for m in j.get("meshes", []):
                for pr in m.get("primitives", []):
                    acc = j["accessors"][pr["indices"]] if "indices" in pr else j["accessors"][pr["attributes"]["POSITION"]]
                    total += acc.get("count", 0) // (3 if "indices" in pr else 3)
            return total
        except Exception:
            return 0
    return 0


def check_file(data, kind):
    """Magic bytes, size and triangles; raises Refused with a plain reason."""
    if len(data) > MAX_BYTES:
        raise Refused("That model file is over 15 MB, too big for this computer.")
    if kind == "glb" and data[:4] != b"glTF":
        raise Refused("That file is not a single-file GLB model.")
    if kind == "stl" and not (data[:5] == b"solid" or len(data) >= 84):
        raise Refused("That file is not a valid STL model.")
    n = triangles(data, kind)
    if n == 0:
        raise Refused("That model file has no triangles I can read.")
    if n > MAX_TRIS:
        raise Refused(f"That model has {n // 1000}k triangles: too heavy for this computer.")
    return n


def fetch(url, source=None, max_hops=3):
    """Download one model file safely -> (bytes, kind). Redirects are followed by hand and only to allowlisted hosts."""
    opener = urllib.request.build_opener(_NoRedirect)
    for _ in range(max_hops + 1):
        check_url(url, source)
        try:
            with opener.open(_req(url), timeout=TIMEOUT) as r:
                if r.status != 200:
                    raise Refused("The source did not give me the file.")
                total = int(r.headers.get("Content-Length") or 0)
                if total > MAX_BYTES:
                    raise Refused("That model file is over 15 MB, too big for this computer.")
                data = r.read(MAX_BYTES + 1)
            kind = EXTS[os.path.splitext(urllib.parse.urlsplit(url).path)[1].lower()]
            check_file(data, kind)
            return data, kind
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                url = urllib.parse.urljoin(url, e.headers["Location"])           # checked against the allowlist at the top of the loop
                continue
            raise Refused(f"The source answered {e.code}.")
        except (urllib.error.URLError, OSError, TimeoutError):
            raise Refused("I couldn't reach the model source.")
    raise Refused("Too many redirects.")


# ----------------------------------------------------------------------------- the cache
def cache_size():
    try:
        return sum(f.stat().st_size for f in CACHE.glob("*") if f.is_file())
    except OSError:
        return 0


def _evict():
    files = sorted((f for f in CACHE.glob("*") if f.is_file()), key=lambda f: f.stat().st_mtime)
    total = cache_size()
    for f in files:
        if total <= MAX_CACHE:
            break
        total -= f.stat().st_size
        f.unlink(missing_ok=True)


def store(data, kind, meta):
    """Cache a checked file -> its public name (16 hex + extension). Metadata (credit line) sits beside it."""
    CACHE.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha1(data).hexdigest()[:16] + "." + kind
    (CACHE / name).write_bytes(data)
    (CACHE / (name + ".json")).write_text(json.dumps(meta))
    os.utime(CACHE / name, None)
    _evict()
    return name


def meta_of(name):
    try:
        return json.loads((CACHE / (name + ".json")).read_text())
    except (OSError, ValueError):
        return None


def path_of(name):
    """The cached file for a public name, or None (the name must look exactly like one we made)."""
    return (CACHE / name) if NAME.match(name or "") and (CACHE / name).is_file() else None


def clear_cache():
    n = 0
    for f in CACHE.glob("*"):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    return n


# ----------------------------------------------------------------------------- Wikimedia Commons lookup (no key)
def _strip(v):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", str(v or ""))).strip()


def lookup(subject, limit=6):
    """Real, free-licensed STL models on Wikimedia Commons whose TITLE contains a word of the subject -> [{title, creator, licence, url, page, size, kind, source}] (best first)."""
    words = [w for w in re.findall(r"[a-z0-9]+", subject.lower()) if len(w) > 2 and w not in ("the", "model", "and", "for", "with")]
    if not words:
        return []
    q = urllib.parse.quote("filetype:3d " + " ".join(words[:3]))
    url = ("https://commons.wikimedia.org/w/api.php?action=query&format=json&generator=search&gsrnamespace=6&gsrlimit=15&gsrsearch=" + q +
           "&prop=imageinfo&iiprop=url|size|mime|extmetadata&iiextmetadatafilter=LicenseShortName|Artist|AttributionRequired")
    try:
        with urllib.request.urlopen(_req(url), timeout=TIMEOUT) as r:
            pages = (json.loads(r.read(2_000_000)).get("query") or {}).get("pages") or {}
    except Exception:
        return []
    out = []
    for p in pages.values():
        ii = (p.get("imageinfo") or [{}])[0]
        title = p.get("title", "").removeprefix("File:")
        ext = os.path.splitext(title)[1].lower()
        md = ii.get("extmetadata") or {}
        lic = _strip((md.get("LicenseShortName") or {}).get("value"))
        if ext not in EXTS or not lic or not FREE.match(lic) or not ii.get("url") or (ii.get("size") or 0) > MAX_BYTES or not any(w in title.lower() for w in words):
            continue
        try:
            check_url(ii["url"], "commons")
        except Refused:
            continue
        out.append({"title": os.path.splitext(title)[0][:70], "creator": _strip((md.get("Artist") or {}).get("value"))[:60] or "unknown", "licence": lic, "url": ii["url"], "size": ii.get("size") or 0,
                    "page": "https://commons.wikimedia.org/wiki/File:" + urllib.parse.quote(title.replace(" ", "_")), "kind": EXTS[ext], "source": "commons",
                    "score": sum(w in title.lower() for w in words)})
    out.sort(key=lambda m: (-m["score"], m["size"]))
    return out[:limit]


def credit_line(m):
    return f"Real model: {m['title']}, by {m['creator']}, licence {m['licence']}, source {m['page']}"


def get_real(m):
    """Download (checked) + cache a lookup result -> the cached public name."""
    data, kind = fetch(m["url"], m.get("source"))
    return store(data, kind, {k: m[k] for k in ("title", "creator", "licence", "page", "source", "size")})


def model_card(m, name):
    """The 3D card for a cached real model. Its parts are the file's own named nodes (the page lists them); a one-piece scan stays one piece: pulling it apart is not faked."""
    from . import holo
    spec = {"title": m["title"], "unit": "model units", "real": True, "parts": [{"id": "model", "label": m["title"][:28], "desc": "The whole model, as one piece.", "shape": "box", "pos": [0, 0, 0], "size": [4, 4, 4]}]}
    spec = holo.validate_scene(spec)
    spec["real"] = True
    c = holo.scene_card(spec, source={"kind": "real", "line": credit_line(m), "link": m["page"]})
    c["model"] = {"url": "/api/model/" + name, "kind": m["kind"]}
    return c
