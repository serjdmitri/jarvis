"""Jarvis memory vault: a folder of Markdown files (Obsidian reads it; Obsidian does not need to be running). Standard library only. Used by jarvis_helper.py (POST /vault?op=...).
Default folder ~/Documents/Jarvis Brain (JARVIS_VAULT overrides; no existing vault of yours is touched). Jarvis writes ONLY in Memory/, Daily/, Projects/, Inbox/ and Trash/.
It reads other folders only when you list them in ~/.jarvis-helper/vault.json ("read_roots": [...]), read-only, and never anything inside a folder called Private.
Never stored: passwords, tokens, keys, codes, card or ID numbers, serials (refused). Sensitive topics (health, money, other people's private details) need a yes first.
"Forget" moves the note to Trash with an undo; nothing is ever hard-deleted by Jarvis."""
import datetime, json, os, re, shutil, time

HOME = os.path.expanduser("~")
ROOT = os.environ.get("JARVIS_VAULT", os.path.join(HOME, "Documents", "Jarvis Brain"))
CFG = os.environ.get("JARVIS_VAULT_CFG", os.path.join(HOME, ".jarvis-helper", "vault.json"))
FOLDERS = {"memory": "Memory", "daily": "Daily", "project": "Projects", "inbox": "Inbox"}
WRITABLE = ("Memory", "Daily", "Projects", "Inbox", "Trash")
MAX_NOTE = 20000
LINK = re.compile(r"\[\[([^\]\|#]+)(?:#[^\]\|]*)?(?:\|[^\]]*)?\]\]")

SECRET_WORDS = re.compile(r"\b(pass(?:word|code|phrase)|passwd|pin(?: number| code)?|cvv|cvc|security code|card number|credit card|debit card|iban|account number|routing number|ssn|social security|"
                          r"api[ -]?key|secret|token|private key|seed phrase|recovery (?:code|phrase)|otp|2fa|verification code|pairing code|serial(?: number)?|licen[cs]e (?:key|number)|passport number|driver'?s licen[cs]e)\b", re.I)
SENSITIVE = [(re.compile(r"\b(diagnos\w*|medication|prescription|therapy|therapist|cancer|diabet\w*|depress\w*|anxiety|surgery|pregnan\w*|blood pressure|my doctor)\b", re.I), "health"),
             (re.compile(r"\b(salary|debt|bank balance|loan|mortgage|net worth|savings|owe[sd]? (?:him|her|them)|credit score|tax return|income)\b", re.I), "money"),
             (re.compile(r"\b(?:my )?(?:wife|husband|girlfriend|boyfriend|friend|boss|mum|mom|dad|brother|sister|neighbou?r|colleague)\b.*\b(cheat\w*|affair|divorc\w*|arrest\w*|sick|ill|fired|pregnan\w*|secret|drunk|addict\w*|lawsuit)\b", re.I), "someone else's private details")]


def looks_secret(text):
    t = text or ""
    if SECRET_WORDS.search(t):
        return True
    if re.search(r"\d{13,19}", re.sub(r"[ -]", "", t)):
        return True
    if re.search(r"\b\d{3}-\d{2}-\d{4}\b", t):
        return True
    if re.search(r"\b(?:sk|pk|ghp|gho|xox[bp]|AKIA|hf_|ya29)[A-Za-z0-9_\-]{8,}", t):
        return True
    if re.search(r"\b(?=[A-Za-z0-9_\-+/=]*\d)(?=[A-Za-z0-9_\-+/=]*[A-Za-z])[A-Za-z0-9_\-+/=]{24,}\b", t):
        return True
    return bool(re.search(r"\b\d{4,8}\b", t) and re.search(r"\b(code|pin)\b", t, re.I))


def sensitive_reason(text):
    for rx, why in SENSITIVE:
        if rx.search(text or ""):
            return why
    return None


def slug(s):
    s = re.sub(r"[^\w .'-]", "", (s or "").strip(), flags=re.U).strip(" .-'")
    return re.sub(r"\s+", " ", s)[:60] or "note"


