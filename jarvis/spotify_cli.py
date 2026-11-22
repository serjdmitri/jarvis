"""Spotify playback commands: auth, search, play, pause, resume, next, previous, status, volume, devices, device, logout.

Inspired by the idea of a tiny Spotify command line (no code taken). It sits on jarvis/spotify.py's PKCE login (no client secret; tokens in a mode-600 file outside git)
and plays on the most recent active Spotify device, or on the device you name ("the Go" = the player called Jarvis, "my Mac", "my phone").

Use it from a terminal:   python -m jarvis.spotify_cli play "miles davis so what"      (also: search, pause, resume, next, previous, status, devices, device NAME, volume 40, logout)
The assistant calls the same functions through structured actions and voice commands only; no shell text from the brain ever reaches this module.
Playing the first search result is the default for "play X". `search` returns a short list so a card can let you choose. Links: only open.spotify.com or spotify: URIs."""
import json
import re
import sys
import time
import urllib.parse

from . import settings, spotify

LAST = {"item": None, "t": 0.0}                # the last track/context seen: {"uri", "url", "name", "artist", "image", "kind"}
ID = r"[A-Za-z0-9]{22}"
KINDS = ("track", "album", "playlist", "artist", "episode", "show")


# ----------------------------------------------------------------------------- links and results
def parse_link(text):
    """An exact Spotify link or URI -> {"kind", "uri", "url"} or None. Only open.spotify.com (https) and spotify: URIs are accepted."""
    t = (text or "").strip()
    m = re.fullmatch(r"https://open\.spotify\.com/(?:intl-[a-z]{2}/)?(" + "|".join(KINDS) + r")/(" + ID + r")(?:\?[^\s]*)?", t) or re.fullmatch(r"spotify:(" + "|".join(KINDS) + r"):(" + ID + r")", t)
    if not m:
        return None
    return {"kind": m.group(1), "uri": f"spotify:{m.group(1)}:{m.group(2)}", "url": f"https://open.spotify.com/{m.group(1)}/{m.group(2)}"}


def _item(kind, o):
    if not o:
        return None
    img = ((o.get("images") or (o.get("album") or {}).get("images") or [{}]) or [{}])
    artists = ", ".join(a.get("name", "") for a in (o.get("artists") or []) if a.get("name")) or (o.get("owner") or {}).get("display_name") or ""
    uri = o.get("uri") or ""
    m = re.fullmatch(r"spotify:(\w+):(" + ID + ")", uri)
    return {"kind": kind, "name": (o.get("name") or "")[:90], "artist": artists[:90], "uri": uri, "image": (img[0] or {}).get("url"),
            "url": f"https://open.spotify.com/{m.group(1)}/{m.group(2)}" if m else None}


def search(query, kinds=("track", "album", "playlist"), limit=4):
    """-> (list of items, error). A few of each kind, so a card can offer a choice."""
    q = urllib.parse.quote(query.strip()[:120])
    j, err = spotify._api("GET", f"/search?q={q}&type={','.join(kinds)}&limit={limit}")
    if err:
        return [], err
    out = []
    for k in kinds:
        for o in ((j or {}).get(k + "s") or {}).get("items") or []:
            it = _item(k, o)
            if it and it["uri"]:
                out.append(it)
    return out, None


# ----------------------------------------------------------------------------- devices
def devices():
    j, err = spotify._api("GET", "/me/player/devices")
    if err:
        return [], err
    return (j or {}).get("devices", []), None


def default_device_name():
    return settings.get("spotify_default_device") or spotify.DEVICE_NAME


def pick_device(devs, want=None):
    """The device to play on: the one you named, else the active one, else your default, else the Go ("Jarvis"), else the first."""
    w = (want or "").lower()
    if w:
        alias = {"go": [spotify.DEVICE_NAME.lower()], "jarvis": [spotify.DEVICE_NAME.lower()], "surface": [spotify.DEVICE_NAME.lower()],
                 "mac": ["mac", "macbook", "imac"], "computer": ["mac", "macbook", "imac", "pc"], "phone": ["iphone", "phone", "android", "galaxy", "pixel"]}.get(w, [w])
        for d in devs:
            n = (d.get("name") or "").lower()
            if any(a in n for a in alias) or (w == "phone" and d.get("type") == "Smartphone") or (w in ("mac", "computer") and d.get("type") == "Computer" and spotify.DEVICE_NAME.lower() not in n):
                return d
        return None
    for d in devs:
        if d.get("is_active"):
            return d
    for d in devs:
        if (d.get("name") or "").lower() == default_device_name().lower():
            return d
    return devs[0] if devs else None


