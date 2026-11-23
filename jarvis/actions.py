"""Actions the UI or the brain can trigger on the user's behalf (an extension point, nothing phone-related is built yet).

Add one with:
    @action("name")
    def name(payload: dict, assistant) -> dict | None: ...
and trigger it from the page with {"t": "action", "name": "name", "payload": {...}} (or, later, from a card button:
card["actions"] = [{"label": "SEND TO PHONE", "action": "send_to_phone", "payload": {...}}]).

Planned (not built): "send_to_phone" (locations, links) and "call_me"; they only need to be registered here."""

import os

REGISTRY = {}


def action(name):
    def deco(fn):
        REGISTRY[name] = fn
        return fn
    return deco


def run(name, payload=None, assistant=None):
    fn = REGISTRY.get(name)
    if not fn:
        return {"ok": False, "error": f"unknown action: {name}"}
    try:
        return {"ok": True, **(fn(payload or {}, assistant) or {})}
    except Exception as e:                      # an action must never take the assistant down
        return {"ok": False, "error": str(e)[:200]}


@action("ping")
def _ping(payload, assistant):
    return {"pong": True, "echo": payload}


@action("send_to_phone")
def _send_to_phone(payload, assistant):
    """From a card button or the page (user-initiated): validated and sent through ntfy."""
    from . import phone
    r = phone.send(payload)
    if assistant is not None and r["ok"] and assistant.on_card:
        assistant.on_card({"type": "text", "title": "Sent to your phone", "text": str(payload.get("title") or payload.get("message") or "")[:120]})
    return {"sent": r["ok"], "message": r["message"]}


@action("focus_jarvis")
def _focus_jarvis(payload, assistant):
    """Tap on the dock orb: bring the big Jarvis window back (same as 'back to Jarvis')."""
    import threading
    threading.Thread(target=assistant.run_open, args=({"action": "focus_jarvis", "label": "Jarvis"},), daemon=True).start()
    return {}


@action("close_embed")
def _close_embed(payload, assistant):
    """The X on the frame: remove what is shown in it (the dock and the kiosk stay)."""
    from . import opener
    return {k: v for k, v in opener.close_last().items() if k == "message"}


@action("embed_full")
def _embed_full(payload, assistant):
    from . import opener
    return {k: v for k, v in opener.set_full(bool(payload.get("on"))).items() if k == "message"}


@action("toggle_orb")
def _toggle_orb(payload, assistant):
    """The orb's expand button / SUPER+SHIFT+J: corner orb <-> full screen."""
    import threading
    from . import opener
    threading.Thread(target=opener.toggle_orb, daemon=True).start()
    return {}


@action("to_orb")
def _to_orb(payload, assistant):
    """The page's TO ORB button: leave full screen for the corner orb."""
    import threading
    from . import opener
    threading.Thread(target=lambda: (opener.clear_embed(), opener.go_to_orb()), daemon=True).start()
    return {}


@action("embed_state")
def _embed_state(payload, assistant):
    """The page reports how many windows (video, map, image) it has open."""
    from . import opener
    try:
        n = int(payload.get("n", 0))
    except (TypeError, ValueError):
        n = 0
    opener.page_windows_changed(max(0, n))
    return {}


@action("embed_closed")
def _embed_closed(payload, assistant):
    return {}


@action("memory_delete")
def _memory_delete(payload, assistant):
    """The x on a memory card: forget that one fact."""
    from . import memory
    return {"forgot": memory.forget_id(str(payload.get("id", "")))}


@action("open_link")
def _open_link(payload, assistant):
    """A source link on a report card: validated like any other open request, then opened in the Jarvis frame."""
    import threading
    from . import opener
    act = opener.validate({"url": str(payload.get("url", ""))})
    if act and assistant is not None:
        threading.Thread(target=assistant.run_open, args=(act,), daemon=True).start()
    return {}


@action("mac_retry")
def _mac_retry(payload, assistant):
    import threading
    from . import mac
    if not mac.paired():
        return {"message": "not paired: say pair my Mac"}
    threading.Thread(target=assistant.mac_watch, daemon=True).start()
    return {"message": "waiting for the Mac again"}


