"""Look: the camera or screen picture, labelled boxes around what the brain can identify, and the voice commands that drive the full-screen LOOK view.
Boxes come from the brain (option a: no downloads): one JSON line {"look":[{"name","box":[x,y,w,h]}]} with fractions 0..1; this code validates, clamps and drops bad ones.
Live detection with a local model (option b) is only a researched option (about 0.2 to 1.6 s per frame on a fanless Pentium); nothing is downloaded."""
import json
import re

FRESH = re.compile(r"\b(look at (?:this|that|it|these|those)|look at (?:the|my) screen|show (?:me )?my screen|show me (?:what (?:is|'s) (?:in front|here|there)|what i(?:'m| am) holding|what you see|this thing)|what(?:'s| is) in front of (?:me|you|the camera)|(?:use|through) the (?:phone )?camera|take a look|what do you see|what can you see|can you see (?:this|that|it)|what am i (?:holding|looking at|showing)|identify (?:this|that|it)|what(?:'s| is) (?:this|that)(?: thing| object)?|what are (?:these|those)|read (?:this|that) (?:label|sign|text|for me))\b", re.I)
BOXING = re.compile(r"^(?:please )?(?:box|outline|highlight|point out|label|circle|find) (?:all |every |each |the |a |an )?(.+)$", re.I)
CAMCUE = re.compile(r"\b(?:on|in|at) (?:the |my )?(?:desk|table|bench|workbench|picture|photo|image|camera|view|screen|frame)\b|\b(?:here|in front of (?:me|you)|you can see|you see)\b", re.I)

PROMPT = ("[LOOK: a picture from the user's camera or screen is attached. Answer in one or two short spoken sentences (the first under 10 words). Then end your reply with ONE JSON line: "
          "{\"look\":[{\"name\":\"CUP\",\"box\":[x,y,w,h]}]} listing up to 8 distinct things you can clearly identify, where box is the top-left corner x,y and the width w and height h as fractions from 0 to 1 "
          "of the picture, and name is 1 to 3 plain words. Skip anything you are not sure of. Never read the JSON aloud.]\n")
PROMPT_FOLLOW = "[LOOK follow-up: the same picture is attached again. {sel}Answer briefly. Add the {{\"look\":[...]}} JSON line only if the user asked you to box or find something.]\n"
FOCUS = "Make sure these are boxed if visible: {what}. "
ONLY = ("[HIGHLIGHT: box ONLY this: {what}. Do not box anything else. If several of that kind are visible, give EACH its own box and name them with numbers (for example CUP 1, CUP 2, left to right). "
        "If it is not visible say so in one short sentence and return an empty list. End with ONE JSON line {{\"look\":[{{\"name\":\"CUP\",\"box\":[x,y,w,h]}}]}} where box is the top-left x,y and the width w and height h as fractions 0 to 1, "
        "drawn TIGHT around the object itself (not its shadow or the table around it). Never read the JSON aloud.]\n")
ALL_WORDS = re.compile(r"^(?:everything|all|all of (?:it|them)|all the (?:things|stuff|items|objects)|things|stuff|items|objects|what you see|whatever you see)\b", re.I)


def _stem(w):
    w = re.sub(r"[^a-z0-9 ]", "", (w or "").lower()).strip()
    w = re.sub(r"^(?:the|that|this|my|a|an)\s+", "", w)
    return re.sub(r"(?:es|s)$", "", w) if len(w) > 3 else w


def names_match(want, name):
    """Does a spoken word ("charger", "phones") name a box ("CHARGER 2", "PHONE")?"""
    a, b = _stem(want), _stem(re.sub(r"\s*\d+$", "", name or ""))
    return bool(a) and bool(b) and (a == b or a in b or b in a)