def _target(want=None):
    devs, err = devices()
    if err:
        return None, err
    d = pick_device(devs, want)
    if d:
        return d, None
    if want:
        return None, f"I can't see a Spotify player called {want}. Open Spotify there and try again."
    return None, spotify.explain("no_active_device")


def set_default_device(name):
    settings.update(spotify_default_device=name)


# ----------------------------------------------------------------------------- playback
def _q(device):
    return "?device_id=" + urllib.parse.quote(device["id"]) if device else ""


def play(target=None, device=None):
    """target: a spotify link/URI, a search words string, or None (resume). -> {"ok", "message", "item"}."""
    dev, err = _target(device)
    if err:
        return {"ok": False, "message": err}
    if not target:
        _, err = spotify._api("PUT", "/me/player/play" + _q(dev), {})
        return {"ok": not err, "message": err or "Playing.", "item": LAST["item"]}
    link = parse_link(target) or (parse_link(target.split("?")[0]) if target.startswith("http") else None)
    if not link and re.match(r"(?:https?://|spotify:)", target.strip(), re.I):
        return {"ok": False, "message": "I only play open.spotify.com links and spotify: addresses."}
    if link:
        it = {"kind": link["kind"], "uri": link["uri"], "url": link["url"], "name": "", "artist": "", "image": None}
    else:
        found, err = search(target, ("track",), 1)
        if err:
            return {"ok": False, "message": err}
        if not found:
            return {"ok": False, "message": f"I couldn't find {target[:40]} on Spotify."}
        it = found[0]
    body = {"uris": [it["uri"]]} if it["kind"] in ("track", "episode") else {"context_uri": it["uri"]}
    _, err = spotify._api("PUT", "/me/player/play" + _q(dev), body)
    if err:
        return {"ok": False, "message": err}
    LAST.update(item=it, t=time.time())
    return {"ok": True, "message": ("Playing " + it["name"] + (" by " + it["artist"] if it["artist"] else "") + ".") if it["name"] else "Playing that link.", "item": it, "device": dev.get("name")}


def play_playlist(name, device=None):
    """"play my <name> playlist": your own playlists first (needs the playlist scope: log in again once), else the best public match."""
    j, err = spotify._api("GET", "/me/playlists?limit=50")
    if not err:
        words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in ("my", "the", "playlist")]
        for o in (j or {}).get("items") or []:
            if o and words and all(w in (o.get("name") or "").lower() for w in words):
                r = play(o["uri"], device)
                if r.get("ok"):
                    r["message"] = f"Playing your {o['name']} playlist."
                return r
    found, err2 = search(name + " playlist", ("playlist",), 1)
    if found:
        r = play(found[0]["uri"], device)
        if r.get("ok"):
            r["message"] = f"Playing {found[0]['name']}."
        if err and r.get("ok"):
            r["message"] += " I couldn't see your own playlists yet: log in to Spotify again from the setup card to allow that."
        return r
    return {"ok": False, "message": err or err2 or f"I couldn't find a playlist called {name}."}


def _simple(method, path, device_name=None):
    dev, err = _target(device_name)
    if err:
        return {"ok": False, "message": err}
    _, err = spotify._api(method, path + _q(dev))
    return {"ok": not err, "message": err or "Done."}


def pause():
    r = _simple("PUT", "/me/player/pause")
    r["message"] = "Paused." if r["ok"] else r["message"]
    return r


def resume():
    r = _simple("PUT", "/me/player/play")
    r["message"] = "Playing." if r["ok"] else r["message"]
    return r


def next_track():
    r = _simple("POST", "/me/player/next")
    r["message"] = "Next." if r["ok"] else r["message"]
    return r


def previous_track():
    r = _simple("POST", "/me/player/previous")
    r["message"] = "Previous." if r["ok"] else r["message"]
    return r


