"""When the internet or the brain is away, Jarvis stays useful: detect it fast, say so ONCE (no error screen), keep doing everything that needs no brain, and switch back by itself.

Works without the brain (all local): the time and date, timers and reminders, opening apps and files, volume and brightness, theme/persona/orb controls, notes and lists,
local music, battery and machine status, the clock, plus a small rule-based chat (greetings, thanks, who are you, a joke).
Speech in and out (Whisper, Piper) is local already. The brain is `claude -p`: a tiny local model was only researched (see PROGRESS.md), nothing was downloaded."""
import random
import re
import threading
import time

STATE = {"off": False, "why": "net", "since": 0.0, "told": False}
CAPS = "timers and reminders, the time and date, volume and brightness, notes and lists, opening things, music from your files, machine status, the clock and my looks"
CHIP = "OFFLINE: " + CAPS.split(", the clock")[0] + " still work"
BRAIN_SLOW_S = 15.0                                 # no first token from the brain after this long: say so (once) and offer what works

JOKES = ["I told my computer I needed a break. It said no problem, and went to sleep.", "Why did the scarecrow get promoted? He was outstanding in his field.",
         "I would tell you a UDP joke, but you might not get it.", "There are only two hard things in computing: cache invalidation, naming things, and off-by-one errors.",
         "I asked the librarian for a book on paranoia. She whispered, it's right behind you."]
SMALL = [
    (r"(?:hi|hello|hey|good (?:morning|afternoon|evening)|howdy)(?: there)?", ["Hello. I'm offline at the moment, but I can still help with {caps}."]),
    (r"(?:thanks|thank you|cheers|ta)(?: very much| a lot)?", ["You're welcome.", "Any time."]),
    (r"how are you(?: doing| today)?|how's it going", ["Running fine, just without the internet."]),
    (r"who are you|what(?:'s| is) your name|what are you", ["I'm {name}, your voice assistant. I'm offline right now, so I'm using my built-in abilities."]),
    (r"good night|goodnight", ["Good night."]),
    (r"(?:tell me |say )?(?:a )?joke|make me laugh", None),
    (r"what can you do(?: offline| without (?:the )?internet)?|help|what works offline", ["Offline I can do {caps}."]),
    (r"are you (?:there|awake|online|offline)|can you hear me|test(?:ing)?", ["I'm here. {status}"]),
    (r"i love you|you're (?:great|awesome|the best)", ["That's kind of you."]),
]


def set_state(off, why=""):
    """-> True when it just changed."""
    changed = off != STATE["off"]
    STATE.update(off=off, why=why if off else STATE["why"], since=time.time() if changed and off else STATE["since"])
    if not off:
        STATE["told"] = False
    return changed


def status_line():
    return "No internet." if STATE["why"] == "net" else "My brain is not responding."


def announce_text(why="net"):
    if why == "brain":
        return f"My brain is not responding right now. I can still help with {CAPS}."
    return f"I've lost the internet. I can still help with {CAPS}."


def chip(why="net"):
    return ("BRAIN NOT RESPONDING: " if why == "brain" else "OFFLINE: ") + CAPS.split(", the clock")[0] + " still work"


def told_once():
    """True the first time in an outage (so it is said once), then False."""
    if STATE["told"]:
        return False
    STATE["told"] = True
    return True


def chat(text, persona_name="Jarvis"):
    """A rule-based reply for small talk, or None."""
    low = re.sub(r"\s+", " ", re.sub(r"[^a-z' ]", " ", text.lower())).strip()
    for pat, replies in SMALL:
        if re.fullmatch(pat, low):
            r = random.choice(replies or JOKES)
            return r.format(caps=CAPS, name=persona_name, status=status_line())
    return None


def handle(text, persona_name="Jarvis"):
    """The reply when a question needs the brain and the brain is away -> {"speech", "offline": True}. Never an error."""
    c = chat(text, persona_name)
    if c:
        return {"speech": c, "offline": True}
    if told_once():
        return {"speech": f"{status_line()} I can't answer that without it, but I can still help with {CAPS}.", "offline": True}
    return {"speech": "Still offline, so I can't answer that one.", "offline": True}


class BrainWatch:
    """Started with a turn that needs the brain: if no first token arrives within BRAIN_SLOW_S, calls on_slow once (the turn keeps waiting: the brain may still answer)."""

    def __init__(self, on_slow, seconds=BRAIN_SLOW_S):
        self.t = threading.Timer(seconds, on_slow)
        self.t.daemon = True

    def start(self):
        self.t.start()

    def got_token(self):
        self.t.cancel()

    cancel = got_token
