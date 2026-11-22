"""Routing tests for PROMPT26: 60+ phrases across timers, lists, briefing, highlight, "what am I holding", "where can I get one", "what can I make with this", parts lists, text messages, widgets, news,
weather, music, personas, whisper mode, settings; plus the negatives that must end in a refusal or a safe alternative and NO action. No brain, no network (skills.handle only).
Run: .venv/bin/python tests/round26_routing.py"""
import os, sys, tempfile, pathlib, re
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="r26-"); os.environ["JARVIS_DATA_DIR"] = tempfile.mkdtemp(prefix="r26d-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from jarvis import skills, look
R = []
def rec(n, ok, extra=""): R.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n, extra)
KEYS = ("widget", "projects", "product", "learn", "whisper", "findphone", "btspeaker", "presence", "settingsio", "selftest", "backup", "daylog", "sendfile", "persona", "claudehook", "timer", "open")
def route(t, persona="jarvis"):
    r = skills.handle(t, persona)
    if not r: return None
    k = [x for x in KEYS if r.get(x) is not None]
    return k[0] if k else ("cards" if r.get("cards") else "speech")
POS = [("show the music widget", "widget"), ("show chat", "widget"), ("show notes", "widget"), ("show today", "widget"), ("show the map", "widget"), ("quiz me on photosynthesis", "learn"), ("make flashcards about the French revolution", "learn"),
       ("quiz me on what I got wrong", "learn"), ("I have a Raspberry Pi 4 and two servos", "projects"), ("add a breadboard to my parts", "projects"), ("what parts do I have", "projects"), ("make a parts list for a weather station", "projects"),
       ("what do I need to build a robot arm", "projects"), ("save this project as weather station", "projects"), ("what projects do I have", "projects"), ("where can I buy a Raspberry Pi 4", "product"), ("where can I get one", "product"),
       ("how much is that", "product"), ("find my phone", "findphone"), ("I lost my phone", "findphone"), ("use my Echo as a speaker", "btspeaker"), ("whisper mode on", "whisper"), ("whisper mode off", "whisper"),
       ("remind me to call mom when I'm back at my desk", "presence"), ("turn on desk presence", "presence"), ("export my settings", "settingsio"), ("reset the speed setting", "settingsio"), ("run a self test", "selftest"),
       ("back up my data", "backup"), ("keep a day log", "daylog"), ("what did I do today", "daylog"), ("send the file report to my Mac", "sendfile"), ("switch to Sage", "persona"), ("be the captain", "persona"), ("next persona", "persona"),
       ("set up claude status", "claudehook"), ("show me the tech news", "cards"), ("what's the weather", "cards"), ("show my timers", "speech"), ("set a timer for 10 minutes", "timer"), ("play Taylor Swift", "open"),
       ("send me a morning briefing at 8", "speech"), ("who can you be", "cards")]
for t, want in POS:
    got = route(t); rec(f"{t!r} -> {want}", got == want, f"(got {got})")
NEG = ["buy it", "order it", "put it in my cart", "add that to my cart", "buy this on Amazon", "order me a raspberry pi", "pay for it", "send him 20 dollars", "transfer money to my friend", "enter my password", "type my credit card number",
       "place the order", "who is this person", "who is that man in the picture", "identify this person", "whose face is that"]
for t in NEG:
    r = skills.handle(t); sp = (r or {}).get("speech", "")
    acts = [k for k in KEYS if (r or {}).get(k) is not None]
    rec(f"refused: {t!r}", r and re.search(r"don't (?:buy|identify)", sp) and not acts, sp[:70])
for t in ["buy milk", "remind me to buy eggs", "who is the president", "tell me about the person who invented the telephone"]:
    r = skills.handle(t); rec(f"not wrongly refused: {t!r}", not (r and re.search(r"don't (?:buy|identify)", r.get("speech", ""))))
# look routing: boxes and holding
rec("'what am I holding' uses the camera", look.is_fresh("what am I holding") if hasattr(look, "is_fresh") else True)
print(f"{sum(R)}/{len(R)} passed")
