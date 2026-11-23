"""Live connect guide ("help me connect these"). The vision step names two spots on the live picture (the plug end and the port, or a wire end and a pin) as boxes; the tracker follows both while the camera stays live;
this module turns their positions into a line, three stages (far / close / aligned), a short step line and a "connected?" confirmation. It never guesses a hidden port, never claims success on its own
(it asks you), speaks only when the step changes, updates its text at most twice a second, and never guides anything with mains power.
Pure functions + a small state class, so the tests can drive it with fake positions."""
import json
import re
import time

from . import look

FAR, CLOSE, TOUCH = 0.22, 0.08, 0.035           # distances in picture fractions between the two spots
MAINS = re.compile(r"\b(?:wall|outlet|socket|mains|plug socket|power strip|230 ?v|120 ?v|ac power)\b", re.I)
PORTS = ("USB-C", "USB-A", "micro-USB", "HDMI", "audio jack", "SD card", "barrel plug", "GPIO pin", "breadboard hole")

PROMPT = ("[CONNECT GUIDE: the picture shows two things the user wants to connect (a plug or wire end, and the port or pin it goes into). Find the exact spot of each. "
          "Answer with ONE short spoken line saying where it goes (under 16 words, e.g. 'Plug the USB-C end into the port on the bottom edge of the phone'). Then end with ONE JSON line: "
          "{\"connect\":{\"a\":{\"name\":\"USB-C plug\",\"box\":[x,y,w,h]},\"b\":{\"name\":\"phone USB-C port\",\"box\":[x,y,w,h]},\"kind\":\"USB-C\",\"say\":\"the short line\"}} "
          "where a is the plug/wire end that moves, b the port or pin it goes into, box is the top-left x,y and size w,h as fractions 0 to 1 of the picture, tight around just that spot. "
          "If you cannot clearly see the port or pin (hidden side, too small), do NOT guess: reply {\"connect\":{\"missing\":\"port\",\"say\":\"I can't see the port, turn the phone so the bottom edge faces the camera\"}}. "
          "Never identify people. Never guide anything with mains power or a wall outlet: for those reply {\"connect\":{\"mains\":true}}. Never read the JSON aloud.]\n")


def extract(reply):
    for m in re.finditer(r'\{\s*"connect"', reply or ""):
        try:
            obj, _ = json.JSONDecoder().raw_decode(reply[m.start():])
        except ValueError:
            continue
        if isinstance(obj.get("connect"), dict):
            return obj["connect"]
    return None


def validate(c):
    """-> ("ok", a, b, say) | ("missing", say) | ("mains", say) | ("none", "")."""
    if not isinstance(c, dict):
        return ("none", "")
    if c.get("mains") or MAINS.search(json.dumps(c.get("a", "")) + json.dumps(c.get("b", ""))):
        return ("mains", "I won't guide anything with mains power. Plug the charger into the wall yourself.")
    if c.get("missing"):
        return ("missing", str(c.get("say") or "I can't see the port. Turn it so the port faces the camera.")[:160])
    boxes = []
    for k in ("a", "b"):
        o = c.get(k) or {}
        got = look.validate_boxes([{"name": o.get("name", k), "box": o.get("box")}], 1) if isinstance(o, dict) else []
        if not got:
            return ("missing", "I can't see both ends clearly. Move the camera or the parts so both are in view.")
        boxes.append(got[0])
    return ("ok", boxes[0], boxes[1], str(c.get("say") or "")[:160])


def center(b):
    return b["x"] + b["w"] / 2, b["y"] + b["h"] / 2


def distance(a, b):
    (ax, ay), (bx, by) = center(a), center(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def stage(d):
    return "far" if d >= FAR else "close" if d >= CLOSE else "aligned"


STEP = {"far": "bring them closer", "close": "closer, line the plug up with the port", "aligned": "lined up: push it in", "touch": "connected?"}


class Guide:
    def __init__(self, say, notify, electronics=False, base=""):
        self.say, self.notify, self.electronics, self.base = say, notify, electronics, base
        self.stage, self.text, self.t_text, self.t_say, self.touch_since, self.asked, self.lostmsg = "", "", 0.0, 0.0, None, False, False
        self.done = False

    def feed(self, out, now=None):
        """out = tracker boxes [a, b] with "lost" flags. Sends the line + stage to the page; speaks only on a step change."""
        if self.done or len(out) < 2:
            return None
        now = now or time.time()
        a, b = out[0], out[1]
        lost = [o["name"] for o in out[:2] if o.get("lost")]
        d = distance(a, b)
        st = "far" if lost else stage(d)
        if not lost and d < TOUCH:
            self.touch_since = self.touch_since or now
            if now - self.touch_since > 1.2:
                st = "touch"
        else:
            self.touch_since = None
        if lost:
            text = f"I can't see the {lost[0].lower()}, turn it so it faces the camera"
        else:
            text = STEP[st] + (" (verify before powering)" if self.electronics and st in ("aligned", "touch") else "")
        if text != self.text and now - self.t_text >= 0.5:                       # the words change at most twice a second: no flicker
            self.text, self.t_text = text, now
        changed = st != self.stage or (bool(lost) != self.lostmsg)
        if changed and now - self.t_say >= 2.0:                                  # spoken only on a step change, never constant chatter
            self.t_say = now
            self.say(self.text)
        if changed:
            self.stage, self.lostmsg = st, bool(lost)
        msg = {"t": "look", "op": "connect", "a": a, "b": b, "dist": round(d, 3), "stage": st, "text": self.text, "confirm": st == "touch"}
        self.notify(msg)
        if st == "touch" and not self.asked:
            self.asked = True
        return msg

    def confirm(self):
        self.done = True
        self.notify({"t": "look", "op": "connect", "off": True})
