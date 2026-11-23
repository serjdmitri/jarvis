"""Connections: one place for every link Jarvis depends on, each in plain words with green / amber / red, the time of the last success, and a real TEST and a FIX.
Rows: Mac helper, Mac pairing, phone notifications, phone file link, phone camera (DroidCam), Spotify player, Wi-Fi, speakers, internet.
TEST really tests (a harmless test file to the phone link and wait for the phone to fetch it; a ping to the Mac helper; a frame from the phone camera; a silent Spotify device check; a speaker check).
FIX does what can be done by itself (reconnect, restart the helper link or the player service, rescan Wi-Fi) and says plainly what only you can do ("open DroidCam on the phone", "wake the Mac").
Nothing here prints or logs a topic, pairing code, key, token or one-time link."""
import json
import os
import re
import subprocess
import threading
import time

from . import config, settings

LAST_OK = {}                                    # row id -> time of the last success
RESULT = {}                                     # row id -> {"state", "text", "t"} from the last TEST (shown until the next check)
_lock = threading.Lock()
ORDER = ("mac_helper", "mac_pair", "phone_notify", "phone_link", "phone_cam", "spotify", "wifi", "speakers", "internet")
NAMES = {"mac_helper": "Mac helper", "mac_pair": "Mac pairing", "phone_notify": "Phone notifications", "phone_link": "Phone file link", "phone_cam": "Phone camera (DroidCam)",
         "spotify": "Spotify player", "wifi": "Wi-Fi", "speakers": "Speakers", "internet": "Internet"}


def _ago(t):
    if not t:
        return "never"
    s = int(time.time() - t)
    return "just now" if s < 20 else f"{s // 60 or 1} min ago" if s < 3600 else f"{s // 3600} h ago" if s < 86400 else f"{s // 86400} d ago"


def _row(i, state, text, test=True, fix=True):
    if state == "green":
        LAST_OK[i] = time.time()
    return {"id": i, "name": NAMES[i], "state": state, "text": text, "last_ok": _ago(LAST_OK.get(i)), "test": test, "fix": fix}


def _wifi():
    """-> (connected bool, ssid or "") using whatever the system has (iwd or NetworkManager), read only."""
    for argv in (["iwctl", "station", "wlan0", "show"], ["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"]):
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        m = re.search(r"Connected network\s+(.+)", out) if argv[0] == "iwctl" else re.search(r"^yes:(.+)$", out, re.M)
        if m:
            return True, m.group(1).strip()
        if out.strip():
            return False, ""
    try:
        r = subprocess.run(["ip", "-4", "route", "get", "1.1.1.1"], capture_output=True, text=True, timeout=3).stdout
        return bool(r.strip()), ""
    except (OSError, subprocess.TimeoutExpired):
        return False, ""


def snapshot(quick=True):
    """-> [rows]. Cheap and read only: it never starts a test (that is the TEST button)."""
    from . import machelper, mac, net, phone, phonecam, spotify, spotifyd
    rows = []
    st = machelper.status(force=not quick)
    q = len(machelper._qload()) if hasattr(machelper, "_qload") else 0
    rows.append(_row("mac_helper", "green" if st in ("ready", "busy") else "amber" if st in ("asleep", "unset") else "red",
                     {"ready": "Mac helper: ready", "busy": "Mac helper: busy with a job", "asleep": "Mac asleep or not reachable: jobs wait" + (f" ({q} waiting)" if q else ""),
                      "unset": "Mac helper not set up yet: install it on the Mac (one command)"}.get(st, st)))
    rows.append(_row("mac_pair", "green" if mac.paired() else "amber", "Mac paired" if mac.paired() else "Not paired: say pair my Mac (the code shows once, only while pairing: do not photograph it)", test=False))
    have_topic = bool(phone.get_topic(create=False))
    rows.append(_row("phone_notify", "green" if have_topic else "amber", "Phone notifications are set up (ntfy)" if have_topic else "Not set up: the phone has not been given its address yet"))
    ip = None
    try:
        from . import voiceapi
        ip = voiceapi.lan_ip()
    except Exception:
        pass
    rows.append(_row("phone_link", "green" if ip else "red", f"File link ready on the home Wi-Fi: it works only while the phone is on the same Wi-Fi" if ip else "No home network address: connect to Wi-Fi first"))
    cam = phonecam.get_url()
    if not cam:
        rows.append(_row("phone_cam", "amber", "No phone camera address saved (Settings > Phone camera)", fix=False))
    else:
        live = phonecam.RELAY.running()
        rows.append(_row("phone_cam", "green" if live else "amber", "Phone camera is streaming" if live else "Phone camera idle: it starts when you open the camera. If it fails: is DroidCam open on the phone?"))
    sp = spotifyd.state()
    if not spotify.connected():
        rows.append(_row("spotify", "amber", "Spotify account not connected (Settings > Music > Set up Spotify). " + sp["plain"]))
    else:
        rows.append(_row("spotify", "green" if sp["active"] else "amber", sp["plain"] + (". Your account is connected." if sp["active"] else ". " + ("Press FIX to start it." if sp["installed"] else "Install it once: " + spotifyd.INSTALL_CMD))))
    ok, ssid = _wifi()
    rows.append(_row("wifi", "green" if ok else "red", ("Wi-Fi: connected" + (f" to {ssid}" if ssid else "")) if ok else "Wi-Fi: not connected. Open the Wi-Fi screen to choose a network"))
    outs = spotifyd.outputs()
    cur = next((o for o in outs if o["default"]), None)
    rows.append(_row("speakers", "green" if cur else "red", f"Speakers: {cur['name']}" + (" (Bluetooth)" if cur and cur["kind"] == "bluetooth" else "") if cur else "No audio output found"))
    on = net.online()
    rows.append(_row("internet", "green" if on else "red", "Internet: online" if on else "Internet: no connection. Local things still work: the clock, timers, local commands, saved maps"))
    for r in rows:                                                       # the last TEST result stays visible until the next one
        t = RESULT.get(r["id"])
        if t and time.time() - t["t"] < 30:                          # a TEST result is shown for 30 seconds, then the live state is shown again (it must never go stale)
            r["state"], r["text"] = t["state"], t["text"]
    return rows


