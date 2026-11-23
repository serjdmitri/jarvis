"""Structured intents: the brain says WHAT the user wants as one allowlisted name + a few plain fields; this code decides what runs.
The brain never supplies commands, URLs for actions it was not asked for, or shell text. 'send' intents only run if the user's own words asked to send/text/share."""
import re
import urllib.parse

from . import maps

ALLOWED = ("show_map", "directions", "search_web", "show_images", "show_video", "show_3d", "send_to_mac", "send_to_phone", "open_app", "answer")

PROMPT = (
    " ROUTING: for every request end your reply with ONE JSON line {\"intent\":{\"name\":...,...}} (you may combine it with show/open keys only when told above). "
    "Choose exactly one name. Allowed intents, with examples:\n"
    "show_map {query}: put a place or a kind of place on the in-screen map card. 'Show Starbucks on the map' -> {\"intent\":{\"name\":\"show_map\",\"query\":\"Starbucks\"}}. "
    "'Show it on the map' (after Eiffel Tower was mentioned) -> {\"intent\":{\"name\":\"show_map\",\"query\":\"Eiffel Tower, Paris\"}}. Also: put it on the map, map it, where is X. "
    "If the place is truly unknown, ask 'Which place?' and use answer.\n"
    "directions {to, mode?}: route and travel time. 'Directions to the airport' -> {\"intent\":{\"name\":\"directions\",\"to\":\"the airport\"}}; 'take me to Central Park, walking' -> {\"intent\":{\"name\":\"directions\",\"to\":\"Central Park\",\"mode\":\"foot\"}}.\n"
    "search_web {query}: a FACT question that needs a web lookup (news, scores, prices, opening hours). Do the lookup yourself with WebSearch, answer in a sentence or two, and report this intent as your LAST line; the computer opens no browser for it. "
    "'Who won the match last night' -> {\"intent\":{\"name\":\"search_web\",\"query\":\"match result last night\"}}. NEVER use it, or any web search or Google page, for a 'show me' request.\n"
    "show_images {query}: pictures. 'Show me a red panda' -> {\"intent\":{\"name\":\"show_images\",\"query\":\"red panda\"}}; 'what does a tapir look like' -> same with 'tapir'.\n"
    "show_video {query}: a video. 'Show me how to tie a tie' -> {\"intent\":{\"name\":\"show_video\",\"query\":\"how to tie a tie\"}}; 'play the Dune trailer' -> {\"intent\":{\"name\":\"show_video\",\"query\":\"Dune trailer\"}}.\n"
    "show_3d {subject, scene}: a 3D model the user can turn and click. ONLY when they say 3D, model or hologram. 'Show me a V8 engine in 3D' -> {\"intent\":{\"name\":\"show_3d\",\"subject\":\"V8 engine\",\"scene\":{\"title\":\"V8 engine\",\"unit\":\"cm\",\"parts\":[...]}}}; "
    "'how does a jet engine work, show it in 3D' -> same with the jet engine's parts. The scene follows the spec you are given when asked to build one; 'show it on the map' and 'send it to my Mac' are NEVER show_3d.\n"
    "send_to_mac / send_to_phone {what: \"current\"}: ONLY when the user says send, text or share, to their Mac or phone. 'Send that to my Mac' -> {\"intent\":{\"name\":\"send_to_mac\",\"what\":\"current\"}}; "
    "'text me the address' -> {\"intent\":{\"name\":\"send_to_phone\",\"what\":\"current\"}}. 'Show', 'put on the map', 'map it' NEVER mean send.\n"
    "open_app {app}: open an app or website. 'Open the terminal' -> {\"intent\":{\"name\":\"open_app\",\"app\":\"terminal\"}}; 'open YouTube' -> {\"intent\":{\"name\":\"open_app\",\"app\":\"youtube\"}}.\n"
    "answer: just talk. 'What is two plus two' or 'tell me a joke' -> {\"intent\":{\"name\":\"answer\"}}. If you need a missing detail from the user, ask ONE short question and use answer; do not add any closing line. "
    "When the answer is about a place, person, thing, animal, food, event or news item and a picture would help, ALSO add a show_images intent via {\"intent\":{\"name\":\"show_images\",\"query\":\"...\"}} instead of answer.")