def set_volume(pct):
    pct = max(0, min(100, int(pct)))
    dev, err = _target()
    if err:
        return {"ok": False, "message": err}
    _, err = spotify._api("PUT", f"/me/player/volume?volume_percent={pct}&device_id=" + urllib.parse.quote(dev["id"]))
    return {"ok": not err, "message": err or f"Volume {pct} percent.", "volume": pct}


def change_volume(delta):
    st = status()
    cur = (st.get("device") or {}).get("volume") if st.get("ok") else None
    return set_volume((cur if cur is not None else 50) + delta)


def status():
    """-> {"ok", "playing", "item": {...}, "progress", "duration", "device": {"name", "volume"}} (Spotify's /me/player)."""
    j, err = spotify._api("GET", "/me/player")
    if err:
        return {"ok": False, "message": err}
    if not j or not j.get("item"):
        return {"ok": True, "playing": False, "item": None, "message": "Nothing is playing on Spotify."}
    it = _item("track" if (j["item"].get("type") or "track") == "track" else "episode", j["item"])
    LAST.update(item=it, t=time.time())
    d = j.get("device") or {}
    return {"ok": True, "playing": bool(j.get("is_playing")), "item": it, "progress": (j.get("progress_ms") or 0) / 1000.0, "duration": (j["item"].get("duration_ms") or 0) / 1000.0,
            "device": {"name": d.get("name"), "volume": d.get("volume_percent")}, "message": f"{it['name']} by {it['artist']}." if it["artist"] else it["name"]}


def logout():
    import os
    try:
        os.unlink(os.path.join(spotify._dir(), "spotify_tokens.json"))
    except OSError:
        pass
    return {"ok": True, "message": "Logged out of Spotify. The Client ID is kept."}


def now_url():
    """The open.spotify.com link of the last track seen (for "send this song to my Mac"), if it is recent."""
    it = LAST["item"]
    return it["url"] if it and it.get("url") and time.time() - LAST["t"] < 6 * 3600 else None


# ----------------------------------------------------------------------------- voice phrases
DEV = r"(?:the go|my go|the surface|jarvis|my mac(?:book)?|the mac|my computer|my laptop|my phone|my iphone|the phone)"


def _dev_word(s):
    s = (s or "").lower()
    return "go" if re.search(r"\b(?:go|surface|jarvis)\b", s) else "mac" if re.search(r"\b(?:mac|macbook|computer|laptop)\b", s) else "phone" if re.search(r"\b(?:phone|iphone)\b", s) else None


def parse(text):
    """-> {"op": ..., ...} for a Spotify voice command, else None. Only phrases about Spotify or about a track/playlist/link; plain "play X" is returned too
    (the caller decides whether Spotify or the YouTube fallback plays it)."""
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    if re.search(r"\b(?:video|trailer|movie|film|clip|youtube|how to|tutorial|episode|podcast|news)\b", low) and "spotify" not in low:
        return None                                                  # those are for the video path, not for music
    link = parse_link(text.strip())
    if link or re.search(r"https://open\.spotify\.com/\S+|spotify:\w+:" + ID, text):
        m = re.search(r"https://open\.spotify\.com/\S+|spotify:\w+:" + ID, text)
        return {"op": "play", "target": m.group(0)}
    m = re.fullmatch(r"(?:please )?play (?:it |this |that |the music |music )?on (" + DEV + ")", low)
    if m:
        return {"op": "transfer", "device": _dev_word(m.group(1))}
    m = re.fullmatch(r"(?:please )?play (?:my |the )(.+?) playlist(?: on spotify)?", low)
    if m:
        return {"op": "playlist", "name": m.group(1)}
    m = re.fullmatch(r"(?:please )?(?:play|put on|start) (.+?)(?: on spotify)?(?: on (" + DEV + "))?", low)
    if m and not re.fullmatch(r"(?:it|that|this|some music|music|the music|something|a song|the song|again|the video|a video|video|my music)", m.group(1)):
        return {"op": "play", "target": m.group(1), "device": _dev_word(m.group(2)), "explicit": "spotify" in low}
    m = re.fullmatch(r"(?:please )?(?:search|look)(?: for| up)? (.+?) on spotify|(?:please )?(?:search spotify for|spotify search) (.+)", low)
    if m:
        return {"op": "search", "query": m.group(1) or m.group(2)}
    if re.fullmatch(r"(?:please )?(?:show|list|what are)(?: me)?(?: my| the)? (?:spotify )?(?:devices|players|speakers)(?: on spotify)?|which (?:device|speaker) (?:is spotify|am i) (?:playing|using)(?: on)?", low) and "spotify" in low or low in ("spotify devices", "show spotify devices"):
        return {"op": "devices"}
    if re.fullmatch(r"(?:please )?(?:make|set) (?:the go|jarvis|the surface) (?:my |the )?default(?: (?:player|speaker|device))?(?: for spotify)?", low):
        return {"op": "default_go"}
    if re.fullmatch(r"(?:please )?(?:log ?out of|disconnect|unlink) spotify", low):
        return {"op": "logout"}
    if re.fullmatch(r"(?:what(?:'s| is) )?(?:currently )?playing(?: now| right now)?|what(?:'s| is) (?:this|that) (?:song|track)|what song is (?:this|that|playing)|who sings this|show (?:the )?music(?: card| player)?|now playing", low):
        return {"op": "status"}
    return None


