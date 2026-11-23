"""A6: the Jarvis Spotify player service + setup step 6 + speakers + volume cap + voice, against FAKES (no account, player not installed here).
Run: .venv/bin/python tests/spotify_player_test.py"""
import os, sys, tempfile
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="jt-cfg-")
import os, sys, subprocess, types
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from jarvis import spotify, spotifyd, spotify_cli, settings, skills
R = []; CALLS = []
def rec(n, ok): R.append(ok); print(("PASS " if ok else "FAIL ") + n, flush=True)
# files
c, u = spotifyd.config_text(), spotifyd.unit_text("/usr/bin/spotifyd")
rec("config names the device exactly Jarvis, plays through PipeWire (pulse), holds no account details", 'device_name = "Jarvis"' in c and 'backend = "pulseaudio"' in c and not any(w in c.lower() for w in ("password", "username", "token", "secret")))
rec("unit: user service, restarts if it dies, starts at login", "Restart=always" in u and "WantedBy=default.target" in u and "--no-daemon" in u)
# step 6 states
spotify.connected = lambda: True
st = {"bin": None, "active": False}
spotifyd.binary = lambda: st["bin"]; spotifyd.active = lambda: st["active"]
started = []
def fstart(): st["bin"] = "/usr/bin/spotifyd"; st["active"] = True; started.append(1); return True, "ok"
spotifyd.start = fstart
dev = {"devices": []}
def fake(m, p, b=None): CALLS.append((m, p.split("?")[0], b)); return (dev, None) if p.startswith("/me/player/devices") else ({}, None)
spotify._api = fake
ok, msg = spotify.check(); rec(f"player not installed: says the one command ({msg[:60]}...)", not ok and "sudo pacman -S spotifyd" in msg)
st["bin"] = "/usr/bin/spotifyd"; spotify.time.sleep = lambda s: None
ok, msg = spotify.check(); rec("installed, not running: Jarvis starts it, then looks for the device", started and not ok and "tap Jarvis once" in msg)
dev["devices"] = [{"id": "J1", "name": "Jarvis", "is_active": False}]
ok, msg = spotify.check(); rec(f"device visible: playback is transferred to Jarvis ({msg})", ok and any(c[0] == "PUT" and c[1] == "/me/player" and c[2] == {"device_ids": ["J1"], "play": False} for c in CALLS))
settings.update(music_pref="youtube"); CALLS.clear()
ok, msg = spotify.play_test(); rec("test song: transfer first, then the song plays, and Spotify becomes the music source", ok and [c[1] for c in CALLS][-2:] == ["/me/player", "/me/player/play"] and settings.get("music_pref") == "spotify")
spotify.connected = lambda: False; ok, msg = spotify.check(); rec("not logged in: asks for the login step first", not ok and "login" in msg)
rec("403 explains the February 2026 rules in plain words", "Premium" in spotify.explain("", 403) and "User Management" in spotify.explain("", 403))
# volume cap, voice
settings.update(spotify_volume_max=70); sent = []
spotify_cli._target = lambda w=None: ({"id": "J1", "name": "Jarvis"}, None)
spotify._api = lambda m, p, b=None: (sent.append(p) or ({}, None))
r = spotify_cli.set_volume(95); rec(f"maximum volume 70: asking for 95 sets {r['volume']}", r["volume"] == 70 and "volume_percent=70" in sent[-1])
spotify.connected = lambda: True
for q, on in (("shuffle", True), ("turn shuffle off", False), ("shuffle the music", True)):
    cmd = spotify_cli.parse_control(q); rec(f"voice '{q}' -> shuffle {'on' if on else 'off'}", bool(cmd) and cmd["op"] == "shuffle" and cmd["on"] == on)
sent.clear(); out = spotify_cli.run({"op": "shuffle", "on": True}); rec("shuffle calls the API", out["ok"] and "shuffle?state=true" in sent[-1])
# speakers (fake wpctl)
fake_wp = "Audio\n ├─ Sinks:\n │  *   55. Built-in Audio Analog Stereo        [vol: 0.70]\n │      77. WH-1000XM4 bluez_output.xx        [vol: 0.50]\n ├─ Sources:\n"
real = subprocess.run
subprocess.run = lambda *a, **k: types.SimpleNamespace(stdout=fake_wp, returncode=0)
o = spotifyd.outputs(); subprocess.run = real
rec(f"speakers listed read-only: {[(x['name'][:12], x['kind'], x['default']) for x in o]}", len(o) == 2 and o[0]["default"] and o[1]["kind"] == "bluetooth")
rec("nothing in the module changes the default output or pairs a speaker", not any(w in open(ROOT + "/jarvis/spotifyd.py").read() for w in ("set-default", "bluetoothctl", "pair ")))
print("RESULT", "ALL PASS" if all(R) else "FAILURES"); sys.exit(0 if all(R) else 1)