def validate(obj, user_text=""):
    """-> {"name", ...plain fields} or None. Unknown names and sends the user did not ask for are dropped."""
    if not isinstance(obj, dict):
        return None
    n = str(obj.get("name", "")).strip().lower()
    if n not in ALLOWED:
        return None
    clean = {"name": n}
    if n == "show_3d":
        if not re.search(r"\b(3 ?d|three ?d|three dimensional|model|hologram|holographic)\b", user_text or "", re.I) or re.search(r"\b(on the map|send|text me)\b", user_text or "", re.I):
            return None
        clean["subject"] = re.sub(r"\s+", " ", str(obj.get("subject") or "")).strip()[:80]
        if isinstance(obj.get("scene"), dict):
            clean["scene"] = obj["scene"]                       # validated (clamped, capped, cleaned) by holo.validate_scene before anything is drawn
        return clean
    for k in ("query", "to", "mode", "app", "what", "place"):
        if obj.get(k):
            clean[k] = re.sub(r"\s+", " ", str(obj[k])).strip()[:120]
    if n in ("show_images", "show_video"):                      # "auto visuals" off: pictures and videos only when asked for
        from . import prefs
        if not prefs.get("auto_visuals") and not re.search(r"\b(show|picture|pictures|pics?|photos?|images?|look like|looks like|video|videos|watch|play|trailer|see)\b", user_text or "", re.I):
            return None
    if n in ("send_to_mac", "send_to_phone") and not re.search(r"\b(send|text|share|message|forward)\b", user_text or "", re.I):
        return None
    if n == "send_to_mac" and not re.search(r"\bmac(?:book)?\b", user_text or "", re.I):
        return None
    return clean


def google(q):
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(q)


def run(intent):
    """-> a local-skill style result dict ({"speech","open","cards","macsend","phonesend","expect"}) or None (answer)."""
    n = intent["name"]
    if n == "answer":
        return None
    if n == "show_3d":
        from . import holo
        return holo.run_intent(intent)
    if n in ("show_map", "directions") and maps.needs_confirm(maps.my_location()):
        me = maps.my_location()
        return {"speech": f"I only have a rough guess from your internet address: {me['label']}. Is that where you are? Say yes, or tell me where you are.", "expect": "confirmip"}
    if n == "show_map":
        q = intent.get("query") or intent.get("place")
        if not q:
            return {"speech": "Which place should I show?", "expect": "map"}
        maps.LAST["q"] = q
        spec, speech = maps.places_spec(q)
        if not spec:
            spec, speech = maps.place_map_spec(q)
        if not spec:
            return {"speech": speech}
        out = {"speech": speech, "open": {"action": "show_map", "id": spec["id"]}}
        if spec.get("places"):
            out["cards"] = [{"type": "list", "title": spec["title"], "items": [f"{p['name']}  ({maps.fmt_dist(p['dist_m'])})" for p in spec["places"][:8]]}]
        return out
    if n == "directions":
        if not intent.get("to"):
            return {"speech": "Where to?", "expect": "directions"}
        maps.LAST["q"] = intent["to"]
        spec, speech = maps.directions_spec(intent["to"], {"walk": "foot", "walking": "foot", "foot": "foot", "bike": "bike", "cycle": "bike"}.get(intent.get("mode", "")))
        return {"speech": speech, "open": {"action": "show_map", "id": spec["id"]}} if spec else {"speech": speech}
    if n == "search_web":
        return None                                   # the brain already looked it up with its own tools; nothing is opened (a Google tab is never a fallback)
    if n == "show_images":
        from . import visuals
        c = visuals.gallery_card(intent.get("query", ""))
        return {"speech": "", "cards": [c]} if c else None
    if n == "show_video":
        from . import video
        return {"speech": "", "video": {"query": intent.get("query", "")}} if intent.get("query") else None
    if n == "send_to_mac":
        return {"speech": "", "macsend": "current"}
    if n == "send_to_phone":
        return {"speech": "", "phonesend": {"title": "From Jarvis", "message": "", "link": None, "current": True}}
    if n == "open_app":
        a = intent.get("app", "")
        return {"speech": "", "open": {"app": a}} if a else None
    return None