def parse_control(text):
    """Pause/resume/next/previous/volume, only used when Spotify is what is playing."""
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    table = [("pause", r"(?:please )?(?:pause|stop)(?: the)?(?: music| song| spotify)?"), ("resume", r"(?:please )?(?:resume|continue|unpause|keep playing)(?: the)?(?: music| song| spotify)?|play"),
             ("next", r"(?:please )?(?:next|skip)(?: the)?(?: song| track)?(?: please)?"), ("previous", r"(?:please )?(?:previous|go back|last)(?: song| track)?|play the previous (?:song|track)")]
    for op, pat in table:
        if re.fullmatch(pat, low):
            return {"op": op}
    m = re.fullmatch(r"(?:please )?(?:turn |make )?(?:the )?(?:music |spotify )?(?:volume )?(up|down|louder|quieter|softer)(?: a (?:bit|little))?", low)
    if m and re.search(r"music|spotify", low):
        return {"op": "volume", "delta": 10 if m.group(1) in ("up", "louder") else -10}
    m = re.fullmatch(r"(?:please )?set (?:the )?(?:music |spotify )?volume to (\d{1,3})(?: percent)?", low)
    if m and re.search(r"music|spotify", low):
        return {"op": "volume", "set": int(m.group(1))}
    return None


# ----------------------------------------------------------------------------- cards
def music_card(st):
    it = st.get("item") or {}
    return {"type": "music", "gid": "music", "title": "MUSIC", "name": it.get("name", ""), "artist": it.get("artist", ""), "wide": 340, "image": it.get("image"), "playing": bool(st.get("playing")),
            "progress": st.get("progress", 0), "duration": st.get("duration", 0), "device": (st.get("device") or {}).get("name", ""), "volume": (st.get("device") or {}).get("volume"), "url": it.get("url")}


def results_card(query, items):
    steps = []
    for n, it in enumerate(items, 1):
        steps.append({"n": n, "text": f"{it['kind'].upper()}  {it['name']}" + (f"  ·  {it['artist']}" if it["artist"] else ""),
                      "buttons": [{"label": "PLAY", "action": "spotify_play", "payload": {"uri": it["uri"]}}]})
    return {"type": "guide", "gid": "spotify-results", "title": "SPOTIFY: " + query[:30].upper(), "steps": steps or [{"n": 1, "text": "Nothing found."}], "buttons": []}


def devices_card(devs):
    default = default_device_name().lower()
    steps = []
    for n, d in enumerate(devs, 1):
        mark = ("  (playing)" if d.get("is_active") else "") + ("  (default)" if (d.get("name") or "").lower() == default else "")
        steps.append({"n": n, "text": f"{d.get('name')}  ·  {d.get('type')}{mark}", "buttons": [
            {"label": "PLAY HERE", "action": "spotify_device", "payload": {"id": d["id"], "name": d.get("name")}},
            {"label": "MAKE DEFAULT", "action": "spotify_device", "payload": {"id": d["id"], "name": d.get("name"), "default": True}}]})
    go = not any((d.get("name") or "") == spotify.DEVICE_NAME for d in devs)
    if go:
        steps.append({"n": len(steps) + 1, "text": f'No player called "{spotify.DEVICE_NAME}" yet: the Go\'s own Spotify player is not running. See the setup card.', "buttons": [{"label": "SETUP", "action": "spotify_setup", "payload": {}}]})
    return {"type": "guide", "gid": "spotify-devices", "title": "SPOTIFY DEVICES", "steps": steps or [{"n": 1, "text": "No Spotify players are open right now. Open Spotify on a device."}], "buttons": []}