@action("mac_test")
def _mac_test(payload, assistant):
    import threading
    from . import mac, opener
    r = mac.send_test()
    opener.NOTIFY({"t": "guide", "gid": "mac", "state": "sent" if r["ok"] else "error", "status": "Test sent, waiting for the Mac to confirm" if r["ok"] else "Not delivered: " + r["message"]})
    if r["ok"]:
        threading.Thread(target=lambda: mac.watch(assistant.mac_notify, 2), daemon=True).start()
        def check(mid=r.get("id")):
            import time
            t0 = time.time()
            while time.time() - t0 < 25:
                if mid in mac.STATE["acks"]:
                    return                                              # the watcher already reported delivered / dropped
                time.sleep(1)
            opener.NOTIFY({"t": "guide", "gid": "mac", "state": "error", "status": "Not delivered: the Mac did not answer. Is the Terminal with 'run' still open?"})
        threading.Thread(target=check, daemon=True).start()
    return {"message": r["message"]}


def _guide(state, text, step=None):
    from . import opener
    m = {"t": "guide", "gid": "spotify", "state": state, "status": text}
    if step:
        m["step"] = step
    opener.NOTIFY(m)


@action("spotify_premium")
def _spotify_premium(payload, assistant):
    if payload.get("yes"):
        _guide("ok", "Good. Press DONE, NEXT.")
        return {"message": "premium"}
    _guide("error", "Spotify playback needs Premium. Music stays on YouTube Music (it works now: just say play something).")
    return {"message": "no premium: youtube music"}


@action("spotify_client_id")
def _spotify_client_id(payload, assistant):
    from . import spotify
    ok, msg = spotify.save_client_id(str(payload.get("value", "")))
    _guide("ok" if ok else "error", msg)
    return {"message": "saved" if ok else "rejected"}


@action("spotify_login")
def _spotify_login(payload, assistant):
    import threading
    from . import opener, spotify
    url = spotify.auth_url()
    if not url:
        _guide("error", "Save your Client ID first (the step before).")
        return {"message": "no client id"}
    spotify.listen_for_callback(lambda ok, msg: _guide("connected" if ok else "error", msg + ("" if ok else " Music stays on YouTube.")))
    _guide("ok", "Spotify's login page is opening. Log in there and press Agree.")
    threading.Thread(target=lambda: (opener.show_in_window(url, "Spotify login") if opener.orb_mode() else opener.execute({"action": "open_url", "url": url, "label": "Spotify login"})), daemon=True).start()
    return {"message": "login opened"}


@action("spotify_check")
def _spotify_check(payload, assistant):
    from . import spotify
    import threading
    def work():
        ok, msg = spotify.check()
        _guide("connected" if ok else "error", msg)
    threading.Thread(target=work, daemon=True).start()                  # starting the player takes a few seconds: never block the page
    return {"message": "checking"}


@action("spotify_test")
def _spotify_test(payload, assistant):
    from . import spotify
    ok, msg = spotify.play_test()
    _guide("connected" if ok else "error", msg + ("" if ok else " Music stays on YouTube."))
    return {"message": "played" if ok else "failed"}


@action("focus_other")
def _focus_other(payload, assistant):
    from . import opener
    r = opener.focus_other(str(payload.get("address", "")))
    return {"message": r["message"]}


def _pc(state, text):
    from . import opener
    opener.NOTIFY({"t": "guide", "gid": "phonecam", "state": state, "status": text})


@action("phonecam_save")
def _phonecam_save(payload, assistant):
    from . import phonecam
    ok, msg = phonecam.save(str(payload.get("value", "")))
    _pc("ok" if ok else "error", msg)
    return {"message": "saved" if ok else "rejected"}


@action("phonecam_test")
def _phonecam_test(payload, assistant):
    import threading
    from . import phonecam, settings

    def go():
        typed = settings.get("phone_cam_typed") or phonecam.get_url() or ""
        cands, notes = phonecam.normalise(typed)
        if not cands:
            _pc("error", notes[0] if notes else "Save the address first.")
            return
        _pc("ok", "Testing " + str(len(cands)) + " address" + ("es" if len(cands) != 1 else "") + " at once...")
        url, path, reasons = phonecam.probe(cands)
        if not url:
            _pc("error", "Nothing worked: " + " ".join(reasons))
            return
        phonecam.save(url) if False else settings.update(phone_cam_url=url)
        _pc("connected", "Works: " + url + " (saved).")
        if assistant.on_card:
            assistant.on_card({"type": "screenshot", "title": "Phone camera test", "file": path})
    threading.Thread(target=go, daemon=True).start()
    return {"message": "testing"}