def _set(i, state, text):
    with _lock:
        RESULT[i] = {"state": state, "text": text, "t": time.time()}
    if state == "green":
        LAST_OK[i] = time.time()
    return {"id": i, "state": state, "text": text}


def test(i, assistant=None):
    """A real test of one link. -> {"id","state","text"} (may take up to a minute for the phone)."""
    from . import fileshare, machelper, phone, phonecam, spotify, spotifyd
    if i == "mac_helper":
        c, r = machelper.request("GET", "/health", timeout=4)
        if c != 200:
            return _set(i, "amber", "The Mac did not answer: it may be asleep. Jobs wait until it is back.")
        d = os.path.join(str(config.ROOT), "shots", ".conn-test.txt")
        os.makedirs(os.path.dirname(d), exist_ok=True)
        open(d, "w").write("Jarvis connection test " + time.strftime("%H:%M:%S") + "\n")
        res = machelper.save_file(d, "jarvis-connection-test.txt")
        try:
            os.unlink(d)
        except OSError:
            pass
        return _set(i, "green" if res.get("ok") else "red", "The Mac helper answered and confirmed a small test file in Downloads/Jarvis." if res.get("ok") else "The Mac answered but did not confirm the file: " + str(res.get("why", ""))[:80])
    if i in ("phone_notify", "phone_link"):
        d = os.path.join(str(config.ROOT), "shots", "jarvis-connection-test.txt")
        os.makedirs(os.path.dirname(d), exist_ok=True)
        open(d, "w").write("Jarvis connection test: if you can read this in Files > Downloads, the phone link works.\n")
        r = phone.send_download(d, title="Jarvis connection test")
        if not r.get("ok"):
            return _set(i, "red", "Could not send the test: " + r["message"])
        end = time.time() + 60
        while time.time() < end:
            if fileshare.proof(r["sid"]).get("complete"):
                return _set(i, "green", "Your phone fetched the whole test file: notifications and the file link both work.")
            time.sleep(1)
        return _set(i, "amber", "Sent, not opened yet: the phone has not fetched the test file. Tap the notification (on the home Wi-Fi) and press TEST again.")
    if i == "phone_cam":
        try:
            p = phonecam.grab(timeout=6)
            ok = os.path.getsize(p) > 2000
            return _set(i, "green" if ok else "red", "Got a frame from the phone camera." if ok else "The phone camera sent an empty picture.")
        except Exception as e:
            return _set(i, "red", "Phone camera not reachable: is DroidCam open on the phone, on the same Wi-Fi? (" + str(e)[:60] + ")")
    if i == "spotify":
        if not spotify.connected():
            return _set(i, "amber", "Not logged in to Spotify yet (Settings > Music).")
        d, msg = spotify.find_device()
        return _set(i, "green" if d else "amber", "The Jarvis device is there and silent (nothing was played)." if d else msg)
    if i == "speakers":
        outs = spotifyd.outputs()
        cur = next((o for o in outs if o["default"]), None)
        if not cur:
            return _set(i, "red", "No audio output found.")
        if assistant is not None:
            assistant.say("Speaker check.")
        return _set(i, "green", f"Speaker check played on {cur['name']}.")
    if i == "wifi":
        ok, ssid = _wifi()
        return _set(i, "green" if ok else "red", "Wi-Fi is connected." if ok else "Wi-Fi is not connected.")
    if i == "internet":
        from . import net
        on = net.online(force=True)
        return _set(i, "green" if on else "red", "Internet answers." if on else "No internet.")
    return _set(i, "amber", "Nothing to test here.")


