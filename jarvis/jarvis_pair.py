"""Shared pairing + signing for Jarvis -> Mac messages. Standard library only.
The SAME file is used on the Go (sender) and on the Mac (receiver).
The pairing code is the only secret. Never print, log or commit it, the topic or the key."""
import hashlib, hmac, json, secrets, time

ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"  # no 0/O/1/I/L/U look-alikes
SKEW = 300  # seconds a message stays valid


def new_code():
    """24 characters, ~117 bits, shown as 6 groups of 4. Show it on the Go screen once."""
    raw = "".join(secrets.choice(ALPHABET) for _ in range(24))
    return "-".join(raw[i:i + 4] for i in range(0, 24, 4))


def derive(code):
    norm = "".join(c for c in code.upper() if c in ALPHABET)
    if len(norm) != 24:
        raise ValueError("pairing code must have 24 letters/digits")
    m = hashlib.pbkdf2_hmac("sha256", norm.encode(), b"jarvis-inbox-v1", 300000, 64)
    return "jv-" + m[:16].hex(), m[32:]  # (ntfy topic, hmac key)


def ack_topic(code):
    """A SECOND private topic from the same code: the Mac publishes its delivery replies here, the Go listens. (The Mac stays outbound-only.)"""
    norm = "".join(c for c in code.upper() if c in ALPHABET)
    m = hashlib.pbkdf2_hmac("sha256", norm.encode(), b"jarvis-inbox-v1", 300000, 64)
    return "jv-" + m[16:32].hex()


def _canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sign(key, payload):
    """payload: dict like {"type":"open_url","url":"https://..."}. Returns an ASCII JSON string."""
    body = dict(payload, v=1, ts=int(time.time()), nonce=secrets.token_hex(8))
    body["sig"] = hmac.new(key, _canon(body).encode(), hashlib.sha256).hexdigest()
    return _canon(body)


def verify(key, text, seen, now=None):
    """Returns the payload dict if genuine, fresh and not replayed; else None."""
    try:
        body = json.loads(text)
        sig = body.pop("sig")
        if body.get("v") != 1:
            return None
        want = hmac.new(key, _canon(body).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, want):
            return None
        if abs((now or time.time()) - int(body["ts"])) > SKEW:
            return None
        if body["nonce"] in seen:
            return None
        seen.add(body["nonce"])
        return body
    except Exception:
        return None


def check(key, text, seen, now=None):
    """Like verify, but explains a refusal: -> (payload or None, reason or "", message id or None). The id is only returned once the signature is genuine."""
    try:
        body = json.loads(text)
        sig = body.pop("sig")
    except Exception:
        return None, "not a Jarvis message", None
    try:
        if body.get("v") != 1:
            return None, "bad signature", None
        want = hmac.new(key, _canon(body).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, want):
            return None, "bad signature", None
        mid = body.get("nonce")
        if abs((now or time.time()) - int(body["ts"])) > SKEW:
            return None, "too old", mid
        if mid in seen:
            return None, "already handled (replayed)", mid
        seen.add(mid)
        return body, "", mid
    except Exception:
        return None, "bad signature", None