@action("phonecam_preview")
def _phonecam_preview(payload, assistant):
    from . import phonecam
    u = phonecam.get_url()
    if not u:
        _pc("error", "Save the address first.")
        return {"message": "no address"}
    if assistant.on_card:
        assistant.on_card({"type": "livecam", "title": "Phone camera (live)", "url": "/api/look/stream"})        # the shared stream: the phone sees one viewer
    return {"message": "preview opened"}


@action("phonecam_use")
def _phonecam_use(payload, assistant):
    from . import phonecam
    if not phonecam.get_url():
        _pc("error", "Save the address first.")
        return {"message": "no address"}
    assistant.source = "phone"
    _pc("connected", "Look now uses the phone camera. (Camera source: PHONE.)")
    return {"message": "source phone"}


def _loc_status(state, text):
    from . import opener
    opener.NOTIFY({"t": "guide", "gid": "loc", "state": state, "status": text})


def _loc_g(payload):
    from . import maps
    try:
        la, lo = float(payload.get("lat")), float(payload.get("lon"))
    except (TypeError, ValueError):
        return None
    label = str(payload.get("label", ""))[:200]
    return {"lat": la, "lon": lo, "label": label, "name": label.split(",")[0]} if maps.valid(la, lo) and label else None


@action("loc_find")
def _loc_find(payload, assistant):
    """FIND with text typed in the box (no suggestion tapped): geocode it and show it on the map to confirm."""
    import threading
    from . import maps

    def go():
        g = maps.geocode(str(payload.get("value", ""))[:200])
        if not g:
            _loc_status("error", "I couldn't find that. Try the street, town and state.")
            return
        _loc_confirm(assistant, g, False)
    threading.Thread(target=go, daemon=True).start()
    return {"message": "looking"}


def _loc_confirm(assistant, g, today):
    from . import maps, settings
    if today:
        settings.update(loc_temp={"date": maps._today(), "lat": g["lat"], "lon": g["lon"], "label": g["name"]})
        _loc_status("connected", "Saved for today: " + g["name"])
        return
    maps.PENDING["g"] = g
    spec = maps.confirm_spec(g)
    _loc_status("ok", "Shown on the map. Press YES there (or say yes) to save it as home.")
    assistant.run_open({"action": "show_map", "id": spec["id"], "label": "Is this where you live?"})
    assistant.expect = {"kind": "confirmloc", "text": ""}


@action("loc_pick")
def _loc_pick(payload, assistant):
    g = _loc_g(payload)
    if not g:
        return {"message": "bad suggestion"}
    _loc_confirm(assistant, g, False)
    return {"message": "shown"}


@action("loc_today")
def _loc_today(payload, assistant):
    g = _loc_g(payload)
    if not g:
        return {"message": "bad suggestion"}
    _loc_confirm(assistant, g, True)
    return {"message": "saved for today"}


@action("files_open")
def _files_open(payload, assistant):
    """FILES window: open a file in its default app. Only from the page, only with the session token, only inside your home folder."""
    import threading
    from . import opener, terminal
    if not terminal.token_ok(str(payload.get("token", ""))):
        return {"message": "refused"}
    threading.Thread(target=opener.open_path, args=(str(payload.get("path", "")),), daemon=True).start()
    return {"message": "opening"}


@action("abort_desktop")
def _abort_desktop(payload, assistant):
    """The always-visible DESKTOP button: leave the Jarvis full screen for the normal desktop. Windows and the terminal keep running (nothing is closed)."""
    import threading
    from . import opener
    threading.Thread(target=opener.go_to_orb, daemon=True).start()
    return {"message": "desktop"}


@action("place_other")
def _place_other(payload, assistant):
    from . import opener
    return {"message": opener.place_other(str(payload.get("address", "")))["message"]}


@action("release_other")
def _release_other(payload, assistant):
    from . import opener
    return {"message": opener.release_other(str(payload.get("address", "")))["message"]}


def _meta(kind):
    import threading
    return lambda payload, assistant: (threading.Thread(target=assistant.run_meta, args=(kind,), daemon=True).start(), {"message": kind})[1]


for _k in ("update_now:update", "improve_apply:improve_apply", "improve_discard:improve_discard", "publish_push:publish_push"):
    _name, _kind = _k.split(":")
    if _name != "publish_push":
        REGISTRY[_name] = _meta(_kind)


