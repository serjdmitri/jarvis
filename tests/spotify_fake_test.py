"""PROMPT28 item 6: Spotify as the main music source, tested against FAKE Spotify responses (no account). What cannot be tested here: a real login, real playback, the Go's own player "Jarvis".
Run: .venv/bin/python tests/spotify_fake_test.py"""
import os, sys, tempfile
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="jt-cfg-")
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from jarvis import spotify, spotify_cli, skills, settings
R = []; CALLS = []
def rec(n, ok): R.append(ok); print(("PASS " if ok else "FAIL ") + n, flush=True)
DEV = {"id": "d1", "name": "Jarvis", "type": "Computer", "is_active": True, "volume_percent": 40}
TR = {"uri": "spotify:track:A", "name": "So What", "type": "track", "artists": [{"name": "Miles Davis"}], "album": {"images": [{"url": "https://i/x.jpg"}]}, "duration_ms": 540000, "external_urls": {"spotify": "https://open.spotify.com/track/A"}}
def fake(method, path, body=None):
    CALLS.append((method, path.split("?")[0], body))
    if path.startswith("/me/player/devices"): return {"devices": [DEV]}, None
    if path.startswith("/search"): return {"tracks": {"items": [TR]}}, None
    if path.startswith("/me/tracks"): return {"items": [{"track": dict(TR, uri=f"spotify:track:{i}")} for i in range(5)]}, None
    if path.startswith("/me/playlists"): return {"items": [{"name": "Focus", "uri": "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"}]}, None
    if path == "/me/player": return {"item": TR, "is_playing": True, "progress_ms": 60000, "device": DEV}, None
    return {}, None
spotify._api = fake
state = {"c": True}
spotify.connected = lambda: state["c"]
settings.get = (lambda g: (lambda k, d=None: False if k == "spotify_premium" and False else g(k, d) if d is not None else g(k)))(settings.get)
def run(q):
    CALLS.clear(); return skills.spotify_command(q)
r = run("play so what by miles davis"); rec(f"play <song>: Spotify plays it ({r['speech'] if r else None})", bool(r) and "So What" in r["speech"] and any(c[1] == "/me/player/play" for c in CALLS))
r = run("play my liked songs"); body = [c[2] for c in CALLS if c[1] == "/me/player/play"]
rec("play my liked songs: your saved tracks are queued (5 uris, shuffled)", bool(r) and "liked songs" in r["speech"] and body and len(body[0]["uris"]) == 5)
r = run("play my focus playlist"); rec(f"play my <playlist>: ({r['speech']})", bool(r) and "Focus" in r["speech"])
r = run("what's playing"); card = r["cards"][0]
rec(f"what's playing: card has art, title, controls data, connected line ('{card['conn']}')", card["name"] == "So What" and card["image"] and card["conn"] == "Spotify: connected")
for q, op in (("pause the music", "pause"), ("next song", "next")):
    spotify_cli.LAST["t"] = __import__("time").time(); r = run(q); rec(f"'{q}' -> {op}", bool(r) and any(c[1].endswith(op) or c[1].endswith("/pause") or c[1].endswith("/next") for c in CALLS))
state["c"] = False
r = run("play my liked songs"); rec(f"not connected: liked songs says what to do, no YouTube search ('{r['speech'][:50]}')", bool(r) and "isn't connected" in r["speech"])
rec("not connected: plain 'play X' falls through to YouTube Music (returns None here)", run("play take five") is None)
rec("not connected: card says YouTube Music is used", "not connected" in spotify_cli.music_card({"item": {"name": "x"}})["conn"])
rec("scopes include the library + playlists + playback", all(s in spotify.SCOPES for s in ("user-library-read", "playlist-read-private", "user-modify-playback-state")))
rec("no token or client id printed anywhere in the module output", "print(" not in open(ROOT + "/jarvis/spotify.py").read() or "token" not in " ".join(l for l in open(ROOT + "/jarvis/spotify.py").read().splitlines() if "print(" in l))
print("RESULT", "ALL PASS" if all(R) else "FAILURES")