# ----------------------------------------------------------------------------- one entry point for the assistant
def run(cmd):
    """Execute a parse()/parse_control() result -> {"speech", "cards"?, "ok"}. Never raises."""
    try:
        op = cmd["op"]
        if op == "play":
            r = play(cmd["target"], cmd.get("device"))
        elif op == "playlist":
            r = play_playlist(cmd["name"])
        elif op == "transfer":
            devs, err = devices()
            d = None if err else pick_device(devs, cmd.get("device"))
            if err or not d:
                return {"ok": False, "speech": err or "I can't see that player. Open Spotify there first."}
            _, err = spotify._api("PUT", "/me/player", {"device_ids": [d["id"]], "play": True})
            return {"ok": not err, "speech": err or f"Playing on {d.get('name')}."}
        elif op == "search":
            items, err = search(cmd["query"])
            return {"ok": not err, "speech": err or ("Here is what I found." if items else "I found nothing."), "cards": [] if err else [results_card(cmd["query"], items)]}
        elif op == "devices":
            devs, err = devices()
            return {"ok": not err, "speech": err or (f"I can see {len(devs)} player{'s' if len(devs) != 1 else ''}." if devs else "No Spotify players are open."), "cards": [] if err else [devices_card(devs)]}
        elif op == "default_go":
            set_default_device(spotify.DEVICE_NAME)
            return {"ok": True, "speech": "The Go is now the default Spotify player."}
        elif op == "logout":
            r = logout()
        elif op == "status":
            st = status()
            return {"ok": st.get("ok"), "speech": st.get("message", ""), "cards": [music_card(st)] if st.get("item") else []}
        elif op in ("pause", "resume", "next", "previous"):
            r = {"pause": pause, "resume": resume, "next": next_track, "previous": previous_track}[op]()
        elif op == "volume":
            r = set_volume(cmd["set"]) if "set" in cmd else change_volume(cmd["delta"])
        else:
            return {"ok": False, "speech": ""}
        out = {"ok": r["ok"], "speech": r["message"]}
        if r["ok"] and r.get("item"):
            out["cards"] = [music_card({"item": r["item"], "playing": True, "progress": 0, "duration": 0, "device": {"name": r.get("device")}})]
        return out
    except Exception as e:                                             # a network or parsing problem is a spoken sentence, not a crash
        return {"ok": False, "speech": "Spotify did not answer (" + type(e).__name__ + ")."}


def main(argv):
    if not argv:
        print(__doc__)
        return 0
    c, rest = argv[0], " ".join(argv[1:])
    if c == "auth":
        if not spotify.have_client_id():
            print("Save your Client ID first (the Spotify setup card on the Go, or write it to ~/.config/jarvis/spotify_client_id).")
            return 1
        print("Open this link, log in to Spotify and press Agree:\n" + spotify.auth_url())
        done = []
        spotify.listen_for_callback(lambda ok, msg: done.append((ok, msg)), 180)
        t0 = time.time()
        while not done and time.time() - t0 < 185:
            time.sleep(0.5)
        print(done[0][1] if done else "No answer.")
        return 0 if done and done[0][0] else 1
    if c == "search":
        items, err = search(rest)
        print(err or "\n".join(f"{i}. [{it['kind']}] {it['name']} - {it['artist']}  {it['url']}" for i, it in enumerate(items, 1)) or "nothing found")
        return 0 if not err else 1
    cmd = {"play": {"op": "play", "target": rest} if rest else {"op": "resume"}, "pause": {"op": "pause"}, "resume": {"op": "resume"}, "next": {"op": "next"}, "previous": {"op": "previous"},
           "status": {"op": "status"}, "devices": {"op": "devices"}, "logout": {"op": "logout"}}.get(c)
    if c == "volume" and rest.isdigit():
        cmd = {"op": "volume", "set": int(rest)}
    if c == "device" and rest:
        set_default_device(rest)
        print("Default device:", rest)
        return 0
    if not cmd:
        print("unknown command:", c)
        return 2
    r = run(cmd)
    print(r["speech"])
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