@action("publish_push")
def _publish_push(payload, assistant):
    """The PUSH TO GITHUB button on the prepared public update (a deliberate press; the scan runs again first)."""
    import threading
    from . import publish

    def go():
        ok, msg = publish.push("Update from the working copy")
        assistant._meta_card("Public update", [msg])
    threading.Thread(target=go, daemon=True).start()
    return {"message": "pushing"}


def _voice_card(assistant):
    from . import voiceapi
    if assistant.on_card:
        assistant.on_card(voiceapi.guide_card(voiceapi.is_on()))


@action("voice_toggle")
def _voice_toggle(payload, assistant):
    """The TURN ON / TURN OFF button of the iPhone voice card (off by default)."""
    import threading
    from . import voiceapi

    def go():
        ok, msg = assistant.voice_set(not voiceapi.is_on())
        from . import opener
        opener.NOTIFY({"t": "guide", "gid": "voice", "state": "connected" if ok else "error", "status": ("ON: listening on your home network" if voiceapi.is_on() else "OFF") if ok else msg})
    threading.Thread(target=go, daemon=True).start()
    return {"message": "toggling"}


@action("voice_regen")
def _voice_regen(payload, assistant):
    from . import opener, voiceapi
    voiceapi.regenerate()
    opener.NOTIFY({"t": "guide", "gid": "voice", "state": "ok", "status": "New secret made. The old address no longer works: copy the new one into your Shortcut."})
    return {"message": "regenerated"}


@action("voice_test")
def _voice_test(payload, assistant):
    """TEST: send a short spoken sample from this machine to its own endpoint and show what came back (the address is never shown)."""
    import threading
    from . import opener, tts, voiceapi

    def go():
        import tempfile
        import urllib.request
        if not voiceapi.is_on():
            opener.NOTIFY({"t": "guide", "gid": "voice", "state": "error", "status": "Turn it on first."})
            return
        wav = tempfile.mktemp(suffix=".wav")
        try:
            tts.synth("What is two plus two", wav)
            req = urllib.request.Request(voiceapi.url(), data=open(wav, "rb").read(), headers={"Content-Type": "audio/wav"}, method="POST")
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read().decode("utf-8", "ignore")[:160]
            opener.NOTIFY({"t": "guide", "gid": "voice", "state": "connected", "status": "Works. I sent a spoken sample and got back: " + body})
        except Exception as e:
            opener.NOTIFY({"t": "guide", "gid": "voice", "state": "error", "status": "The endpoint did not answer (" + type(e).__name__ + "). If a firewall is on, port 8766 may be blocked; I did not change it."})
        finally:
            try:
                os.unlink(wav)
            except OSError:
                pass
    threading.Thread(target=go, daemon=True).start()
    return {"message": "testing"}


@action("outbox_resend")
def _outbox_resend(payload, assistant):
    import threading
    threading.Thread(target=assistant.resend_failed, daemon=True).start()
    return {"message": "resending"}


@action("mic_test")
def _mic_test(payload, assistant):
    import threading
    threading.Thread(target=assistant.mic_test, daemon=True).start()
    return {"message": "testing"}


@action("mic_fix")
def _mic_fix(payload, assistant):
    import threading
    from . import micheck
    threading.Thread(target=lambda: assistant.say(micheck.fix()), daemon=True).start()
    return {"message": "fixing"}


@action("tour_loc")
def _tour_loc(payload, assistant):
    from . import maps
    assistant.on_card(maps.location_card(False))
    return {"message": "location"}


@action("tour_look")
def _tour_look(payload, assistant):
    assistant.ask_text("look at this")
    return {"message": "look"}


@action("tour_done")
def _tour_done(payload, assistant):
    from . import settings
    settings.update(onboarded=True)
    return {"message": "done"}


def _cm(gid, ok, msg):
    from . import opener
    opener.NOTIFY({"t": "guide", "gid": gid, "state": "connected" if ok else "error", "status": msg})


@action("cal_save")
def _cal_save(payload, assistant):
    from . import calmail
    ok, msg = calmail.save_calendar(str(payload.get("value", "")))
    _cm("cal", ok, msg)
    return {"message": "saved" if ok else "rejected"}


@action("mail_user")
def _mail_user(payload, assistant):
    from . import calmail
    ok, msg = calmail.save_mail(user=str(payload.get("value", "")))
    _cm("mail", ok, msg)
    return {"message": "saved" if ok else "rejected"}


