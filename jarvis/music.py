"""Music through YouTube in the Jarvis frame (the Spotify version needs a Premium account and your own developer app: see the README).
"play some jazz", "play <song> by <artist>": a YouTube search in our own code (yt-dlp), the best match plays in a video window with autoplay.
Voice: pause, resume, next, previous, what's playing, stop the music, volume up/down (the system volume). While he listens or speaks the music is ducked and then brought back."""
import re

from . import video

STATE = {"active": False, "queue": [], "i": 0, "query": ""}


def search(query):
    res = video.search(query, n=8)
    return [r for r in res if (r.get("duration") or 0) >= 90 or not r.get("duration")] or res


def start(query):
    """-> (spoken, open action or None)."""
    res = search(query)
    if not res:
        return "I couldn't find any music for that.", None
    STATE.update(active=True, queue=res, i=0, query=query)
    return f"Playing {res[0]['title'][:70]}.", {"action": "open_url", "url": res[0]["url"], "label": res[0]["title"][:40]}


def step(delta):
    if not STATE["queue"]:
        return "Nothing is queued.", None
    STATE["i"] = (STATE["i"] + delta) % len(STATE["queue"])
    r = STATE["queue"][STATE["i"]]
    return f"{r['title'][:70]}.", {"action": "open_url", "url": r["url"], "label": r["title"][:40]}


def card():
    """The now-playing card for what the YouTube queue is on (same card as Spotify's: title, channel, play / pause / next / volume)."""
    r = STATE["queue"][STATE["i"]] if STATE["queue"] else {}
    return {"type": "music", "gid": "music", "wide": 340, "title": "MUSIC", "name": r.get("title", "")[:80], "artist": r.get("channel", ""), "image": None, "playing": True, "progress": 0, "duration": 0,
            "device": "YouTube Music", "volume": None, "yt": True}


def now_playing():
    if not STATE["active"] or not STATE["queue"]:
        return "Nothing is playing."
    r = STATE["queue"][STATE["i"]]
    return f"{r['title'][:80]}" + (f", from {r['channel'][:30]}." if r.get("channel") else ".")


def parse_command(text):
    low = text.lower().strip(" .?!")
    m = re.fullmatch(r"(?:please )?(?:play|put on|start|queue up) (?:me )?(?:some |a bit of |a little )(.+?)(?: music)?", low)
    if m and not re.search(r"\b(?:on youtube|video|videos|the first|the second|the third)\b", low):
        return {"kind": "play", "query": m.group(1).strip() + " music mix"}
    m = re.fullmatch(r"(?:please )?(?:play|put on) (.+?) by (.+)", low)
    if m:
        return {"kind": "play", "query": f"{m.group(1).strip()} {m.group(2).strip()}"}
    m = re.fullmatch(r"(?:please )?(?:play|put on) (?:the )?(?:song|track|album) (.+)", low)
    if m:
        return {"kind": "play", "query": m.group(1).strip()}
    m = re.fullmatch(r"(?:please )?(?:play|put on|start) (?:me )?(.{2,70}?)(?: on youtube(?: music)?)?", low)           # "play Taylor Swift", "play a song", "play something"
    if m and re.match(r"(?:please )?(?:put on|start)\b", low) and not re.search(r"\b(?:music|song|songs|track|tracks|album|playlist|radio|tunes?|mix|jazz|rock|pop|classical|hip hop|rap|blues|country|lofi|lo-fi)\b", low):
        m = None                                                          # "start a timer", "put on my coat", "start the car" are not music (only "play X" takes any name)
    if m:
        what = m.group(1).strip()
        if re.fullmatch(r"(?:some music|music|the music|something|a song|any song|a random song|something good|my music|songs)", what):
            return {"kind": "play", "query": "top songs playlist"}
        if not re.match(r"(?:it|that|this|these|those|again|another|something else|the video|a video|video|videos|the (?:first|second|third|next|previous|last)|(?:the |a )?(?:game|games|chess|cards|football|soccer|basketball|tennis|golf|piano|guitar|chords|sound|sounds|noise|role|dead|along|back|out|up|fair|nice|with)\b|next|previous|number|\d+)", what):
            return {"kind": "play", "query": what}
    if not STATE["active"]:
        return None
    if re.fullmatch(r"(?:please )?pause(?: the)?(?: music| song| video)?", low): return {"kind": "pause"}
    if re.fullmatch(r"(?:please )?(?:resume|continue|unpause|play)(?: the)?(?: music| song| video)?|(?:please )?keep playing", low): return {"kind": "resume"}
    if re.fullmatch(r"(?:please )?(?:next|skip)(?: the)?(?: song| track| one)?|(?:please )?play (?:the )?next (?:one|song|track)", low): return {"kind": "next"}
    if re.fullmatch(r"(?:please )?previous(?: song| track| one)?|(?:please )?go back (?:a |one )?(?:song|track)|(?:please )?play (?:the )?previous (?:one|song|track)", low): return {"kind": "prev"}
    if re.fullmatch(r"what(?:'s| is) (?:this song|playing|this|the song|this track)(?: called)?|what song is this|who sings this", low): return {"kind": "what"}
    if re.fullmatch(r"(?:please )?stop(?: the)?(?: music| song| playing)|(?:please )?turn (?:the )?music off", low): return {"kind": "stop"}
    return None
