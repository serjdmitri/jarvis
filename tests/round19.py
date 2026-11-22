"""Phrase and logic regression for the PROMPT19 features. Run: .venv/bin/python tests/round19.py   (uses throwaway settings and data folders; prints PASS/FAIL per check)."""
import os
import sys
import tempfile
import time

os.environ["JARVIS_DATA_DIR"] = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jarvis import buddy, moods, offline, personas, screenhelp, skills, spotify_cli  # noqa: E402

FAILS = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


def h(t, who="jarvis"):
    return skills.handle(t, who) or {}


# personas
check("persona: switch to the coach", h("switch to the coach").get("persona") == "coach")
check("persona: be the captain", h("be the captain").get("persona") == "captain")
check("persona: the mentor is Sage", h("switch to the mentor").get("persona") == "sage")
check("persona: unknown name is not a switch", not h("switch to the moon").get("persona"))
check("persona: five or more installed", len(personas.PERSONAS) >= 6)
# moods
st = moods.Stream()
out = "".join(st.feed(c) for c in ["[hap", "py] It's sunny. [ca", "lm] Bring a coat."]) + st.flush()
check("moods: tags split across chunks become markers", out.count("⁣") == 2 and "[" not in out)
check("moods: tag stripped from shown text", moods.strip("[Happy] hi [calm]there") == "hi there")
check("moods: sentence keeps its mood", moods.take("⁣eWow!") == ("Wow!", "excited"))
# reminders
r = buddy.parse_recurring("remind me every weekday at 9 to stand up")
check("buddy: every weekday at 9", bool(r) and r["rule"]["days"] == [0, 1, 2, 3, 4] and r["rule"]["at"] == "09:00" and r["label"] == "stand up")
r = buddy.parse_recurring("every 2 hours remind me to drink water")
check("buddy: every 2 hours", bool(r) and r["rule"] == {"kind": "interval", "every": 7200})
r = buddy.parse_recurring("every Sunday evening remind me to plan the week")
check("buddy: every Sunday evening", bool(r) and r["rule"]["days"] == [6] and r["rule"]["at"] == "18:00")
check("buddy: no passwords in a reminder", "passwords" in h("remind me every day to type my password 123456").get("speech", ""))
h("remind me every day at 7 to run")
check("buddy: edit is not a new reminder", "now every day at 8" in h("move the run reminder to 8 am").get("speech", "") and len(buddy.list_recurring()) == 1)
check("buddy: pause and delete by voice", "Paused" in h("pause the run reminder").get("speech", "") and "Deleted" in h("delete the run reminder").get("speech", ""))
p = buddy.detect_promise("I need to send that form by Friday")
check("promise: detected with a due time", bool(p) and p["label"] == "Send that form" and p["due"])
check("promise: questions and chatter are not promises", not buddy.detect_promise("can I call mum tomorrow?") and not buddy.detect_promise("I'll be there at five"))
check("promise: nothing sensitive", not buddy.detect_promise("I need to pay with card 4111111111111111 tomorrow"))
item = buddy.add_promise("Call mum", time.time() - 5)
n = [len(buddy.due_promises(time.time() + d)) for d in (0, 1, 7300, 14700, 22000)]
check("promise: nudged now, not again at once, at 2 h and 4 h, never a 4th time", n == [1, 0, 1, 1, 0])
# spotify
check("spotify: play X", (spotify_cli.parse("play miles davis so what") or {}).get("op") == "play")
check("spotify: a video request is not music", spotify_cli.parse("play the dune trailer") is None)
check("spotify: exact link", (spotify_cli.parse("https://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl") or {}).get("op") == "play")
check("spotify: only open.spotify.com links", spotify_cli.parse_link("https://evil.example/track/11dFghVXANMlKmJXsNCbNl") is None)
check("spotify: device words", (spotify_cli.parse("play on my mac") or {}).get("device") == "mac")
# screen helpers
check("howto: needs a screen cue", screenhelp.parse_howto("how do I change the font size in this app") == "change the font size" and screenhelp.parse_howto("show me how to tie a tie") is None)
check("howto: next / stop", screenhelp.parse_control("next") == "next" and screenhelp.parse_control("never mind") == "stop" and screenhelp.parse_control("next song") is None)
check("howto: a whole-screen box is rejected", screenhelp.parse_step('{"say":"x","box":{"x":0,"y":0,"w":1,"h":1}}')["box"] is None)
check("circle: explicit phrases only", screenhelp.parse_circle("circle to ask") and not screenhelp.parse_circle("what is this"))
# orb
check("orb: move commands", h("move to the top").get("orb") == "top" and h("move left").get("orb") == "leftstep" and h("reset the orb position").get("orb") == "reset")
# offline
check("offline: small talk without the brain", "joke" not in offline.handle("hello")["speech"] and offline.chat("tell me a joke") is not None)
check("offline: said once, then short", offline.handle("what is the capital of Peru")["speech"].startswith("No internet") or offline.handle("x")["speech"].startswith("Still offline"))

print("\n%s" % ("ALL PASS" if not FAILS else "FAILED: " + ", ".join(FAILS)))
sys.exit(1 if FAILS else 0)
