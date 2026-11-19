"""Routing test: each phrase -> expected intent. Local rules answer the clear ones; everything else goes to the real brain with the intent prompt.
Run: .venv/bin/python tests/intent_phrases.py   (prints PASS or FAIL per phrase; needs the claude login for the brain ones)"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jarvis import brain, core, intents, maps, personas, skills

CASES = [
    ("Show Starbucks on the map", "show_map"), ("show it on the map", "show_map"), ("put it on the map", "show_map"), ("map it", "show_map"),
    ("show it on the Mac", "show_map"), ("where is the nearest pharmacy", "show_map"), ("find a coffee shop near me", "show_map"), ("where is the Eiffel Tower", "show_map"),
    ("directions to the airport", "directions"), ("take me to Central Park", "directions"), ("how do I get to the nearest bank", "directions"), ("how long to walk to Miami Beach", "directions"),
    ("show me pictures of a red panda", "show_images"), ("what does a tapir look like", "show_images"), ("show me a photo of the Golden Gate Bridge", "show_images"),
    ("show me how to tie a tie", "show_video"), ("play the Dune trailer", "show_video"), ("show me a video of a cat playing piano", "show_video"),
    ("send that to my Mac", "send_to_mac"), ("text me the address", "send_to_phone"), ("send it to my phone", "send_to_phone"),
    ("open the terminal", "open_app"), ("open YouTube", "open_app"),
    ("who won the football match last night", "search_web"), ("what's the latest news about the Mars mission", "search_web"),
    ("what is two plus two", "answer"), ("tell me a joke", "answer"), ("how are you", "answer"),
]
FIRST = {"show_map": ("show_map",), "directions": ("show_map",)}


def local_intent(text):
    r = skills.handle(text, "jarvis")
    if not r:
        return None
    o = r.get("open") or {}
    if o.get("action") == "show_map":
        sp = maps.SPECS.get(o.get("id"), {})
        return "directions" if str(sp.get("title", "")).startswith("Route") else "show_map"
    if r.get("macsend") == "report_phone": return "send_to_phone"
    if r.get("macsend"): return "send_to_mac"
    if r.get("phonesend"): return "send_to_phone"
    if o.get("action") in ("open_app", "open_desktop") or (o.get("action") == "open_url"): return "open_app"
    if r.get("expect") == "map": return "show_map"
    if r.get("video"): return "show_video"
    extra = [k for k in r if k not in ("speech", "cards")]
    return "answer" if not extra else "local:" + ",".join(extra)


def brain_intent(b, text, ctx=""):
    reply = b.ask(ctx + text)
    return intents.validate(core.split_intent(reply), text), reply


if __name__ == "__main__":
    maps.LAST["q"] = "Eiffel Tower, Paris"          # as if the Eiffel Tower had just been mentioned
    b = brain.make_brain(personas.system_prompt("jarvis", True))
    b.warm()
    bad = 0
    for phrase, want in CASES:
        got, how = local_intent(phrase), "local"
        if got is None:
            i, reply = brain_intent(b, phrase, "[context: home area Miami. Earlier we talked about the Eiffel Tower in Paris.]\n")
            got, how = (i or {}).get("name", "NO-INTENT:" + reply[-60:].replace("\n", " ")), "brain"
            if want == "show_images" and got == "show_images": pass
        ok = got == want
        bad += not ok
        print(("PASS" if ok else "FAIL"), f"{phrase!r:52} want {want:14} got {got}  [{how}]", flush=True)
    print(f"\n{len(CASES) - bad}/{len(CASES)} passed")
    b.close()