@action("mail_pass")
def _mail_pass(payload, assistant):
    from . import calmail
    ok, msg = calmail.save_mail(password=str(payload.get("value", "")))
    _cm("mail", ok, msg)
    return {"message": "saved" if ok else "rejected"}


@action("read_to_mac")
def _read_to_mac(payload, assistant):
    import threading
    from . import outbox
    t = getattr(assistant, "_last_read", "")
    if t:
        threading.Thread(target=assistant.run_mac, args=({"file": outbox.text_to_note_file(t, "text")},), daemon=True).start()
    return {"message": "sending"}


@action("read_to_phone")
def _read_to_phone(payload, assistant):
    import threading
    t = getattr(assistant, "_last_read", "")
    if t:
        threading.Thread(target=assistant.run_phone, args=({"title": "Text from Jarvis", "message": t[:900]},), daemon=True).start()
    return {"message": "sending"}


@action("read_to_list")
def _read_to_list(payload, assistant):
    from . import extras
    n = 0
    for ln in getattr(assistant, "_last_read", "").splitlines()[:20]:
        if 3 <= len(ln.strip()) <= 80:
            extras.list_add(ln.strip())
            n += 1
    assistant.say(f"Added {n} lines to your list.")
    return {"message": f"{n} added"}


@action("launch_item")
def _launch_item(payload, assistant):
    """LAUNCHER: open an app (by its desktop id, which must exist) or a file/folder inside your home folder. Token required."""
    import threading
    from . import opener, terminal
    if not terminal.token_ok(str(payload.get("token", ""))):
        return {"message": "refused"}
    if payload.get("kind") == "app":
        hit = {v for v in opener.desktop_apps().values()}
        did = str(payload.get("id", ""))
        if did in hit:
            threading.Thread(target=lambda: opener.execute({"action": "open_desktop", "id": did, "label": did}), daemon=True).start()
            return {"message": "launching"}
        return {"message": "unknown app"}
    threading.Thread(target=opener.open_path, args=(str(payload.get("path", "")),), daemon=True).start()
    return {"message": "opening"}


@action("clip_copy")
def _clip_copy(payload, assistant):
    return {"message": "ok"}


# ----------------------------------------------------------------------------- reminders and promises (card buttons)
def _refresh(assistant, kind):
    from . import buddy
    if assistant is not None and assistant.on_card:
        assistant.on_card(buddy.reminders_card() if kind == "rem" else buddy.promises_card())


@action("rem_pause")
def _rem_pause(payload, assistant):
    from . import buddy
    buddy.update_recurring(str(payload.get("id")), paused=True)
    _refresh(assistant, "rem")


@action("rem_resume")
def _rem_resume(payload, assistant):
    from . import buddy
    buddy.update_recurring(str(payload.get("id")), paused=False)
    _refresh(assistant, "rem")


@action("rem_delete")
def _rem_delete(payload, assistant):
    from . import buddy
    buddy.delete_recurring(str(payload.get("id")))
    _refresh(assistant, "rem")


@action("prom_done")
def _prom_done(payload, assistant):
    from . import buddy
    buddy.update_promise(str(payload.get("id")), done=True)
    _refresh(assistant, "prom")


@action("prom_snooze")
def _prom_snooze(payload, assistant):
    import time
    from . import buddy
    buddy.update_promise(str(payload.get("id")), due=time.time() + 3600, nudges=0, last_nudge=0)
    _refresh(assistant, "prom")


@action("prom_delete")
def _prom_delete(payload, assistant):
    from . import buddy
    buddy.delete_promise(str(payload.get("id")))
    _refresh(assistant, "prom")


@action("prom_auto")
def _prom_auto(payload, assistant):
    from . import settings
    settings.update(auto_promises=not settings.get("auto_promises"))
    _refresh(assistant, "prom")


# ----------------------------------------------------------------------------- Spotify playback (cards and the music card): structured, no text from the brain reaches a shell
def _music_push(assistant, st):
    from . import opener, spotify_cli
    if st.get("item"):
        opener.NOTIFY({"t": "musicstate", **{k: v for k, v in spotify_cli.music_card(st).items() if k not in ("type", "gid", "title")}})


@action("text_send")
def _text_send(payload, assistant):
    import threading as _th
    _th.Thread(target=assistant.send_text_draft, daemon=True).start()
    return {"message": "sending"}