ORD = {"first": 1, "1st": 1, "one": 1, "second": 2, "2nd": 2, "two": 2, "third": 3, "3rd": 3, "three": 3, "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "last": -1}


_TAIL = re.compile(r"^(?:\s*(?:please|now|for me|right now|here|thing|object|part|one|through the (?:phone )?camera|(?:on|in|at|from) (?:the |my )?(?:desk|table|bench|workbench|picture|photo|image|camera|view|screen|frame)|in front of (?:me|you|the camera)|of (?:me|you|the (?:phone )?camera)))*\s*[.?!]*$", re.I)
DESK_ITEMS = re.compile(r"\b(?:screws?|bolts?|nuts?|washers?|resistors?|leds?|wires?|cables?|jumpers?|coins?|batteries|chips?|boards?|buttons?|items?|things|parts?|components?|pins?|capacitors?|connectors?|tools?|pens?|cards?|boxes)\b", re.I)


def is_fresh(text):
    """A look is asked for only when nothing else follows the phrase ("what's that song", "what do you see in the news" are ordinary questions)."""
    t = text or ""
    for m in FRESH.finditer(t):
        if _TAIL.match(t[m.end():]):
            return True
    return False


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
        if re.search(r"\b(face|faces|head)\b", name, re.I):             # never a face: a person is just "person", no recognition, no names
            name = "PERSON"
        if x < -0.05 or y < -0.05 or w <= 0 or h <= 0 or x > 1.05 or y > 1.05 or x + w > 1.1 or y + h > 1.1:
            continue                                                     # out of range: dropped, not "fixed"
        x, y = max(0.0, x), max(0.0, y)
        w, h = min(w, 1 - x), min(h, 1 - y)                              # clamped to the picture
        if w < 0.02 or h < 0.02:
            continue
        if w * h > 0.92:                                                 # a box around the whole picture says nothing: dropped
            continue
        b = {"name": name, "x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}
        if any(_iou(b, o) > 0.85 for o in out):                          # the same thing boxed twice
            continue
        out.append(b)
        if len(out) >= limit:
            break
    return out


def _iou(a, b):
    ix = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    iy = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    inter = ix * iy
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union else 0.0


MODES = [   # (mode, voice pattern). Each is a fresh look at the live picture with its own instructions; all of them say so when they are not sure.
    ("connect", re.compile(r"\b(?:help me (?:connect|plug|wire|attach|hook up|fit)|how do i (?:connect|plug|attach)|where (?:does|do) (?:this|these|that|it) (?:go|plug|connect)|connect (?:these|this|them)|plug (?:this|these|it) in|guide me (?:to )?(?:connect|plug))\b", re.I)),
    ("wiring", re.compile(r"\b(?:check (?:my |the )?(?:wiring|circuit|breadboard)|is (?:my |the )?(?:wiring|circuit) (?:right|correct|ok)|look at my (?:wiring|circuit|breadboard))\b", re.I)),
    ("count", re.compile(r"^(?:please )?count (?:the |all |these |those )(.+?)(?: (?:on|in) (?:my |the )?(?:desk|table|bench|picture|view|camera))?$|^how many (.+?) (?:are|do you see|can you see)(?: there)? (?:on|in) (?:my |the )?(?:desk|table|bench|picture|view|camera)$|^how many (.+?) (?:do you see|can you see)$", re.I)),
    ("measure", re.compile(r"^(?:please )?(?:measure|how (?:big|long|wide|tall|large) is) (?:this|that|it|the (?:thing|object|part|board))(?: (?:using|with|next to|by|against) (?:a |the |my )?(.+))?$", re.I)),
    ("read", re.compile(r"\b(?:read (?:the |this |that )?(?:label|text|part number|markings?|writing|print)|what(?:'s| is) (?:this|that) part|what does (?:it|this|the label) say$|what part is this)\b", re.I)),
]
MODE_PROMPTS = {
    "read": "[READ: read the printed text, labels and part numbers visible on the object in the picture, exactly as printed. Then say in one or two short sentences what they mean (what the part is). If text is blurry or cut off say which part and ask the user to move the object or the camera closer: never invent characters. End with ONE JSON line {\"look\":[{\"name\":\"LABEL\",\"box\":[x,y,w,h]}]} boxing each piece of text you read (fractions 0 to 1, up to 8).]\n",
    "count": "[COUNT: count the items of this kind on the desk: {what}. Say the number in one short sentence, and say so if some may be hidden or overlapping (then it is at least that many). End with ONE JSON line {\"look\":[{\"name\":\"1\",\"box\":[x,y,w,h]},{\"name\":\"2\",\"box\":[...]}]} one numbered box per item counted (the name is just its number), fractions 0 to 1, up to 8 (if more, box 8 and say so).]\n",
    "measure": "[MEASURE: estimate the size of the object in the picture using this reference object for scale: {what}. If no reference object is visible, ask the user to put a card, coin or ruler next to it, and do not guess. Otherwise give width and height in centimetres and say clearly it is APPROXIMATE (perspective and angle change it). End with ONE JSON line {\"look\":[{\"name\":\"REFERENCE\",\"box\":[x,y,w,h]},{\"name\":\"OBJECT\",\"box\":[...]}]}.]\n",
    "wiring": "[CHECK WIRING: compare what is on the breadboard or Raspberry Pi in the picture with this project: {what}. Say only what you can really SEE (which parts, which rows or pins a wire reaches) and what you cannot see or are unsure of; never say a circuit is correct or safe to power. Finish with the plain line 'verify before powering'. End with ONE JSON line {\"look\":[{\"name\":\"WIRE\",\"box\":[x,y,w,h]}]} boxing the things you mention (up to 8).]\n",
}


def mode_of(text, open_view=False):
    """-> {"op": "mode", "mode": ..., "what": ...} when the words ask for one of the camera helpers, else None."""
    low = (text or "").lower().strip(" .?!")
    for name, rx in MODES:
        m = rx.search(low)
        if m:
            what = next((g for g in m.groups() if g), "") if m.groups() else ""
            if name in ("count", "measure") and not open_view and not CAMCUE.search(low) and not (name == "count" and DESK_ITEMS.search(what)) and not (name == "measure" and re.search(r"\b(?:measure|how (?:big|long|wide|tall|large) is (?:this|that))\b", low) and not re.search(r"is (?:it|that)$", low)):
                continue                                                  # "how long is it", "count the sheep": ordinary questions unless the camera view is open or the words point at the desk
            if name == "count" and re.search(r"\b(?:until|till|to|from|down|up|minutes?|hours?|days?|seconds?|words?|times?)\b", what):
                continue
            return {"op": "mode", "mode": name, "what": what.strip()[:60] or ("the items" if name == "count" else "a card or a coin")}
    return None


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
    md = mode_of(low, bool(state.get("open")))
    if md:
        return md
    if re.fullmatch(r"(?:please )?(?:scan|list|find) (?:everything|all|all the (?:things|stuff|items)) (?:on|in) (?:my |the )?(?:desk|table|workbench|bench)|what(?:'s| is) (?:all )?(?:on|in) my (?:desk|table|workbench|bench)|(?:please )?scan (?:my |the )?(?:desk|table|workbench|bench)", low):
        return {"op": "box", "what": "everything on the desk, every separate item"}              # "scan everything on my desk": every visible item with a box
    if state.get("open") and state.get("guide") and re.fullmatch(r"(?:yes[, ]*)?(?:it(?:'s| is)|that(?:'s| is)|they(?:'re| are)) (?:connected|plugged in|in)|(?:yes[, ]*)?(?:connected|plugged in|done|it worked)", low):
        return {"op": "connected"}
    if not state.get("open"):
        m = BOXING.match(low)                                          # with the view closed, only when the words point at the camera/desk (not "find a pizza place", "circle back")
        return {"op": "box", "what": m.group(1), "only": not ALL_WORDS.match(m.group(1))} if m and len(m.group(1)) < 40 and CAMCUE.search(low) and not re.match(r"(?:me|my|up|out|the map|a map|it|back)\b", m.group(1)) else None
    if re.fullmatch(r"(?:please )?(?:clear|remove|hide|delete|turn off|stop showing) (?:the |all |every )?(?:highlights?|boxes|labels|box|outlines?)|(?:please )?stop highlighting(?: (?:everything|all|it|them))?|(?:please )?(?:un)?highlight (?:everything|all)", low):
        return {"op": "clear"}
    m = re.fullmatch(r"(?:please )?(?:(?:stop|quit) (?:highlighting|boxing|showing)|(?:don't|do not) (?:highlight|box|show)|un-?highlight|(?:remove|clear|hide) (?:the )?(?:highlight|box|outline|label)(?: (?:on|from|around|for))?) (?:the |that |this |my )?(.+?)(?: highlight| box)?", low)
    if m and m.group(1) not in ("it", "them", "everything", "all"):
        return {"op": "drop", "what": m.group(1)}
    m = re.fullmatch(r"(?:please )?highlight (?:just|only) (?:the |that |this |my )?(.+)", low)
    if m:
        mo = re.fullmatch(r"(first|second|third|fourth|fifth|last|1st|2nd|3rd|4th|5th)(?: one| box| thing| object| item)?", m.group(1))
        if mo:
            return {"op": "keepn", "n": ORD[mo.group(1)]}
        if any(names_match(m.group(1), b.get("name", "")) for b in state.get("boxes", [])):
            return {"op": "keep", "what": m.group(1)}                         # already boxed: just hide the others, no new look
        return {"op": "box", "what": m.group(1), "only": True}
    m = re.fullmatch(r"(?:please )?(?:highlight|box|outline|show|point (?:out|at)) (?:the |that |only the )?(first|second|third|fourth|fifth|last|1st|2nd|3rd|4th|5th) (?:one|box|thing|object|item)", low)
    if m and state.get("boxes"):
        return {"op": "keepn", "n": ORD[m.group(1)]}
    if re.fullmatch(r"(?:please )?(?:freeze|hold|pause|stop)(?: it| the (?:picture|image|frame|camera|view))?", low):
        return {"op": "freeze"}
    if re.fullmatch(r"(?:please )?(?:go live|live|resume|unfreeze|back to live|show live)(?: view| camera)?", low):
        return {"op": "live"}
    if re.fullmatch(r"(?:please )?(?:close|exit|leave|turn off|stop) (?:the )?(?:look|looking|view|camera)(?: view)?(?: at (?:it|this))?", low):
        return {"op": "close"}
    m = re.fullmatch(r"(?:what(?:'s| is) |tell me about |select |show me |pick |highlight )?(?:the )?(first|second|third|fourth|fifth|last|1st|2nd|3rd|4th|5th|one|two|three) (?:one|box|thing|object|item)", low)
    if m:
        return {"op": "select", "n": ORD[m.group(1)], "speak": low.startswith(("what", "tell"))}
    m = re.fullmatch(r"(?:please )?(?:select|choose|pick|highlight|what(?:'s| is)|tell me about) (?:the |that |this )?(.+)", low)
    if m:
        if low.lstrip("please ").startswith("highlight") and any(names_match(m.group(1), b.get("name", "")) for b in state.get("boxes", [])):
            return {"op": "keep", "what": m.group(1)}
        for i, b in enumerate(state.get("boxes", [])):
            if b["name"].lower() == m.group(1) or m.group(1) in b["name"].lower():
                return {"op": "select", "n": i + 1, "speak": low.startswith(("what", "tell"))}
    m = BOXING.match(low)
    if m and len(m.group(1)) < 40:
        return {"op": "box", "what": m.group(1), "only": not ALL_WORDS.match(m.group(1))}
    return None