def ensure():
    for f in WRITABLE:
        os.makedirs(os.path.join(ROOT, f), exist_ok=True)
    mk = os.path.join(ROOT, ".jarvis-vault")
    if not os.path.exists(mk):
        open(mk, "w").write("Jarvis memory vault. Notes are plain Markdown; open this folder in Obsidian to see the graph.\n")


def _cfg():
    try:
        return json.load(open(CFG))
    except (OSError, ValueError):
        return {}


def _private(path):
    return any(p.lower() == "private" for p in os.path.normpath(path).split(os.sep))


def _inside(path, root):
    r = os.path.realpath(root)
    p = os.path.realpath(path)
    return p == r or p.startswith(r + os.sep)


def _roots():
    """[(label, abs root, writable)]"""
    out = [("vault", ROOT, True)]
    for i, r in enumerate(_cfg().get("read_roots") or []):
        r = os.path.expanduser(str(r))
        if os.path.isdir(r) and not _private(r) and not _inside(r, ROOT):
            out.append((f"ext{i}", r, False))
    return out


def _walk(folders_only=True):
    """-> [(label, abs path, rel path)] of .md notes the Go may see."""
    res = []
    for label, root, w in _roots():
        if w:
            subs = [os.path.join(root, f) for f in ("Memory", "Daily", "Projects", "Inbox")]
        else:
            subs = [root]
        for sub in subs:
            for dp, dn, fn in os.walk(sub):
                dn[:] = [d for d in dn if d.lower() != "private" and not d.startswith(".")]
                for f in fn:
                    if f.endswith(".md") and not f.startswith("."):
                        ap = os.path.join(dp, f)
                        res.append((label, ap, os.path.relpath(ap, root)))
    return res


def _title(path):
    return os.path.splitext(os.path.basename(path))[0]


def _fm_split(text):
    if text.startswith("---\n"):
        e = text.find("\n---", 4)
        if e > 0:
            return text[4:e], text[e + 4:].lstrip("\n")
    return "", text


def _stamp():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def append(p):
    """p: {"kind": memory|daily|project|inbox, "title", "text", "tags": [], "links": [], "source", "confidence", "confirmed"} -> {"ok", "path"} | {"ok": False, "why" | "needs_confirm"}."""
    text = (p.get("text") or "").strip()
    if not text:
        return {"ok": False, "why": "nothing to save"}
    if looks_secret(text) or looks_secret(p.get("title", "")):
        return {"ok": False, "why": "That looks like a password, key, code or ID number. I never store those."}
    why = sensitive_reason(text)
    if why and not p.get("confirmed"):
        return {"ok": False, "needs_confirm": why, "why": f"That is about {why}."}
    kind = p.get("kind", "memory")
    if kind not in FOLDERS:
        return {"ok": False, "why": "unknown kind"}
    ensure()
    text = text[:2000]
    tags = [re.sub(r"[^\w-]", "", t) for t in (p.get("tags") or [])][:8]
    links = [slug(l) for l in (p.get("links") or [])][:8]
    titles = {_title(a): a for _, a, _ in _walk() if os.sep + "Trash" + os.sep not in a}
    for t in sorted(titles, key=len, reverse=True):                       # [[wikilinks]] to notes that already exist and are named in the text: the graph view shows how things connect
        if len(t) >= 4 and re.search(r"\b" + re.escape(t) + r"\b", text, re.I) and t not in links and len(links) < 8:
            links.append(t)
    rel_folder = FOLDERS[kind]
    if kind == "daily":
        day = datetime.date.today().isoformat()
        path = os.path.join(ROOT, "Daily", day + ".md")
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as f:
            if new:
                f.write(f"---\ncreated: {_stamp()}\nsource: jarvis\ntags: [daily]\n---\n# {day}\n\n")
            f.write(f"- {datetime.datetime.now():%H:%M} {text}" + ("  " + " ".join(f"[[{l}]]" for l in links) if links else "") + "\n")
        return {"ok": True, "path": os.path.join("Daily", day + ".md")}
    title = slug(p.get("title") or text.split(".")[0][:50])
    path = os.path.join(ROOT, rel_folder, title + ".md")
    if os.path.exists(path):                                              # same topic: a dated line is added, nothing is overwritten
        old = open(path, encoding="utf-8").read()
        if len(old) > MAX_NOTE:
            return {"ok": False, "why": "that note is full"}
        if text.lower() in old.lower():
            return {"ok": True, "path": os.path.join(rel_folder, title + ".md"), "dup": True}
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n- {datetime.date.today().isoformat()}: {text}" + ("  " + " ".join(f"[[{l}]]" for l in links) if links else "") + "\n")
        return {"ok": True, "path": os.path.join(rel_folder, title + ".md")}
    fm = (f"---\ncreated: {_stamp()}\nsource: {slug(p.get('source') or 'voice')}\nconfidence: {slug(p.get('confidence') or 'high')}\ntags: [{', '.join(tags)}]\n---\n")
    body = f"# {title}\n\n{text}\n" + ("\nRelated: " + " ".join(f"[[{l}]]" for l in links) + "\n" if links else "")
    with open(path, "w", encoding="utf-8") as f:
        f.write(fm + body)
    return {"ok": True, "path": os.path.join(rel_folder, title + ".md")}