@action("text_cancel")
def _text_cancel(payload, assistant):
    from . import textmsg
    textmsg.clear()
    assistant.say("Cancelled. Nothing was sent.")
    return {"message": "cancelled"}


@action("learn_ctl")
def _learn_ctl(payload, assistant):
    from . import learn
    op = str(payload.get("op"))
    if op in ("right", "wrong"):
        learn.record(op == "right")
    c = learn.move(1) if op in ("next", "right", "wrong") else learn.move(-1) if op == "prev" else learn.card()
    if c:
        assistant.on_card(c)
    return {"message": op}


@action("bt_use")
def _bt_use(payload, assistant):
    from . import btspeaker
    ok = btspeaker.use(int(payload.get("id", -1)))                    # only after YES: the button is the confirmation
    assistant.say("Jarvis now plays through the speaker." if ok else "I couldn't switch to that speaker.")
    return {"message": "ok" if ok else "failed"}


@action("bt_back")
def _bt_back(payload, assistant):
    from . import btspeaker
    ok = btspeaker.back()
    assistant.say("Back on the Go's speaker." if ok else "I couldn't switch the speaker.")
    return {"message": "ok" if ok else "failed"}


@action("bt_check")
def _bt_check(payload, assistant):
    from . import btspeaker
    assistant.on_card(btspeaker.card())
    return {"message": "checked"}


@action("music_open")
def _music_open(payload, assistant):
    """The MUSIC button. Playing or connected: the now-playing card. Nothing yet: ONE card with two big choices (play now with no account, or connect Spotify), remembering the choice."""
    from . import music, spotify, spotify_cli, settings
    if spotify.connected():
        st = spotify_cli.status()
        if st.get("item"):
            assistant.on_card(spotify_cli.music_card(st))
            return {"message": "now playing"}
    if music.STATE["active"] and music.STATE["queue"]:
        assistant.on_card(music.card())
        return {"message": "now playing"}
    sp = "Spotify: connected" if spotify.connected() else "Spotify: not connected"
    assistant.on_card({"type": "choice", "gid": "music-choice", "title": "MUSIC", "status": sp + "  ·  YouTube Music: ready, no account needed", "choices": [
        {"label": "PLAY NOW, NO ACCOUNT", "sub": "YouTube Music. Works right away: then just say \"play Taylor Swift\".", "action": "music_choice", "payload": {"mode": "youtube"}},
        {"label": "CONNECT SPOTIFY", "sub": "Needs Spotify Premium. A few guided steps, one at a time, in plain words.", "action": "music_choice", "payload": {"mode": "spotify"}}]})
    return {"message": "choice card"}


@action("music_choice")
def _music_choice(payload, assistant):
    from . import settings, spotify
    mode = "spotify" if payload.get("mode") == "spotify" else "youtube"
    settings.update(music_pref=mode)
    if mode == "spotify":
        assistant.on_card(spotify.guide_card())
        return {"message": "spotify setup"}
    assistant.say("Music is ready. Tell me what to play, for example: play Taylor Swift.")
    return {"message": "youtube"}


@action("spotify_status")
def _spotify_status(payload, assistant):
    from . import spotify_cli
    _music_push(assistant, spotify_cli.status())


@action("spotify_ctl")
def _spotify_ctl(payload, assistant):
    from . import spotify_cli
    op = str(payload.get("op"))
    from . import spotify as _sp
    if not _sp.connected():                                         # the YouTube queue: the same buttons send the same words as your voice
        words = {"pause": "pause the music", "resume": "resume the music", "next": "next song", "previous": "previous song", "vol_up": "turn the volume up", "vol_down": "turn the volume down"}
        if op in words:
            import threading as _th
            _th.Thread(target=assistant.ask_text, args=(words[op],), daemon=True).start()
        return {"ok": op in words}
    cmd = {"op": "volume", "delta": 10} if op == "vol_up" else {"op": "volume", "delta": -10} if op == "vol_down" else {"op": op}
    if cmd["op"] not in ("pause", "resume", "next", "previous", "volume"):
        return {"ok": False}
    r = spotify_cli.run(cmd)
    import time
    time.sleep(0.4)
    _music_push(assistant, spotify_cli.status())
    return {"message": r.get("speech", "")}


