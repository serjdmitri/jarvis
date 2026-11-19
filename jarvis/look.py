"""Look: the camera or screen picture, labelled boxes around what the brain can identify, and the voice commands that drive the full-screen LOOK view.
Boxes come from the brain (option a: no downloads): one JSON line {"look":[{"name","box":[x,y,w,h]}]} with fractions 0..1; this code validates, clamps and drops bad ones.
Live detection with a local model (option b) is only a researched option (about 0.2 to 1.6 s per frame on a fanless Pentium); nothing is downloaded."""
import json
import re

FRESH = re.compile(r"\b(look at (?:this|that|it|these|those)|take a look|what do you see|what can you see|can you see (?:this|that|it)|what am i (?:holding|looking at|showing)|identify (?:this|that|it)|what(?:'s| is) (?:this|that)(?: thing| object)?|what are (?:these|those)|read (?:this|that) (?:label|sign|text|for me))\b", re.I)
BOXING = re.compile(r"^(?:please )?(?:box|outline|highlight|point out|label|circle|find) (?:all |every |each |the |a |an )?(.+)$", re.I)

PROMPT = ("[LOOK: a picture from the user's camera or screen is attached. Answer in one or two short spoken sentences (the first under 10 words). Then end your reply with ONE JSON line: "
          "{\"look\":[{\"name\":\"CUP\",\"box\":[x,y,w,h]}]} listing up to 8 distinct things you can clearly identify, where box is the top-left corner x,y and the width w and height h as fractions from 0 to 1 "
          "of the picture, and name is 1 to 3 plain words. Skip anything you are not sure of. Never read the JSON aloud.]\n")
PROMPT_FOLLOW = "[LOOK follow-up: the same picture is attached again. {sel}Answer briefly. Add the {{\"look\":[...]}} JSON line only if the user asked you to box or find something.]\n"
FOCUS = "Make sure these are boxed if visible: {what}. "

ORD = {"first": 1, "1st": 1, "one": 1, "second": 2, "2nd": 2, "two": 2, "third": 3, "3rd": 3, "three": 3, "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "last": -1}


def is_fresh(text):
    return bool(FRESH.search(text or ""))


def validate_boxes(raw, limit=8):
    """The brain's list -> clean [{"name","x","y","w","h"}] (fractions, clamped). Anything unusable is dropped."""
    out = []
    if not isinstance(raw, list):
        return out
    for o in raw[:limit * 2]:
        try:
            name = re.sub(r"[^A-Za-z0-9 \-']", "", str(o.get("name", ""))).strip().upper()[:24]
            x, y, w, h = (float(v) for v in o["box"][:4])
        except (AttributeError, KeyError, TypeError, ValueError, IndexError):
            continue
        if not name or any(v != v for v in (x, y, w, h)):
            continue
        if x < -0.05 or y < -0.05 or w <= 0 or h <= 0 or x > 1.05 or y > 1.05 or x + w > 1.1 or y + h > 1.1:
            continue                                                     # out of range: dropped, not "fixed"
        x, y = max(0.0, x), max(0.0, y)
        w, h = min(w, 1 - x), min(h, 1 - y)                              # clamped to the picture
        if w < 0.02 or h < 0.02:
            continue
        out.append({"name": name, "x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)})
        if len(out) >= limit:
            break
    return out


def extract(reply):
    """-> raw list from {"look": [...]} anywhere in the reply's JSON blocks, or None."""
    for m in re.finditer(r'\{\s*"(?:look|show|open|phone|mac|intent)"', reply):
        try:
            obj, _ = json.JSONDecoder().raw_decode(reply[m.start():])
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("look"), list):
            return obj["look"]
    return None


def parse_command(text, state):
    """Voice commands for the LOOK view -> {"op": ..., ...} or None. `state` = the assistant's look state ({"open": bool, "boxes": [...]})."""
    low = (text or "").lower().strip(" .?!")
    if not state.get("open"):
        m = BOXING.match(low)
        return {"op": "box", "what": m.group(1)} if m and len(m.group(1)) < 40 and not re.match(r"(?:me|my|up|out|the map|a map|it)\b", m.group(1)) else None
    if re.fullmatch(r"(?:please )?(?:clear|remove|hide|delete) (?:the |all )?(?:boxes|labels|box)", low):
        return {"op": "clear"}
    if re.fullmatch(r"(?:please )?(?:freeze|hold|pause|stop)(?: it| the (?:picture|image|frame|camera|view))?", low):
        return {"op": "freeze"}
    if re.fullmatch(r"(?:please )?(?:go live|live|resume|unfreeze|back to live|show live)(?: view| camera)?", low):
        return {"op": "live"}
    if re.fullmatch(r"(?:please )?(?:close|exit|leave) (?:the )?(?:look|view|camera)(?: view)?", low):
        return {"op": "close"}
    m = re.fullmatch(r"(?:what(?:'s| is) |tell me about |select |show me |pick |highlight )?(?:the )?(first|second|third|fourth|fifth|last|1st|2nd|3rd|4th|5th|one|two|three) (?:one|box|thing|object|item)", low)
    if m:
        return {"op": "select", "n": ORD[m.group(1)], "speak": low.startswith(("what", "tell"))}
    m = re.fullmatch(r"(?:please )?(?:select|choose|pick|highlight|what(?:'s| is)|tell me about) (?:the |that |this )?(.+)", low)
    if m:
        for i, b in enumerate(state.get("boxes", [])):
            if b["name"].lower() == m.group(1) or m.group(1) in b["name"].lower():
                return {"op": "select", "n": i + 1, "speak": low.startswith(("what", "tell"))}
    m = BOXING.match(low)
    if m and len(m.group(1)) < 40:
        return {"op": "box", "what": m.group(1)}
    return None