def fix(i):
    """What can be done by itself. -> {"id","state","text"}; the text says what only you can do."""
    from . import fileshare, machelper, phonecam, spotifyd
    if i == "mac_helper":
        for wait in (0, 2, 4):                                        # reconnect with a short back-off
            time.sleep(wait)
            if machelper.status(force=True) in ("ready", "busy"):
                try:
                    from . import vault
                    vault.flush()
                except Exception:
                    pass
                return _set(i, "green", "Reconnected to the Mac helper. Anything that waited is being sent.")
        return _set(i, "amber", "The Mac does not answer. Wake it (move its mouse or press a key); I cannot wake a sleeping Mac from here. If you want it to stay awake while you work, that is the Mac's Energy setting: your choice.")
    if i == "mac_pair":
        return _set(i, "amber", "Say pair my Mac to start pairing. The code appears once on this screen and is never saved or logged. Do not photograph it.")
    if i in ("phone_notify", "phone_link"):
        try:
            fileshare.stop_server()
            ip = fileshare.ensure_server()
            return _set(i, "green" if ip else "red", "Restarted the file link on the home Wi-Fi." if ip else "No home network address.")
        except fileshare.Refused as e:
            return _set(i, "red", str(e))
    if i == "phone_cam":
        try:
            phonecam.RELAY.release()
        except Exception:
            pass
        return _set(i, "amber", "Reset the camera connection. Now open DroidCam on the phone (same Wi-Fi) and press TEST. Only you can open the app.")
    if i == "spotify":
        if not spotifyd.binary():
            return _set(i, "amber", spotifyd.not_installed())
        ok, msg = spotifyd.restart() if spotifyd.active() else spotifyd.start()
        return _set(i, "green" if ok else "red", msg)
    if i == "wifi":
        try:
            subprocess.run(["iwctl", "station", "wlan0", "scan"], capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return _set(i, "amber", "Rescanned for networks. Choosing a network and typing its password is done on the Wi-Fi screen.")
    if i == "speakers":
        return _set(i, "amber", "I never change your output by myself. Pick the speaker in Settings > Music (Bluetooth speakers need pairing mode and your yes).")
    if i == "internet":
        return _set(i, "amber", "Check the Wi-Fi row first; if Wi-Fi is fine, the router or the provider is down.")
    return _set(i, "amber", "Nothing to fix here.")


def summary(rows=None):
    """One short spoken line for "check my connections"."""
    rows = rows or snapshot(quick=False)
    bad = [r for r in rows if r["state"] == "red"]
    mid = [r for r in rows if r["state"] == "amber"]
    if not bad and not mid:
        return "Everything is connected."
    parts = []
    if bad:
        parts.append(", ".join(r["name"] for r in bad[:3]) + (" are down" if len(bad) > 1 else " is down"))
    if mid:
        parts.append(", ".join(r["name"] for r in mid[:3]) + " need" + ("" if len(mid) > 1 else "s") + " a look")
    line = "; ".join(parts)
    return line[:1].upper() + line[1:] + ". Open Settings, Connections, for the details."


def card(rows=None):
    rows = rows or snapshot(quick=False)
    return {"type": "list", "title": "CONNECTIONS", "items": [f"{'●' if r['state'] == 'green' else '◐' if r['state'] == 'amber' else '○'} {r['name']}: {r['text']}" for r in rows]}


# ----------------------------------------------------------------------------- no popup spam: only something you are using right now, down for more than 10 seconds
_down = {}


def warnings(in_use):
    """`in_use` = ids of links in use right now. -> list of plain warning lines for links that have been red/amber for over 10 s (they clear by themselves)."""
    now, out = time.time(), []
    try:
        rows = {r["id"]: r for r in snapshot(quick=True)}
    except Exception:
        return out
    for i in in_use:
        r = rows.get(i)
        if r and r["state"] != "green":
            _down.setdefault(i, now)
            if now - _down[i] > 10:
                out.append(r["text"])
        else:
            _down.pop(i, None)
    return out
