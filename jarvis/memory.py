"""What Jarvis remembers about you: short facts in data/memory.json (inside the jarvis folder, git-ignored).
"remember that my mum is called Ana", "I like jazz", "my usual coffee place is X" -> a fact. "what do you remember about me", "forget that", "forget everything" (needs a spoken
confirmation). The brain only ever gets the few facts that share keywords with the question, never the whole file.
Never stored: passwords, card numbers, codes, tokens, keys (refused with the reason)."""
import json
import os
import re
import time
import uuid

from . import config

PATH = config.ROOT / "data" / "memory.json"
STOP = set("a an the is are was were be been am i you he she it we they my your his her our their me to of in on at for with and or but not no do does did can could would should will "
           "what who when where why how which that this these those about tell please just really very some any there here from by as if so than then also have has had get got "
           "remember know like likes usual favourite favorite called name".split())
SECRET = re.compile(r"\b(pass(?:word|code|phrase)|passwd|pin(?: number| code)?|cvv|cvc|security code|card number|credit card|debit card|iban|account number|routing number|ssn|social security|"
                    r"api[ -]?key|secret|token|private key|seed phrase|recovery (?:code|phrase)|otp|2fa|verification code|pairing code|topic)\b", re.I)


def looks_secret(text):
    """True for anything that looks like a credential: the words above, a 13-19 digit card-like number, or a long random-looking string."""
    t = text or ""
    if SECRET.search(t):
        return True
    digits = re.sub(r"[ -]", "", t)
    if re.search(r"\d{13,19}", digits):
        return True
    if re.search(r"\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{20,}\b", t):
        return True
    return bool(re.search(r"\b\d{4,8}\b", t) and re.search(r"\b(code|pin)\b", t, re.I))


def _load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(facts):
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(json.dumps(facts, indent=1, ensure_ascii=False), encoding="utf-8")


def add(text):
    """-> (ok, spoken)."""
    t = re.sub(r"\s+", " ", text).strip(" .")
    if not t:
        return False, "Remember what?"
    if looks_secret(t):
        return False, "I don't store passwords, card numbers, codes or keys, so I won't remember that one."
    t = t[0].upper() + t[1:]
    facts = _load()
    if any(f["text"].lower() == t.lower() for f in facts):
        return True, "I already know that."
    facts.append({"id": uuid.uuid4().hex[:8], "text": t[:200], "t": time.time()})
    _save(facts[-300:])
    return True, "Got it, I'll remember that."


def all_facts():
    return _load()


def forget_last():
    facts = _load()
    if not facts:
        return "I don't have anything to forget."
    gone = facts.pop()
    _save(facts)
    return f"Forgotten: {gone['text']}."


def forget_id(fid):
    facts = _load()
    n = [f for f in facts if f["id"] != fid]
    _save(n)
    return len(n) != len(facts)


def forget_all():
    n = len(_load())
    _save([])
    return n


def _words(t):
    return {w for w in re.findall(r"[a-z0-9']+", t.lower()) if len(w) > 2 and w not in STOP}


def relevant(question, limit=3):
    """The few facts that share keywords with the question (simple keyword overlap), best first."""
    q = _words(question)
    if not q:
        return []
    scored = []
    for f in _load():
        s = len(q & _words(f["text"]))
        if s:
            scored.append((s, f["t"], f["text"]))
    return [t for _, _, t in sorted(scored, reverse=True)[:limit]]


def context(question):
    r = relevant(question)
    return ("[things you know about the user (use only if relevant): " + "; ".join(r) + "]\n") if r else ""


def parse_command(text):
    t = text.strip()
    low = t.lower().strip(" .?!")
    if re.fullmatch(r"(?:yes[, ]*)?(?:please )?(?:confirm )?forget everything(?: you know)?(?: about me)?", low) and low.startswith(("yes", "confirm")):
        n = forget_all()
        return {"speech": f"Done. I've forgotten all {n} things." if n else "There was nothing to forget."}
    if re.fullmatch(r"(?:please )?forget (?:everything|all of it|all that)(?: you know)?(?: about me)?|(?:please )?clear (?:your |my )?memory|wipe (?:your |my )?memory", low):
        n = len(_load())
        return {"speech": f"That would erase all {n} things I remember. Say yes, forget everything, to confirm."} if n else {"speech": "I don't remember anything yet."}
    if re.fullmatch(r"(?:please )?forget (?:that|the last (?:thing|one|fact))", low):
        return {"speech": forget_last()}
    if re.fullmatch(r"what do you (?:remember|know) about me|what have i told you|what do you remember|show (?:me )?(?:my )?memor(?:y|ies)|show (?:me )?what you remember", low):
        f = _load()
        if not f:
            return {"speech": "I don't know anything about you yet. Tell me, for example: remember that my mum is called Ana."}
        spoken = "; ".join(x["text"].rstrip(".") for x in f[-4:])
        return {"speech": f"I remember {len(f)} thing{'s' if len(f) != 1 else ''}. Most recent: {spoken}.", "cards": [{"type": "memory", "title": "Memory", "items": [{"id": x["id"], "text": x["text"]} for x in f[::-1][:30]]}]}
    if re.match(r"(?:set )?my usual (?:place|destination|spot) (?:is|to)\b", low):
        return None                                                      # that one is the briefing's drive-time place (extras)
    m = re.match(r"(?:please )?remember (?:that )?(.+)$", t, re.I)
    if m and not re.match(r"(?:please )?remember to\b", low) and not re.search(r"\b(alarm|timer)\b", low):
        ok, sp = add(m.group(1))
        return {"speech": sp}
    short = len(t.split()) <= 12 and not re.search(r"\?|\bbut\b|\bso\b|\bbecause\b|\bwhat\b|\bwhere\b|\bhow\b|\bwhich\b", low)
    if short and re.match(r"(?:my (?:usual|favou?rite) .+ is .+|i (?:really )?(?:like|love|prefer|hate|dislike|am allergic to|work as|work at|live in|was born in) .+|my (?:mum|mom|dad|wife|husband|partner|son|daughter|brother|sister|dog|cat|boss|birthday|name) .+)$", t.strip(" ."), re.I):
        ok, sp = add(t)
        return {"speech": sp}
    return None