def _snippet(text, toks, n=220):
    low = text.lower()
    i = min([low.find(t) for t in toks if low.find(t) >= 0] or [0])
    return re.sub(r"\s+", " ", text[max(0, i - 40):i + n]).strip()


def search(p):
    q = (p.get("q") or "").lower()
    toks = [t for t in re.findall(r"[\w']+", q) if len(t) > 2]
    if not toks:
        return {"ok": True, "hits": []}
    hits = []
    for label, ap, rel in _walk():
        if _private(ap):
            continue
        try:
            text = open(ap, encoding="utf-8", errors="replace").read(MAX_NOTE)
        except OSError:
            continue
        low, ti = text.lower(), _title(ap).lower()
        score = sum(low.count(t) for t in toks) + 5 * sum(t in ti for t in toks)
        if score:
            hits.append({"path": rel if label == "vault" else f"{label}/{rel}", "title": _title(ap), "snippet": _snippet(_fm_split(text)[1], toks), "score": score, "folder": rel.split(os.sep)[0] if label == "vault" else label,
                         "mtime": os.path.getmtime(ap)})
    hits.sort(key=lambda h: (-h["score"], -h["mtime"]))
    return {"ok": True, "hits": hits[:int(p.get("limit") or 6)]}


def _resolve(rel):
    """A path as the Go names it ("Memory/x.md" in the vault, "ext0/sub/x.md" in an enabled read-only folder) -> (label, absolute path, writable) or (None, None, False). Never outside the roots, never Private."""
    rel = (rel or "").replace("\\", "/").lstrip("/")
    m = re.match(r"(ext\d+)/(.*)", rel)
    for label, root, w in _roots():
        if m and label == m.group(1):
            ap = os.path.join(root, m.group(2))
        elif label == "vault" and not m:
            ap = os.path.join(root, rel)
        else:
            continue
        if _inside(ap, root) and not _private(os.path.relpath(os.path.realpath(ap), os.path.realpath(root))):
            return label, ap, w
    return None, None, False


def read(p):
    label, ap, w = _resolve(p.get("path", ""))
    if not ap or not os.path.isfile(ap) or not ap.endswith(".md") or _private(ap):
        return {"ok": False, "why": "I can't read that note (not found, or it is private)."}
    return {"ok": True, "title": _title(ap), "text": open(ap, encoding="utf-8", errors="replace").read(MAX_NOTE)}


def forget(p):
    rel = p.get("path", "")
    ap = os.path.join(ROOT, rel)
    top = rel.split("/")[0].split(os.sep)[0]
    if top not in ("Memory", "Projects", "Inbox") or not _inside(ap, ROOT) or not os.path.isfile(ap):
        return {"ok": False, "why": "I can only forget notes Jarvis wrote."}
    ensure()
    name = f"{int(time.time())}-{os.path.basename(ap)}"
    dst = os.path.join(ROOT, "Trash", name)
    shutil.move(ap, dst)
    idx = os.path.join(ROOT, "Trash", ".undo.json")
    try:
        d = json.load(open(idx))
    except (OSError, ValueError):
        d = {}
    d[name] = rel
    json.dump(d, open(idx, "w"))
    return {"ok": True, "undo": name}