@action("spotify_play")
def _spotify_play(payload, assistant):
    from . import spotify_cli
    uri = str(payload.get("uri", ""))
    if not spotify_cli.parse_link(uri):
        return {"ok": False}
    r = spotify_cli.run({"op": "play", "target": uri})
    if assistant is not None and assistant.on_card:
        for c in r.get("cards", []):
            assistant.on_card(c)
    return {"message": r.get("speech", "")}


@action("spotify_devices")
def _spotify_devices(payload, assistant):
    from . import spotify_cli
    r = spotify_cli.run({"op": "devices"})
    if assistant is not None and assistant.on_card:
        for c in r.get("cards", []):
            assistant.on_card(c)


@action("spotify_device")
def _spotify_device(payload, assistant):
    import re
    from . import spotify, spotify_cli
    name = str(payload.get("name") or "")[:60]
    if payload.get("default"):
        spotify_cli.set_default_device(name)
    did = str(payload.get("id") or "")
    if re.fullmatch(r"[A-Za-z0-9]{10,64}", did):
        _, err = spotify._api("PUT", "/me/player", {"device_ids": [did], "play": not payload.get("default")})
        return {"message": err or ("Default set to " + name if payload.get("default") else "Playing on " + name)}
    return {"ok": False}


@action("spotify_setup")
def _spotify_setup(payload, assistant):
    from . import spotify
    if assistant is not None and assistant.on_card:
        assistant.on_card(spotify.guide_card())


@action("typebox_open")
def _typebox_open(payload, assistant):
    """The TYPE BOX button in the menu: same as the shortcut."""
    import subprocess
    from . import config, personas
    subprocess.Popen([str(config.ROOT / "bin" / "jarvis-typebox"), assistant.persona, personas.PERSONAS[assistant.persona]["name"]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


# ----------------------------------------------------------------------------- Show me how / Circle to ask (buttons)
@action("howto_next")
def _howto_next(payload, assistant):
    assistant.howto_next()


@action("howto_stop")
def _howto_stop(payload, assistant):
    assistant.howto_stop()


@action("howto_ask")
def _howto_ask(payload, assistant):
    """The SHOW ME HOW button: asks what you want to do, then listens for the answer without a wake word."""
    import threading
    def go():
        assistant.expect = {"kind": "howto_goal", "text": ""}
        assistant.say("What do you want to do?", wait=True)
        assistant._conversation_open(wait=10.0)
    threading.Thread(target=go, daemon=True).start()


@action("circle_open")
def _circle_open(payload, assistant):
    """The CIRCLE button: the overlay opens at once; draw a circle and ask out loud, in either order."""
    assistant.circle_start()


@action("orb_reset")
def _orb_reset(payload, assistant):
    from . import opener
    opener.NOTIFY({"t": "orb", "op": "reset"})


@action("holo_send_export")
def _holo_send_export(payload, assistant):
    """Send the newest exported picture/video of a model to the Mac through the signed inbox (allowed file types and size limit apply there)."""
    import os
    path = getattr(assistant, "last_export", None)
    if not path or not os.path.exists(path):
        return {"ok": False, "message": "Nothing exported yet."}
    import threading
    threading.Thread(target=assistant.run_mac, args=({"file": path},), daemon=True).start()
    return {"message": "Sending to your Mac."}


@action("models_clear")
def _models_clear(payload, assistant):
    from . import models3d
    n = models3d.clear_cache()
    if assistant is not None and assistant.on_card:
        assistant.on_card({"type": "text", "title": "3D model cache", "text": f"Cleared {n // 2 if n else 0} cached model{'s' if n != 2 else ''}."})


# ----------------------------------------------------------------------------- Markets desk (read-only: nothing here can trade)
@action("mkt_key")
def _mkt_key(payload, assistant):
    from . import markets
    ok, msg = markets.save_key(str(payload.get("value", "")))
    return {"ok": ok, "message": msg}


@action("mkt_contact")
def _mkt_contact(payload, assistant):
    import re
    from . import settings
    v = re.sub(r"[^A-Za-z0-9@._+ -]", "", str(payload.get("value", "")))[:80].strip()
    settings.update(edgar_contact=v or None)
    return {"message": "Contact saved for SEC filings." if v else "Contact cleared."}


@action("mkt_open")
def _mkt_open(payload, assistant):
    from . import mktdesk
    if assistant.on_card:
        assistant.on_card(mktdesk.desk_card())