def undo(p):
    name = os.path.basename(p.get("undo", ""))
    idx = os.path.join(ROOT, "Trash", ".undo.json")
    try:
        d = json.load(open(idx))
    except (OSError, ValueError):
        return {"ok": False, "why": "nothing to bring back"}
    rel = d.get(name)
    src = os.path.join(ROOT, "Trash", name)
    if not rel or not os.path.isfile(src):
        return {"ok": False, "why": "nothing to bring back"}
    dst = os.path.join(ROOT, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    n = 1
    base, ext = os.path.splitext(dst)
    while os.path.exists(dst):
        n += 1
        dst = f"{base} ({n}){ext}"
    shutil.move(src, dst)
    d.pop(name, None)
    json.dump(d, open(idx, "w"))
    return {"ok": True, "path": os.path.relpath(dst, ROOT)}


def recent(p):
    n = int(p.get("n") or 30)
    items = []
    for label, ap, rel in _walk():
        if label != "vault" or not rel.startswith("Memory"):
            continue
        t = open(ap, encoding="utf-8", errors="replace").read(1500)
        items.append({"path": rel, "title": _title(ap), "text": re.sub(r"\s+", " ", _fm_split(t)[1].replace("#", ""))[:300], "mtime": os.path.getmtime(ap)})
    items.sort(key=lambda i: -i["mtime"])
    return {"ok": True, "items": items[:n]}


def graph(p):
    """Nodes and edges from the [[wikilinks]]: at most max_nodes (default 300) by number of links; filter by folder or by a word (focus)."""
    mx = min(300, int(p.get("max") or 300))
    want = p.get("folder")
    focus = (p.get("focus") or "").lower()
    notes, byt = {}, {}
    for label, ap, rel in _walk():
        if label != "vault" or _private(ap):
            continue
        folder = rel.split(os.sep)[0]
        t = open(ap, encoding="utf-8", errors="replace").read(MAX_NOTE)
        title = _title(ap)
        notes[rel] = {"id": title, "folder": folder, "path": rel, "summary": re.sub(r"\s+", " ", _fm_split(t)[1].replace("#", ""))[:160], "links": [l.strip() for l in LINK.findall(t)], "new": time.time() - os.path.getmtime(ap) < 86400, "mtime": os.path.getmtime(ap)}
        byt.setdefault(title.lower(), title)
    nodes, edges = {}, set()
    for rel, n in notes.items():
        nodes[n["id"]] = dict(n, degree=0)
    for rel, n in notes.items():
        for l in n["links"]:
            tgt = byt.get(l.lower(), l)
            if tgt not in nodes:
                nodes[tgt] = {"id": tgt, "folder": "ghost", "path": "", "summary": "(not written yet)", "links": [], "new": False, "degree": 0, "mtime": 0}
            if tgt != n["id"]:
                edges.add((n["id"], tgt))
    for a, b in edges:
        nodes[a]["degree"] += 1; nodes[b]["degree"] += 1
    keep = set(nodes)
    if focus:
        seed = {k for k in nodes if focus in k.lower()}
        near = set(seed)
        for a, b in edges:
            if a in seed: near.add(b)
            if b in seed: near.add(a)
        keep = near or keep
    if want:
        keep = {k for k in keep if nodes[k]["folder"] == want}
    if len(keep) > mx:
        keep = set(sorted(keep, key=lambda k: -nodes[k]["degree"])[:mx])
    outn = [{k: v for k, v in nodes[k].items() if k != "links"} for k in keep]
    oute = [[a, b] for a, b in edges if a in keep and b in keep]
    return {"ok": True, "nodes": outn, "edges": oute, "total": len(nodes)}


OPS = {"append": append, "search": search, "read": read, "forget": forget, "undo": undo, "recent": recent, "graph": graph}


def handle(op, payload):
    f = OPS.get(op)
    if not f:
        return {"ok": False, "why": "unknown vault action"}
    try:
        ensure()
        return f(payload if isinstance(payload, dict) else {})
    except Exception as e:                                                   # a bad file never takes the helper down
        return {"ok": False, "why": "vault error: " + type(e).__name__}
