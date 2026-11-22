"""The assistant pipeline: hold-to-talk -> whisper -> claude (streamed) -> piper (per sentence).
UI-agnostic; reports through callbacks (may be called from any thread)."""
import json
import math
import os
import random
import re
import shutil
import subprocess
import threading
import time
import wave

import numpy as np

from . import buddy, holo, markets, mktdesk, models3d, offline, screenhelp, moods, sounds, audio, camera, companion, continuity, lookmem, micheck, improve, look, outbox, phonecam, maps, intents, prefs, soundtest, spotify, memory, music, net, quiet, readscreen, scenes, dictate, extras, research, video, config, convo, endpoint, mac, opener, phone, personas, settings, skills, stt, tts
from .brain import make_brain

MIN_RECORD_S = 0.4
COMMAND_PROMPT = "Jarvis. Rose. Hide the interface. Show the interface. Open YouTube. Back to Jarvis."   # biases spelling of the command words
SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
CLAUSE_END = re.compile(r"(?<=[,;:])\s+")
HOLD_RE = re.compile(r"```|\{")                    # start of a (possible) JSON card block
SHOW_RE = re.compile(r'(?:```(?:json)?\s*)?\{\s*"(?:show|open|phone|mac|intent|look)"')
KIOSK_CLASSES = ("jarvis-ui", "local.jarvis", "chrome-127.0.0.1__dock-Default")       # our own windows: never what "look" should see
LAST_NOTE = ""                                      # set by screenshot() when it could not step away from our own window
CARD_TYPES = {"text", "list", "map", "chart", "image", "explain"}
SLOW_INTENT = re.compile(r"\b(near me|nearby|near here|around here|best|find|search|look up|latest|news|tonight|open(?:ing)? hours|opening|price|cost|recommend|events?|restaurants?|cafes?|things to do|where can|directions|route|reviews?|compare|research)\b", re.I)


# --- cards ----------------------------------------------------------------
def _s(v, n):
    return str(v)[:n] if v is not None else ""


def clean_explain(c):
    """The brain's explain card: a small picture that makes an answer clear. kind: diagram (nodes + arrows), timeline (events), labelled (a picture with numbered pins) or spin3d
    (a slowly turning cube, sphere, pyramid or ring). Everything is clipped and typed here; the page draws it and falls back to plain text if drawing fails."""
    kind = c.get("kind") if c.get("kind") in ("diagram", "timeline", "labelled", "spin3d", "steps", "draw") else "diagram"
    out = {"kind": kind, "key": _s(c.get("key"), 220), "text": _s(c.get("text"), 500)}
    if kind in ("diagram", "steps"):
        nodes = [{"id": _s(n.get("id"), 12), "label": _s(n.get("label"), 40)} for n in (c.get("nodes") or [])[:10] if isinstance(n, dict) and n.get("id") is not None]
        ids = {n["id"] for n in nodes}
        out["nodes"] = nodes
        out["edges"] = [[_s(e[0], 12), _s(e[1], 12), _s(e[2], 24) if len(e) > 2 else ""] for e in (c.get("edges") or [])[:14] if isinstance(e, (list, tuple)) and len(e) >= 2 and _s(e[0], 12) in ids and _s(e[1], 12) in ids]
    elif kind == "timeline":
        out["events"] = [{"when": _s(e.get("when"), 16), "text": _s(e.get("text"), 70)} for e in (c.get("events") or [])[:8] if isinstance(e, dict)]
    elif kind == "labelled":
        out["src"] = _s(c.get("src"), 500)
        out["labels"] = [{"x": max(0.0, min(1.0, float(l.get("x", 0.5)))), "y": max(0.0, min(1.0, float(l.get("y", 0.5)))), "text": _s(l.get("text"), 50)}
                         for l in (c.get("labels") or [])[:8] if isinstance(l, dict) and all(isinstance(l.get(k, 0), (int, float)) for k in ("x", "y"))]
    elif kind == "draw":
        d = holo.validate_draw({"elements": c.get("elements")})
        out["elements"] = d["elements"] if d else []
        out["bullets"] = holo.bullets(c.get("bullets"))
        out["more"] = _s(c.get("more"), 800)
    elif kind == "spin3d":
        out["shape"] = c.get("shape") if c.get("shape") in ("cube", "sphere", "pyramid", "ring") else "cube"
        out["caption"] = _s(c.get("caption"), 80)
    return out


def clean_cards(raw):
    """Validate/normalize the model's {"show":[...]} list; drops anything unknown."""
    out = []
    for c in (raw if isinstance(raw, list) else [])[:4]:
        if not isinstance(c, dict) or c.get("type") not in CARD_TYPES:
            continue
        t = c["type"]
        card = {"type": t, "title": _s(c.get("title"), 60)}
        if t == "text":
            card["text"] = _s(c.get("text"), 1200)
        elif t == "list":
            card["items"] = [_s(i, 160) for i in (c.get("items") or [])[:12]]
        elif t == "map":
            card["query"] = _s(c.get("query"), 120)
            for k in ("lat", "lon"):
                if isinstance(c.get(k), (int, float)):
                    card[k] = float(c[k])
        elif t == "chart":
            card["kind"] = "bar" if c.get("kind") == "bar" else "line"
            vals = [float(v) for v in (c.get("values") or [])[:24] if isinstance(v, (int, float))]
            card["values"] = vals
            card["labels"] = [_s(l, 16) for l in (c.get("labels") or [])[: len(vals)]]
        elif t == "image":
            card["src"] = _s(c.get("src"), 500)
        elif t == "explain":
            card.update(clean_explain(c))
            if not (card.get("nodes") or card.get("events") or card.get("src") or card.get("shape") or card.get("elements")):          # nothing to draw: a plain text card instead (or nothing)
                txt = card.get("key") or card.get("text")
                if not txt:
                    continue
                card = {"type": "text", "title": card["title"], "text": txt}
        out.append(card)
    return out


def split_reply_full(reply):
    """-> (text to show/speak, cards, open_action, phone_action, mac_action). Strips the trailing {"show":[...], "open":{...}, "phone":{...}, "mac":{...}} block."""
    m = SHOW_RE.search(reply)
    if not m:
        return reply.strip(), [], None, None, None
    try:
        obj, _ = json.JSONDecoder().raw_decode(reply[reply.index("{", m.start()):])
        cards, act, ph, mc = clean_cards(obj.get("show")), obj.get("open"), obj.get("phone"), obj.get("mac")
    except (ValueError, AttributeError):
        cards, act, ph, mc = [], None, None, None
    return reply[: m.start()].strip(), cards, act, ph, mc


def split_intent(reply):
    """The brain's {"intent": {...}} (one allowlisted name + plain fields), wherever it sits among the trailing JSON blocks, or None."""
    for m in SHOW_RE.finditer(reply):
        try:
            obj, _ = json.JSONDecoder().raw_decode(reply[reply.index("{", m.start()):])
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("intent"), dict):
            return obj["intent"]
    return None


def split_reply_ex(reply):
    t, cards, act, _, _ = split_reply_full(reply)
    return t, cards, act


def split_reply(reply):
    t, cards, _ = split_reply_ex(reply)
    return t, cards


# --- screenshots ----------------------------------------------------------
def _hypr(*args):
    try:
        return json.loads(subprocess.run(["hyprctl", *args, "-j"], capture_output=True, text=True, timeout=3).stdout)
    except Exception:
        return {}


def _hypr_focus_previous():
    subprocess.run(["hyprctl", "dispatch", 'hl.dsp.focus({ workspace = "previous" })'],
                   capture_output=True, timeout=3)


def screenshot(source="screen"):
    """Grab the screen ("screen": focused monitor, "window": the focused window) with grim, 70% scale.
    If our own kiosk is in front, hop to the previous workspace for the capture, then come back."""
    config.SHOTS_DIR.mkdir(exist_ok=True)
    for f in sorted(config.SHOTS_DIR.glob("shot-*.jpg"))[:-9]:
        f.unlink()
    global LAST_NOTE
    LAST_NOTE = ""
    hopped = False
    showing = opener.EMBED.get("kind") is not None            # something is open in the Jarvis frame: the screen as it is IS the answer
    if not showing and _hypr("activewindow").get("class") in KIOSK_CLASSES:
        _hypr_focus_previous()
        time.sleep(0.45)
        hopped = _hypr("activewindow").get("class") not in KIOSK_CLASSES
        if not hopped:
            LAST_NOTE = ("Look: Jarvis is the only thing on this workspace, so the picture is Jarvis itself. "
                         "Give it its own workspace (e.g. SUPER+9) and keep your work on another.")
    try:
        cmd = ["grim", "-t", "jpeg", "-q", "80", "-s", "0.7"]
        win = _hypr("activewindow")
        if source == "window" and win.get("class") not in (None, *KIOSK_CLASSES) and win.get("size"):
            (x, y), (w, h) = win["at"], win["size"]
            cmd += ["-g", f"{x},{y} {w}x{h}"]
        else:
            mons = _hypr("monitors")
            name = next((m["name"] for m in mons if m.get("focused")), None) if isinstance(mons, list) else None
            if name:
                cmd += ["-o", name]
        path = str(config.SHOTS_DIR / f"shot-{int(time.time())}.jpg")
        try:
            subprocess.run(cmd + [path], check=True, timeout=6)
        except subprocess.TimeoutExpired:
            raise RuntimeError("screen capture timed out (is the display asleep?)")
        return path
    finally:
        if hopped:
            _hypr_focus_previous()


# --- streaming helpers -----------------------------------------------------
class Sentences:
    """Feed streamed text deltas, get complete sentences as soon as they are finished.
    A trailing JSON/code block is withheld (never spoken)."""

    def __init__(self):
        self.buf, self.first, self.hold = "", True, None

    def _drain(self):
        out = []
        while True:
            m = SENTENCE_END.search(self.buf)
            if not m and self.first:   # start talking sooner: cut the first chunk at a comma
                c = CLAUSE_END.search(self.buf)
                m = c if c and c.start() >= 18 else None
            if not m:
                return out
            out.append(self.buf[: m.start()].strip())
            self.buf = self.buf[m.end():]
            self.first = False

    def feed(self, delta):
        if self.hold is not None:
            self.hold += delta
            return []
        self.buf += delta
        m = HOLD_RE.search(self.buf)
        if not m:
            return self._drain()
        self.hold, self.buf = self.buf[m.start():], self.buf[: m.start()]
        out = self._drain()
        rest, self.buf = self.buf.strip(), ""
        return out + ([rest] if rest else [])

    def flush(self):
        out = []
        if self.hold is not None:
            if not SHOW_RE.search(self.hold):      # braces/backticks that weren't a card: speak them
                self.buf += self.hold
            self.hold = None
        rest, self.buf = self.buf.strip(), ""
        return out + ([rest] if rest else [])


class Timing:
    """Seconds since the key was released."""

    def __init__(self, t_release):
        self.t0, self.marks = t_release, {}

    def mark(self, name):
        self.marks.setdefault(name, time.time() - self.t0)

    def line(self):
        names = [("ack", "ack"), ("transcript", "transcript"), ("intent", "intent known"), ("first_token", "claude 1st token"),
                 ("first_sentence", "1st sentence"), ("first_audio", "FIRST AUDIO"), ("first_visual", "FIRST VISUAL"), ("open_dispatched", "open dispatched"), ("open_window", "WINDOW SHOWN"), ("done", "done")]
        return " | ".join(f"{label} {self.marks[k]:.2f}s" for k, label in names if k in self.marks)


class Assistant:
    _display = None
    _no_local = False

    def __init__(self, on_state, on_msg, on_timing=None, on_card=None, on_level=None, on_persona=None, cards=False, on_listen=None, handsfree=False, on_status=None, on_muted=None, on_ui=None):
        self.on_state, self.on_msg, self.on_timing = on_state, on_msg, on_timing
        self.on_card, self.on_level, self.on_persona, self.on_listen = on_card, on_level, on_persona, on_listen
        self.on_status = on_status or (lambda lines, active: None)
        self.hf, self.handsfree_default = None, handsfree
        self.handsfree_want = handsfree                  # what the user wants when not muted
        self.on_muted = on_muted or (lambda on: None)
        self.on_ui = on_ui or (lambda hidden: None)
        self.timers = []
        self.translate_to = None                                     # translate mode: the language, while it is on
        self.in_conv, self._hf_temp, self.expect = False, False, None      # conversation mode state; expect = a question we asked (e.g. 'home')
        if not settings.get("followup_v2"):
            settings.update(followup=False, followup_v2=True)             # no closing question ("anything else?") unless you turn it on
        self.followup = bool(settings.get("followup", False))               # ask "Anything else?" after helping
        self.muted = bool(settings.get("muted", False))  # deaf mode: persisted, only a deliberate button/key/CLI can change it
        self.cards = cards
        self.persona = personas.DEFAULT
        sounds.set_persona(self.persona)
        self.recent, self._carry, self._pending = [], "", None     # last exchanges (for persona switches)
        tts.set_voice(personas.voice_path(self.persona))
        self.rec, self.brain = audio.Recorder(), make_brain(personas.system_prompt(self.persona, cards))
        for n in personas.PERSONAS:                                                    # a remembered speaking speed for each voice
            tts.set_speed(personas.voice_path(n), prefs.speed(n))
        self.speaker = tts.Speaker(on_play=self._on_play)
        self.speaker.on_mood = self._mood_changed
        self.speaker.on_fail = self._sound_failed
        self.state = "loading"
        self.shot = None
        self.last_source = ""
        audio.cleanup_stale()
        tts.prune_cache()
        self.look = {"open": False, "path": None, "boxes": [], "sel": None, "source": "", "live": False}
        mktdesk.BRAIN = lambda q: moods.strip(split_reply_full(self.brain.ask(q, timeout=40))[0])
        self.holo3 = None            # the open 3D model: {"spec", "sel", "tour"}
        self.howto = None            # "Show me how": {"goal", "steps", "active", "ev_next", ...}
        self.circle = None           # "Circle to ask": {"q", "box", "t", "busy"}
        self.source = "auto"         # what "look" captures: auto (USB webcam, else screen) | screen | window | camera
        self.t0 = 0.0
        self.turn = 0
        self.ep = None
        self._timing = None
        self._played_gen = None
        self.brain_ready = threading.Event()

    def _set(self, state, detail=""):
        if state == "error" and self.state != "error":
            sounds.play("error")
        self.state = state
        self.on_state(state, detail)
        if music.STATE["active"]:                                    # music is lowered while he listens, thinks or speaks, and comes back afterwards
            quiet = state in ("recording", "transcribing", "thinking", "speaking")
            if quiet != music.STATE.get("ducked", False):
                music.STATE["ducked"] = quiet
                opener.NOTIFY({"t": "embed", "kind": "win", "cmd": "duck" if quiet else "unduck", "type": "video"})
        if state == "idle" and self._pending:
            name, self._pending = self._pending, None
            quiet, self._pending_quiet = getattr(self, "_pending_quiet", False), False
            threading.Thread(target=self.set_persona, args=(name,), kwargs={"announce": not quiet}, daemon=True).start()

    def _mood_changed(self, m):
        if os.environ.get("JARVIS_DEBUG_MOODS"):
            print("[mood] playing as", m, flush=True)
        opener.NOTIFY({"t": "mood", "v": m})

    # --- the buddy: recurring reminders and promise nudges -------------------------------------------
    def _in_call_or_video(self):
        try:
            if opener.EMBED.get("kind") or music.STATE.get("active"):
                return True
            for c in opener._hypr_json("clients") or []:
                if re.search(r"zoom|microsoft teams|google meet|meet\.google|webex|skype|facetime", ((c.get("class") or "") + " " + (c.get("title") or "")).lower()):
                    return True
        except Exception:
            pass
        return False

    def buddy_tick(self):
        """Called every 10 s: fire recurring reminders and promise nudges that are due."""
        try:
            from . import notifier
            threading.Thread(target=notifier.send_due, daemon=True).start() if notifier.due_briefings() else None
        except Exception:
            pass
        for it in buddy.due_recurring():
            self.nudge("Reminder: " + it["label"] + ".", "Reminder", it["label"])
        for p in buddy.due_promises():
            self.nudge(f"You said you would {p['label'][0].lower() + p['label'][1:]}. Done?", "Promise", p["label"], promise=p)

    def nudge(self, text, title, label, promise=None):
        """Gentle: spoken only when idle, not muted, not quiet hours and no video or call is on; otherwise just a small card (and your phone if reminders go there)."""
        spoken = self.state == "idle" and not self.muted and not quiet.active() and not self._in_call_or_video()
        card = {"type": "text", "title": title, "text": label}
        if promise:
            card = {"type": "guide", "gid": "nudge", "title": "PROMISE DUE", "steps": [{"n": 1, "text": promise["label"], "buttons": [
                {"label": "DONE", "action": "prom_done", "payload": {"id": promise["id"]}}, {"label": "LATER", "action": "prom_snooze", "payload": {"id": promise["id"]}}]}], "buttons": []}
            buddy.LAST_NUDGE.update(id=promise["id"], t=time.time())
        if self.on_card:
            self.on_card(card)
        if spoken:
            if promise:
                self.expect = {"kind": "promise_done", "text": promise["id"]}
            self.say(text, wait=True)
            if promise and self.state == "idle" and not self.muted:
                self._conversation_open(wait=8.0)
        elif label and phone.get_topic(create=False) and prefs.get("notify_phone_reminders"):
            threading.Thread(target=self.run_phone, args=({"title": title, "message": label[:200]},), daemon=True).start()

    # --- offline / brain away ---------------------------------------------------------------
    def offline_changed(self, off, why="net", speak=True):
        """The internet or the brain went away (or came back). The chip shows it, it is spoken ONCE per outage (not on an error screen), and the local skills keep working."""
        changed = offline.set_state(off, why)
        opener.NOTIFY({"t": "offline", "msg": offline.chip(why) if off else ""})
        if off and speak and offline.told_once() and self.state in ("idle", "thinking") and not self.muted and not quiet.active():
            threading.Thread(target=self.say, args=(offline.announce_text(why),), daemon=True).start()
        elif changed and not off and self.on_card:
            self.on_card({"type": "text", "title": "Back online", "text": "The " + ("internet" if why == "net" else "brain") + " is back."})
        return changed

    # --- "Show me how" and "Circle to ask": pointing at / drawing on the real screen -------------------------
    def _fx(self, *args):
        """Start a screen overlay (see screenfx.py). It is only ever drawn: no clicks or keys are sent from here."""
        return subprocess.Popen([str(config.ROOT / "bin" / "jarvis-screenfx"), *[str(a) for a in args], "--persona=" + self.persona], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE if args and args[0] == "draw" else subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True, start_new_session=True)

    def howto_card(self, status=""):
        h = self.howto or {"steps": [], "active": False}
        rows = [{"n": i + 1, "text": ("\u2713 " if i < len(h["steps"]) - 1 or not h.get("active") else "\u2192 ") + st} for i, st in enumerate(h["steps"])] or [{"n": 1, "text": "Looking at your screen\u2026"}]
        return {"type": "guide", "gid": "howto", "replace": True, "title": "SHOW ME HOW", "status": status, "steps": rows,
                "buttons": ([{"label": "NEXT", "action": "howto_next", "payload": {}}, {"label": "STOP", "action": "howto_stop", "payload": {}}] if h.get("active") else [])}

    def howto_start(self, goal):
        if self.howto and self.howto.get("active"):
            self.howto_stop()
        self.howto = {"goal": goal, "steps": [], "active": True, "ev_next": threading.Event(), "stop": False}
        threading.Thread(target=self._howto_loop, args=(self.howto,), daemon=True).start()

    def howto_next(self):
        if self.howto and self.howto.get("active"):
            self.howto["ev_next"].set()

    def howto_stop(self, say=None):
        h = self.howto
        if h and h.get("active"):
            h["stop"] = True
            h["ev_next"].set()
        if say:
            self.say(say)

    def _howto_loop(self, h):
        """One step at a time: look at the screen, get the next step + a box, point at it, say it, wait for the screen to change (or "next"), repeat. Never clicks or types."""
        shown = None
        try:
            if _hypr("activewindow").get("class") in KIOSK_CLASSES:                 # Jarvis itself is in front: the app to help with is the one before it
                _hypr_focus_previous()
                time.sleep(0.5)
                if _hypr("activewindow").get("class") in KIOSK_CLASSES:
                    self.say("Switch to the app you want help with first, then ask me again.")
                    return
            for n in range(screenhelp.MAX_STEPS):
                if h["stop"]:
                    return
                self.on_status(["Looking at your screen"], True)
                path = None
                try:
                    path = screenhelp.grab()
                    reply = self.brain.ask(screenhelp.step_prompt(h["goal"], h["steps"]), image=path, timeout=60)
                finally:
                    screenhelp.drop(path)
                    self.on_status([], False)
                step = screenhelp.parse_step(moods.strip(reply))
                if h["stop"]:
                    return
                if not step:
                    self.say("I couldn't tell what to do next from the screen. Try asking again.")
                    return
                if step["done"]:
                    self.say(step["say"] or "That looks done.")
                    return
                h["steps"].append(step["say"])
                if shown is not None:
                    shown.terminate()
                shown = self._fx("point", *( [step["box"][k] for k in ("x", "y", "w", "h")] if step["box"] else [0.5, 0.5, 0.0, 0.0] ), step["say"]) if step["box"] else None
                if self.on_card:
                    self.on_card(self.howto_card("Do it, then I'll look again. Say next to skip, or stop."))
                self.say(step["say"], wait=True)
                h["ev_next"].clear()
                base, t0, stuck_said = screenhelp.sig(), time.time(), False
                while not h["stop"] and not h["ev_next"].is_set():
                    time.sleep(1.0)
                    cur = screenhelp.sig()
                    if screenhelp.changed(base, cur):                                   # you clicked something: wait until the screen settles, then look again
                        for _ in range(4):
                            time.sleep(0.9)
                            nxt = screenhelp.sig()
                            if not screenhelp.changed(cur, nxt, 0.005):
                                break
                            cur = nxt
                        break
                    waited = time.time() - t0
                    if waited > screenhelp.GIVE_UP_S:
                        self.say("I'll stop here. Ask me again whenever you like.")
                        return
                    if waited > screenhelp.STUCK_S and not stuck_said:
                        stuck_said = True
                        self.say("Are you stuck? Say next to skip this step, or stop.")
            self.say("That's as many steps as I do at once. Ask me again if you need more.")
        except Exception as e:
            print("[howto] failed:", type(e).__name__, flush=True)
            self.say("I couldn't look at the screen just now.")
        finally:
            h["active"] = False
            if shown is not None:
                shown.terminate()
            subprocess.run([str(config.ROOT / "bin" / "jarvis-screenfx"), "stop", "point"], capture_output=True)
            screenhelp.cleanup()
            if self.on_card and h["steps"]:
                self.on_card(self.howto_card("Finished." if not h["stop"] else "Stopped."))

    def circle_start(self, question=None):
        self.circle = {"q": question, "box": None, "t": time.time(), "busy": False}
        threading.Thread(target=self._circle_draw, args=(self.circle,), daemon=True).start()

    def _circle_draw(self, c):
        p = self._fx("draw")
        killer = threading.Timer(32, lambda: p.poll() is None and p.kill())
        killer.start()
        try:
            line = (p.stdout.readline() or "").strip()
        finally:
            killer.cancel()
        if not line or line == "cancel":
            if self.circle is c:
                self.circle = None
            return
        try:
            b = json.loads(line)
            box = look.validate_boxes([{"name": "region", "box": [b["x"], b["y"], b["w"], b["h"]]}], 1)
        except (ValueError, KeyError, TypeError):
            box = []
        if not box or self.circle is not c:
            return
        c["box"], c["t"] = box[0], time.time()
        self._circle_try()

    def _circle_try(self):
        c = self.circle
        if not c or c.get("busy") or not c.get("box"):
            return
        if not c.get("q"):
            if self.state == "idle":
                self.expect = {"kind": "circle_q", "text": ""}
                self.say("What would you like to know about it?", wait=True)
                if self.state == "idle":
                    self._conversation_open(wait=10.0)
            return
        c["busy"] = True
        threading.Thread(target=self._circle_answer, args=(c,), daemon=True).start()

    def _circle_answer(self, c):
        path = None
        try:
            self.on_status(["Looking at what you circled"], True)
            path = screenhelp.crop(c["box"])
            if not path:
                self.say("I couldn't capture that part of the screen.")
                return
            reply = self.brain.ask(screenhelp.circle_prompt(c["q"]), image=path, timeout=60)
            text = moods.strip(split_reply_full(reply)[0]) or "I couldn't tell."
            b = c["box"]
            self._fx("bubble", b["x"], b["y"], b["w"], b["h"], text[:420])
            if self.on_card:
                self.on_card({"type": "text", "title": "About what you circled", "text": text[:600]})
            self.say(" ".join(tts.split_sentences(text)[:2]))
        except Exception as e:
            print("[circle] failed:", type(e).__name__, flush=True)
            self.say("I couldn't look at that just now.")
        finally:
            screenhelp.drop(path)
            self.on_status([], False)
            self.circle = None

    # --- 3D models -------------------------------------------------------------------------------
    def _scene_from_brain(self, subject):
        """Ask the brain for a scene spec for `subject` -> (spec or None, spoken sentence). The brain returns JSON only; holo.validate_scene decides what is drawn."""
        reply = self.brain.ask(skills.context_line() + holo.scene_prompt(subject), timeout=90)
        shown, _c, _o, _p, _m = split_reply_full(reply)
        intent = intents.validate(split_intent(reply), "3D model " + subject)
        spec = holo.validate_scene((intent or {}).get("scene")) if intent else None
        return spec, moods.strip(shown)

    def holo_real(self, m, subject):
        """Download (checked) and show a real model the user approved."""
        self.on_status(["Downloading the model"], True)
        try:
            name = models3d.get_real(m)
        except models3d.Refused as e:
            self.on_status([], False)
            self.say(str(e) + " I'll build it from basic shapes instead.")
            return self.holo_build(subject, skip_real=True)
        finally:
            self.on_status([], False)
        card = models3d.model_card(m, name)
        holo.remember(card["spec"], source=card["source"])
        self._apply_effects({"cards": [card]})
        if self.on_card:
            self.on_card(card)
        self.say(f"Here is a real model: {m['title'][:50]}. It is {m['licence']}, by {m['creator'][:40]}.")

    def holo_build(self, subject, skip_real=False):
        if not skip_real:
            try:
                found = models3d.lookup(subject)
            except Exception:
                found = []
            if found:
                m = found[0]
                if "commons" in (settings.get("models_ok") or []):
                    return self.holo_real(m, subject)
                mb = m["size"] / 1048576
                self.expect = {"kind": "model_ok", "text": json.dumps({"m": m, "subject": subject})}
                self.say(f"I found a real model: {m['title'][:50]}, from Wikimedia Commons, {mb:.1f} megabytes, licence {m['licence']}. Download it? Say yes, or no to build one from basic shapes.", wait=True)
                if self.state == "idle":
                    self._conversation_open(wait=12.0)
                return
        self.on_status(["Building a 3D model of " + subject[:40]], True)
        try:
            spec, said = self._scene_from_brain(subject)
        except Exception as e:
            print("[3d] failed:", type(e).__name__, flush=True)
            spec, said = None, ""
        finally:
            self.on_status([], False)
        if not spec:
            name = holo.demo_for(subject)
            spec = holo.demo(name) if name else None
            said = "I couldn't build that one, so here is the closest built-in model." if spec else ""
        else:
            said = (said + " This is built from basic shapes, not a real model.").strip()
        if not spec:
            self.say("I couldn't build a model of that. " + holo.HELP)
            return
        holo.remember(spec)
        self._apply_effects({"cards": [holo.scene_card(spec)]})
        if self.on_card:
            self.on_card(holo.scene_card(spec))
        self.say(" ".join(tts.split_sentences(said)[:1]) or f"Here is the {spec['title'].lower()}. Tap a part to ask about it.")

    def holo_compare(self, a, b):
        cards = []
        self.on_status([f"Building {a[:20]} and {b[:20]}"], True)
        try:
            for subj in (a, b):
                name = holo.demo_for(subj) if re.fullmatch(r"(?:a |an |the )?(?:(?:four[- ]?stroke )?engine|atom|house|water(?: molecule)?|earth|moon)", subj) else None
                spec = holo.demo(name) if name else self._scene_from_brain(subj)[0]
                if spec:
                    spec = dict(spec, title=(subj.title() + " - " + spec["title"])[:60]) if len(subj) < 30 and subj.lower() not in spec["title"].lower() else spec
                    holo.remember(spec)
                    cards.append(holo.scene_card(spec))
        except Exception as e:
            print("[3d] compare failed:", type(e).__name__, flush=True)
        finally:
            self.on_status([], False)
        if len(cards) < 2:
            self.say("I couldn't build both models to compare.")
            return
        self.holo3 = {"spec": cards[0]["spec"], "sel": None, "tour": None}
        for c in cards:
            self.on_card(c)
        self.say(f"Here are {a} and {b} side by side.")

    def holo_save(self, name):
        h = self.holo3
        if not h:
            self.say("Open a model first, then save it.")
            return
        holo.save_scene(name, h["spec"], holo.get_notes(h["spec"]["title"]))

    def holo_tour(self):
        h = self.holo3
        if not h or h.get("tour"):
            return
        self.on_status(["Preparing the tour"], True)
        try:
            steps = holo.parse_tour(self.brain.ask(holo.tour_prompt(h["spec"]), timeout=60), h["spec"])
        except Exception as e:
            print("[3d] tour failed:", type(e).__name__, flush=True)
            steps = []
        finally:
            self.on_status([], False)
        if not steps:
            self.say("I couldn't plan a tour of this one.")
            return
        t = h["tour"] = {"stop": False, "jump": 0, "expect": None}
        i = 0
        try:
            while 0 <= i < len(steps) and not t["stop"]:
                st = steps[i]
                t["expect"] = st["id"]
                opener.NOTIFY({"t": "holo", "op": "select", "id": st["id"]})
                self.say(st["say"], wait=True)
                while self.state == "speaking" and not t["stop"] and not t["jump"]:
                    time.sleep(0.1)
                if t["stop"]:
                    break
                i = max(0, i + (t["jump"] or 1))
                t["jump"] = 0
                time.sleep(0.3)
        finally:
            h["tour"] = None

    def holo_event(self, m):
        """Messages from the 3D card: opened (with its parts), a part chosen, notes changed, closed, a tour or a save asked for."""
        k, h = m.get("t"), self.holo3
        if k == "holo_open" and h and h["spec"].get("real") and m.get("parts"):
            real = [dict(p, shape="box", pos=[0, 0, 0], size=[1, 1, 1]) for p in (m.get("parts") or [])[:40] if isinstance(p, dict)]
            h["spec"] = dict(h["spec"], parts=holo.validate_scene({"title": h["spec"]["title"], "parts": real})["parts"]) if holo.validate_scene({"title": "x", "parts": real}) else h["spec"]
        elif k == "holo_open" and not h:
            self.holo3 = {"spec": holo.validate_scene({"title": m.get("title"), "parts": [dict(p, shape=p.get("shape") if p.get("shape") in holo.SHAPES else "box") for p in (m.get("parts") or [])[:40] if isinstance(p, dict)]}) or {"title": "model", "parts": []}, "sel": None, "tour": None}
        elif k == "holo_closed":
            if h and h.get("tour"):
                h["tour"]["stop"] = True
            self.holo3 = None
        elif k == "holo_sel" and h:
            h["sel"] = str(m.get("id") or "") or None
            if h.get("tour") and h["sel"] != h["tour"].get("expect"):         # you tapped a part yourself: that ends the tour
                h["tour"]["stop"] = True
                self.speaker.stop()
        elif k == "holo_notes" and h:
            holo.set_notes(h["spec"]["title"], m.get("notes"))
        elif k == "holo_tour":
            threading.Thread(target=self.holo_tour, daemon=True).start()
        elif k == "holo_save_ask" and h:
            self.expect = {"kind": "holo_name", "text": ""}
            threading.Thread(target=lambda: (self.say("What should I call this scene?", wait=True), self._conversation_open(wait=10.0)), daemon=True).start()

    def _on_play(self, gen, path):
        if getattr(self, "_draw_open", False):
            self._draw_n = getattr(self, "_draw_n", 0) + 1
            opener.NOTIFY({"t": "draw_step", "n": self._draw_n})            # one element of an open drawing per spoken sentence
        if self._timing and self._played_gen != gen:
            self._played_gen = gen
            self._timing.mark("first_audio")
            self._set("speaking")
        if self.on_level:
            try:
                with wave.open(path) as w:
                    rate = w.getframerate()
                    samples = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
                threading.Thread(target=self._envelope, args=(samples, rate, gen), daemon=True).start()
            except Exception:
                pass

    def _envelope(self, samples, rate, gen):
        """Emit the loudness of the sentence being spoken, in step with playback."""
        hop, t0 = int(rate * 0.04), time.time()
        for i in range(0, len(samples), hop):
            if gen != self.speaker.gen:
                return
            chunk = samples[i: i + hop].astype(np.float32) / 32768.0
            db = 20 * math.log10(max(float(np.sqrt((chunk * chunk).mean())), 1e-5))
            self.on_level("out", min(1.0, max(0.0, (db + 46) / 30)), db)
            time.sleep(max(0.0, t0 + (i + hop) / rate - time.time()))
        self.on_level("out", 0.0, -90.0)

    def preload(self):
        def run():
            try:
                stt.warm()
                tts.warm()
                self._set("idle")           # usable as soon as ears + voice are warm
                self.restore_timers()
                if self.handsfree_default:
                    self.set_handsfree(True)
                threading.Thread(target=self._preload_other_voices, daemon=True).start()
                self.brain.warm()
                self.brain_ready.set()
            except Exception as e:
                self._set("error", f"startup failed: {e}")
        threading.Thread(target=run, daemon=True).start()

    FAST = None

    def _fast_lines(self):
        if Assistant.FAST is None:
            Assistant.FAST = opener.ack_phrases() + ["Here it is.", "Hiding the interface. Tap the orb to bring it back.", "Louder.", "Quieter.", "Sound muted.", "Sound on.",
                                                     "Timers cancelled.", "Your timer is done.",
                                                     *[f"Timer set for {n} minute{'s' if n != 1 else ''}." for n in (1, 2, 3, 5, 10, 15, 20, 30, 45)], "Timer set for 1 hour.",
                                                     *[f"Volume {v} percent." for v in range(10, 101, 10)], "Muted. Press Super Alt M, or tap the mute icon, to wake me."]
        return Assistant.FAST

    def _preload_persona(self, n):
        """Load a persona's voice and pre-synthesise its short lines (cached on disk, so only the first time is slow). Only the active persona is done at start; the others when you switch to them."""
        try:
            p = personas.PERSONAS[n]
            tts._get(personas.voice_path(n))
            tts.prewarm(p["ack"] + p["awake"] + [p["greeting"], p["welcome_home"]], personas.voice_path(n))
            tts.prewarm(self._fast_lines(), personas.voice_path(n))
        except Exception as e:
            print("voice preload failed:", n, type(e).__name__)

    def _preload_other_voices(self):
        sounds.warm()                                                              # the chime player is connected before the first wake phrase
        self._preload_persona(self.persona)
        for n in ("rose" if self.persona == "jarvis" else "jarvis",):          # the other built-in, so the first switch is instant
            self._preload_persona(n)

    # --- push to talk -------------------------------------------------
    def grab(self, explicit_screen=False, camera_only=False):
        """The picture for "look". ONE place decides the source, in this order: the phone camera if one is saved (and reachable), else a USB webcam, else (no camera of any kind) the desktop.
        A desktop screenshot is taken ONLY when you asked for the screen ("show my screen", "look at the screen", source SCREEN/WINDOW) or when no camera exists at all, and it says so.
        A saved phone camera that cannot be reached is NEVER replaced by the desktop: it raises RuntimeError with the plain reason. Sets self.last_source to "phone camera",
        "USB camera" or "screenshot of the desktop". The USB check happens only here, only when a look was asked for (never on a timer or at start-up)."""
        if not camera_only and (explicit_screen or self.source in ("screen", "window")):
            self.last_source = "screenshot of the desktop"
            return screenshot("window" if self.source == "window" else "screen")
        if phonecam.get_url():
            try:
                p = phonecam.grab(timeout=8)
            except RuntimeError as e:
                print("[look] phone camera not reachable:", str(e)[:70], flush=True)
                raise RuntimeError(phonecam.explain(str(e)))
            self.last_source = "phone camera"
            return p
        if camera.usb_webcams():
            try:
                p = camera.capture()
            except RuntimeError as e:
                raise RuntimeError("The USB camera did not give a picture (" + str(e)[:60] + ").")
            self.last_source = "USB camera"
            return p
        if camera_only:
            self.camera_chip("No camera is set up. Add a phone camera in settings.", force=True)
            raise RuntimeError("No camera is set up. Add a phone camera in settings.")
        self.camera_chip("No camera is set up, so I looked at your screen.")
        self.last_source = "screenshot of the desktop"
        return screenshot("screen")

    def camera_chip(self, text, force=False):
        """One small quiet status chip (never a popup): when you pressed the camera button, or if CAMERA WARNINGS is on; at most once every 10 minutes."""
        if not (force or prefs.get("camera_warnings")) or time.time() - getattr(self, "_chip_t", 0) < 600:
            return
        self._chip_t = time.time()
        opener.NOTIFY({"t": "health", "msg": text})

    def camera_on(self):
        """"turn on the camera": the LOOK view opens on the camera and goes LIVE (the phone's stream through the one shared reader). Say "what do you see" to get boxes you can tap."""
        try:
            self._look_text = ""
            self._look_frame(retake=True, camera_only=True)
            if self.last_source == "phone camera":
                self.look.update(live=True, boxes=[], sel=None)
                opener.NOTIFY({"t": "look", "op": "live", "stream": "/api/look/stream"})
        except Exception as e:
            self.on_msg("sys", str(e)[:200])
            self.say(str(e)[:160])

    def look_button(self):
        """The camera button under Jarvis: one press opens the LOOK view with one sharp frame from the camera (phone first); pressed again it closes. Returns True if it opened."""
        if self.look["open"]:
            self.look_close()
            return False
        try:
            self._look_frame(retake=True, camera_only=True)
            return True
        except Exception as e:
            self.on_msg("sys", str(e)[:200])
            if str(e).startswith("I can't reach"):
                self.camera_chip("Can't reach the phone camera: is DroidCam open in front?", force=True)
            return False

    def press(self, look=False):
        if self.in_conv:                                     # a deliberate press always wins over the conversation mic
            self.conversation_end()
        if self.state not in ("idle", "speaking", "error"):
            return
        self.speaker.stop()
        self.turn += 1
        self.shot = None
        if look:
            try:
                self._look_text = ""
                self.shot = self._look_frame(retake=True)
            except Exception as e:
                self.on_msg("sys", str(e)[:200])
                self.say(str(e)[:160])
                return
        self.rec.start()
        self.t0 = time.time()
        if config.SPECULATE:
            self.ep = endpoint.Endpointer(self.rec, stt.transcribe)
            self.ep.start()
        self._set("recording", "manual press (muted)" if self.muted else ("looking + listening" if look else "listening"))

    def cancel_press(self):
        """A hold that turned out to be a drag of the orb: throw the recording away, no turn."""
        if self.state != "recording" or self.rec is None:
            return
        self.rec.stop()
        if self.ep:
            self.ep.stop_evt.set()
        self._set("idle")

    def release(self):
        if self.state != "recording":
            return
        t_rel = time.time()
        path = self.rec.stop()      # stop first so the beep can't leak into the recording
        if t_rel - self.t0 < MIN_RECORD_S or not path:   # a tap, not speech: no chime, no turn
            if self.ep:
                self.ep.stop_evt.set()
            self._set("idle")
            return
        if config.ACK:
            audio.ack("done")
        T = Timing(t_rel)
        T.mark("ack")
        threading.Thread(target=self._pipeline, args=(path, self.shot, self.turn, T, self.ep), daemon=True).start()

    def reset(self):
        self.speaker.stop()
        self.brain.reset()
        self.recent, self._carry = [], ""
        self.on_msg("sys", "New conversation.")

    def _apply_effects(self, local, T=None):
        """Everything a local/intent result asks for besides speaking: cards, windows, sends, timers... (shared by voice commands and the brain's intents)."""
        if T and (local.get("cards") or local.get("open")):
            T.mark("first_visual")
        if self.on_card:
            for c in local.get("cards", []):
                self.on_card(c)
        if local.get("ui") is not None:
            self.on_ui(bool(local["ui"]["hidden"]))
        for c in local.get("cards", []):
            if isinstance(c, dict) and c.get("type") == "explain" and c.get("kind") == "draw":
                self._draw_open, self._draw_n = True, 0
            if isinstance(c, dict) and c.get("type") == "scene3d":
                self.holo3 = {"spec": c["spec"], "sel": None, "tour": None}
        if local.get("mkt"):
            opener.NOTIFY({"t": "mkt", "ops": local["mkt"]})
        if local.get("holo_build"):
            threading.Thread(target=self.holo_build, args=(local["holo_build"],), daemon=True).start()
        if local.get("holo_compare"):
            threading.Thread(target=self.holo_compare, args=(local["holo_compare"]["a"], local["holo_compare"]["b"]), daemon=True).start()
        if local.get("holo_tour"):
            threading.Thread(target=self.holo_tour, daemon=True).start()
        if local.get("holo_save"):
            self.holo_save(local["holo_save"])
        if local.get("orb"):
            opener.NOTIFY({"t": "orb", "op": local["orb"]})
        if local.get("persona") and local["persona"] in personas.PERSONAS and local["persona"] != self.persona:
            if self.state in ("idle", "error", "loading"):
                threading.Thread(target=self.set_persona, args=(local["persona"],), daemon=True).start()
            else:
                self._pending = local["persona"]                     # after this reply, then it introduces itself
        if local.get("idle"):
            opener.NOTIFY({"t": "idle", "on": True})
        if local.get("meta"):
            threading.Thread(target=self.run_meta, args=(local["meta"],), daemon=True).start()
        if local.get("loccard") and self.on_card:
            self.on_card(maps.location_card(local["loccard"] == "today"))
        if local.get("phonecam") == "setup" and self.on_card:
            self.on_card(phonecam.setup_card())
        if local.get("phonecam") == "use":
            self.source = "phone"
        if local.get("spotify") and self.on_card:
            self.on_card(spotify.guide_card())
        if local.get("soundtest"):
            threading.Thread(target=self.run_soundtest, daemon=True).start()
        if local.get("focus") is not None:
            self.run_focus(local["focus"])
        if local.get("quiet_off"):
            threading.Thread(target=self.quiet_apply, args=(False,), daemon=True).start()
        if local.get("scene"):
            threading.Thread(target=self.run_scene, args=(local["scene"],), daemon=True).start()
        if "translate" in local:
            self.translate_to = local["translate"]
            self._translate_voice()
            print("[translate]", self.translate_to or "off", flush=True)
        if local.get("readscreen"):
            threading.Thread(target=self.run_readscreen, daemon=True).start()
        if local.get("phonesend"):
            threading.Thread(target=self.run_phone, args=(local["phonesend"],), daemon=True).start()
        if local.get("micbtn") is not None:
            threading.Thread(target=dictate.set_micbtn, args=(local["micbtn"],), daemon=True).start()
        if local.get("research"):
            threading.Thread(target=self.run_research, args=(local["research"],), daemon=True).start()
        if local.get("video"):
            threading.Thread(target=self.run_video, args=(local["video"],), daemon=True).start()
        if local.get("tool"):
            opener.NOTIFY({"t": "tool", "open": [local["tool"]]})
        if local.get("layout_open"):
            opener.NOTIFY({"t": "tool", "open": local["layout_open"]})
        if local.get("layout_save"):
            opener.NOTIFY({"t": "layout_ask", "name": local["layout_save"]})
        if local.get("calmail") and self.on_card:
            from . import calmail
            self.on_card(calmail.setup_card(local["calmail"]))
        if local.get("mic"):
            threading.Thread(target=(self.mic_test if local["mic"] == "test" else (lambda: self.say(micheck.fix()))), daemon=True).start()
        if local.get("send"):
            threading.Thread(target=self.run_send, args=(local["send"]["to"], local["send"]["what"]), daemon=True).start()
        if local.get("resend"):
            threading.Thread(target=self.resend_failed, daemon=True).start()
        if local.get("textmsg"):
            from . import textmsg
            k = local["textmsg"]["kind"]
            if k == "draft":
                d = textmsg.make_draft(local["textmsg"]["to"], local["textmsg"]["body"])
                self.on_card(textmsg.card(d))
                if textmsg.trusted(d["to"]):
                    threading.Thread(target=self.send_text_draft, daemon=True).start()
                else:
                    self.say(f"Text to {d['to']}: {d['body']}. Say send it, or tap send.", wait=False)
            elif k == "confirm":
                threading.Thread(target=self.send_text_draft, daemon=True).start()
            else:
                textmsg.clear()
                self.say("Cancelled. Nothing was sent.")
        if local.get("scancode"):
            def _sc():
                from . import scan
                try:
                    path = self.grab(camera_only=True)
                except Exception as e:
                    self.say("I couldn't get a picture: " + str(e)[:80])
                    return
                codes = scan.scan_codes(path)
                self.on_card(scan.card(codes))
                self.say(f"I found {len(codes)} code{'s' if len(codes) != 1 else ''}." if codes else "I couldn't read a code. Hold it flat and closer.")
                if len(codes) == 1 and codes[0]["type"] == "barcode":
                    self.last_object = "UPC " + codes[0]["data"]
            threading.Thread(target=_sc, daemon=True).start()
        if local.get("daylog"):
            from . import daylog
            k = local["daylog"]["kind"]
            if k == "on":
                settings.update(daylog=True); self.say("I'll keep a private day log of what you ask. Say delete my day log any time; it clears itself after 30 days.")
            elif k == "off":
                settings.update(daylog=False); self.say("Day log off. What is already in it stays until you delete it.")
            elif k == "delete":
                daylog.delete(); self.say("Your day log is deleted.")
            else:
                rows = daylog.day(local["daylog"]["offset"])
                if not daylog.enabled() and not rows:
                    self.say("I'm not keeping a day log. Say: keep a day log.")
                else:
                    self.on_card({"type": "list", "gid": "daylog", "title": "Day log", "items": [f"{time.strftime('%-I:%M %p', time.localtime(r['t']))}  {r['text']}" for r in rows] or ["Nothing logged for that day."]})
        if local.get("selftest"):
            def _st():
                from . import healthcheck
                res = healthcheck.checks()
                self.on_card(healthcheck.card(res))
                self.say(healthcheck.summary(res))
            threading.Thread(target=_st, daemon=True).start()
        if local.get("backup"):
            threading.Thread(target=self.run_backup, daemon=True).start()
        if local.get("settingsio"):
            threading.Thread(target=self.run_settingsio, args=(local["settingsio"],), daemon=True).start()
        if local.get("projects"):
            threading.Thread(target=self.run_projects, args=(local["projects"],), daemon=True).start()
        if local.get("product"):
            threading.Thread(target=self.run_product, args=(local["product"],), daemon=True).start()
        if local.get("learn"):
            threading.Thread(target=self.run_learn, args=(local["learn"],), daemon=True).start()
        if local.get("sendfile"):
            def _sf(name=local["sendfile"]):
                from . import fileshare
                path = fileshare.find(name)
                if not path:
                    self.say(f"I couldn't find a file called {name[:40]} in Downloads, Documents or Desktop.")
                    return
                r = self.run_mac({"share": path})
                if r and r.get("ok"):
                    self.say("Sending it to your Mac. It is saved there, never opened.")
                elif r:
                    self.say(r["message"][:120])
            threading.Thread(target=_sf, daemon=True).start()
        if local.get("presence"):
            self.run_presence(local["presence"])
        if local.get("claudehook"):
            from . import claudestat
            self.on_card({"type": "guide", "gid": "claudehook", "title": "CLAUDE CODE STATUS", "paged": False, "status": "nothing changed yet", "steps": [
                {"n": 1, "text": "Add these hooks to ~/.claude/settings.json (merge them into any hooks you already have). Each one runs bin/jarvis-hook, which posts to this Go on 127.0.0.1 only, with a private token.", "hint": claudestat.snippet(0)},
                {"n": 2, "text": "Codex: in its config set  notify = [\"" + str(config.ROOT) + "/bin/jarvis-hook\", \"done\"]  with JARVIS_HOOK_WHO=codex in its environment."},
                {"n": 3, "text": "You get a corner badge (WORKING / NEEDS ME / DONE / FAILED) and a coloured screen edge: yellow blinks when it needs you, red when it failed, green flashes once. ALLOW / DECLINE from Jarvis is not built: it would need a permission hook that answers the terminal prompt, and it must never be answered by voice or the model."}]})
        if local.get("findphone"):
            def _fp():
                from . import findphone
                r = findphone.ring()
                self.on_card(findphone.card(bool(r.get("ok"))))
            threading.Thread(target=_fp, daemon=True).start()
        if local.get("btspeaker"):
            from . import btspeaker
            if local["btspeaker"] == "back":
                threading.Thread(target=lambda: self.say("Back on the Go's speaker." if btspeaker.back() else "I couldn't switch the speaker."), daemon=True).start()
            else:
                self.on_card(btspeaker.card())
        if local.get("whisper") is not None:
            from . import whisper_mode
            settings.update(**local["whisper"])
            opener.NOTIFY({"t": "prefs", **prefs.public(self.persona)})
            opener.NOTIFY({"t": "whisper", "on": whisper_mode.active()})
            self.brain.set_system(personas.system_prompt(self.persona, self.cards))
        if local.get("widget"):
            threading.Thread(target=self.show_widget, args=(local["widget"],), daemon=True).start()
        if local.get("outbox"):
            threading.Thread(target=self.show_outbox, daemon=True).start()
        if local.get("macsend"):
            threading.Thread(target=self.run_macsend, args=(local["macsend"],), daemon=True).start()
        if local.get("mac") in ("pair", "show_code"):
            self.run_mac_pair(local["mac"] == "pair")
        if local.get("volume"):
            threading.Thread(target=self.run_volume, args=(local["volume"],), daemon=True).start()
        if local.get("timer"):
            self.run_timer(local["timer"])
        if local.get("mute"):
            threading.Thread(target=self.set_muted, args=(True,), daemon=True).start()
        if local.get("open"):                                   # voice command: parsed by code, then validated
            act = opener.validate(local["open"])
            if act:
                threading.Thread(target=self.run_open, args=(act, T), daemon=True).start()
        if local.get("playvideo"):
            threading.Thread(target=self.play_video_query, args=(local["playvideo"],), daemon=True).start()

    def play_video_query(self, q):
        """show_video: the first YouTube result opens as a video card in the Jarvis frame (the instant embed path), never a Google search."""
        res = video.search(q)
        if not res:
            self.say("I couldn't find a video for that.")
            return
        video.LAST["results"], video.LAST["url"] = res, res[0]["url"]
        act = opener.validate({"url": res[0]["url"], "label": res[0]["title"][:40]})
        if act:
            self.run_open(act)

    def _answer_local(self, text, local, turn, T):
        """A skill answered (no Claude): speak it, show its cards."""
        self.on_msg("you", text)
        continuity.add(text or "", local.get("speech") or "")
        opener.NOTIFY({"t": "heard", "text": (text or "")[:140], "route": "skill: " + (next((k for k in local if k not in ("speech", "cards")), "answer"))})
        for k, v in (local.get("set") or {}).items():             # a spoken preference change is applied BEFORE the reply, so "Faster." is already faster
            try:
                self.apply_pref(k, v)
            except ValueError:
                pass
        self._timing, self._played_gen = T, None
        T.mark("intent")                                          # the local matcher has answered: no brain call
        self._set("thinking")
        gen = self.speaker.begin()
        T.mark("first_token"); T.mark("first_sentence")
        for sentence in tts.split_sentences(local["speech"]):
            self.speaker.add(sentence, gen)
        self.on_msg("jarvis", local["speech"])
        self._apply_effects(local, T)
        self.recent = (self.recent + [(text[:300], local["speech"][:300])])[-6:]
        while self.speaker.busy() and self._alive(turn):
            time.sleep(0.05)
        T.mark("done")
        extras.HISTORY.append(dict(T.marks)); del extras.HISTORY[:-20]
        line = T.line()
        print("TIMING (local)", line, flush=True)
        self.on_msg("sys", "⏱ " + line)
        if local.get("expect"):
            self.expect = {"kind": local["expect"], "text": local.get("retry", "")}
        opened = self._after_reply(local["speech"], turn, gen, allow_followup=local.get("ui") is None and not local.get("open") and not local.get("mute") and not local.get("persona"))
        if self._alive(turn) and not opened:
            self._set("idle")

    # --- conversation mode ----------------------------------------------------------
    def _after_reply(self, shown, turn, gen, allow_followup=True):
        """After a reply: a question opens the mic; otherwise ask 'Anything else?' and open it. Muted: never."""
        if self.muted or not self._alive(turn):
            return False
        if not convo.is_question(shown):
            if quiet.active():
                return False                                # quiet hours: no 'Anything else?' and no open mic
            if self.translate_to:
                pass                                        # translate mode: keep the mic open for the next sentence, without 'Anything else?'
            elif not (allow_followup and self.followup and (shown or "").strip()):
                return False
            else:
                line = random.choice(personas.PERSONAS[self.persona]["followup"])
                self.speaker.add(line, gen)
                self.on_msg("jarvis", line)
                while self.speaker.busy() and self._alive(turn):
                    time.sleep(0.05)
        if not self._alive(turn) or self.muted:
            return False
        from . import wake
        return self._conversation_open(wait=wake.ANSWER_WAIT_S if (convo.is_question(shown) or self.expect) else None)

    def _conversation_open(self, wait=None):
        hf = self.hf
        if hf is None:                                        # wake phrases are off: open the mic just for this conversation
            from .wake import HandsFree
            hf = self.hf = HandsFree(self, use_kws=False, wake=False)
            self._hf_temp = True
            hf.start()
            for _ in range(40):
                if hf.stream is not None:
                    break
                time.sleep(0.05)
        ok = hf.open_conversation(self.speaker.last_end, wait)
        self.in_conv = ok
        return ok

    def conversation_end(self):
        """Close the conversation: the mic goes back to wake-word listening (or off, if it was opened only for this)."""
        self.in_conv = False
        hf = self.hf
        if hf is not None and hf.cap is not None and hf.cap.get("conv"):
            hf.cap = None
            if self.state == "recording":
                self._set("idle")
        if self._hf_temp and self.hf is not None:
            self.hf.stop()
            self.hf, self._hf_temp = None, False

    def _signoff(self, text, turn):
        """'No thanks' / 'that's all': a few words, then the mic closes and we are back to idle + wake phrases."""
        p = personas.PERSONAS[self.persona]
        self.on_msg("you", text)
        self._timing = None
        self._set("speaking")
        gen = self.speaker.begin()
        line = random.choice(p["signoff"])
        self.speaker.add(line, gen)
        self.on_msg("jarvis", line)
        while self.speaker.busy() and self._alive(turn):
            time.sleep(0.05)
        self.conversation_end()
        if self._alive(turn):
            self._set("idle")

    def set_followup(self, on):
        self.followup = bool(on)
        settings.update(followup=self.followup)

    def run_focus(self, minutes):
        """Focus timer: quiet alerts, 'five minutes left', then a break prompt. minutes=0 cancels."""
        extras.PENDING[:] = [p for p in extras.PENDING if not p.get("focus")]
        for tm in list(getattr(self, "focus_timers", [])):
            tm.cancel()
        self.focus_timers = []
        if not minutes:
            extras.save_pending()
            return
        now, total = time.time(), minutes * 60
        steps = []
        if total > 600:
            steps.append({"due": now + total - 300, "label": "Five minutes left", "text": "Five minutes left.", "soft": True, "focus": True})
        steps.append({"due": now + total, "label": "Focus time is over", "text": "Time for a break. Say start a five minute timer if you want one.", "soft": True, "focus": True})
        for st in steps:
            extras.PENDING.append(st)
            self._schedule(st)
        extras.save_pending()
        extras.log_event("focus started", f"{minutes} minutes")

    def quiet_apply(self, on):
        """Quiet hours start: dim the screen (remembering the brightness); they end: put it back."""
        if on and not getattr(self, "_dimmed", False):
            self._dim_was = scenes.brightness_get()
            scenes.brightness_set(8)
            self._dimmed = True
            extras.log_event("quiet hours", "started")
        elif not on and getattr(self, "_dimmed", False):
            if getattr(self, "_dim_was", None):
                scenes.brightness_set(self._dim_was)
            self._dimmed = False
            extras.log_event("quiet hours", "ended")

    def run_scene(self, name):
        """Run a scene's steps. Every step goes through scenes.validate_step first and is executed by fixed code."""
        if name == "__restore__":
            if scenes.SNAP["brightness"]:
                scenes.brightness_set(scenes.SNAP["brightness"])
            if scenes.SNAP["volume"] is not None:
                self.run_volume({"set": scenes.SNAP["volume"]})
            self.on_ui(False)
            self.translate_to = None
            self._translate_voice()
            extras.log_event("scene", "back to normal")
            return
        steps = [s for s in (scenes.validate_step(x) for x in scenes.load().get(name, [])) if s]
        scenes.SNAP.update(brightness=scenes.brightness_get(), volume=scenes.volume_get())
        extras.log_event("scene", name)
        for st in steps:
            try:
                d = st["do"]
                if d == "volume":
                    self.run_volume({"set": st["set"]})
                elif d == "brightness":
                    scenes.brightness_set(st["set"])
                elif d == "mute":
                    self.set_muted(bool(st["on"]))
                elif d == "hide_ui":
                    self.on_ui(bool(st["on"]))
                elif d == "timer":
                    self.run_timer({"seconds": st["seconds"], "label": st["label"], "text": (st["label"].capitalize() + ".") if st["label"] else "Your timer is done."})
                elif d == "say":
                    self.say(st["text"])
                elif d == "close_all":
                    opener.clear_embed()
                elif d == "open":
                    act = opener.validate({k: st[k] for k in ("url", "app") if k in st})
                    if act:
                        self.run_open(act)
            except Exception as e:
                print("[scene] step failed:", st.get("do"), type(e).__name__, flush=True)

    def run_readscreen(self):
        """Screenshot, local OCR; short text is read aloud and shown, anything unreadable goes to the brain with the picture."""
        try:
            path = screenshot("screen")
        except Exception as e:
            self.say("I couldn't take a screenshot: " + str(e)[:80])
            return
        text = readscreen.ocr(path)
        if not text:
            self.ask_text("Read out loud the text that is visible in this screenshot. If there is a lot, read the main part and then summarise the rest in one sentence.", image=path, display="Read this to me (looking at the picture)", local=False)
            return
        if self.on_card:
            self.on_card({"type": "text", "title": "On screen", "text": text[:2000]})
        spoken = re.sub(r"\s*\n\s*", ". ", text)
        self.say(spoken[:700] + (" ... and there is more on the card." if len(spoken) > 700 else ""))

    def run_research(self, topic):
        """Background research: a live status card while it runs, then a spoken summary, a full-screen report and a saved note."""
        extras.log_event("started research", topic[:60])
        r = research.run(topic, on_status=lambda lines: self.on_status(lines, True))
        extras.log_event("finished research" if not r.get("error") else "research failed", "" if r.get("error") else f"{r['elapsed']:.0f} seconds")
        self.on_status([], False)
        if r.get("error"):
            self.say(r["error"])
            return
        ok = not r["unverified"] and not r["bad_refs"]
        if self.on_card:
            self.on_card(r["card"])
        line = r["summary"] + ("" if ok else f" Careful: {len(r['unverified']) + len(r['bad_refs'])} source{'s were' if len(r['unverified']) + len(r['bad_refs']) != 1 else ' was'} not verified.")
        self.on_msg("sys", f"Research saved: {r['path']} ({r['elapsed']:.0f}s). " + ("All sources verified." if ok else "Some sources were NOT verified; see the report."))
        self.say(line + " Say send the report to my Mac if you want it there.")

    def run_video(self, v):
        """Video features. The model never runs anything: yt-dlp / ffmpeg / whisper are called from video.py with fixed arguments."""
        kind = v["kind"]
        if kind == "find":
            res = video.search(v["query"])
            if not res:
                self.say("I couldn't find any videos for that.")
                return
            video.LAST["results"] = res
            if self.on_card:
                self.on_card({"type": "list", "title": "Videos: " + v["query"][:40], "items": [f"{i}. {r['title'][:70]}  ({r['channel'][:24]}, {video.fmt_dur(r['duration'])})" for i, r in enumerate(res, 1)]})
            self.say(f"I found {len(res)}. Say open the first one, or send the second one to my Mac.")
            return
        if kind == "pick":
            r = video.LAST["results"][v["n"] - 1]
            video.LAST["url"] = r["url"]
            if v.get("mac"):
                self.run_mac({"url": r["url"]})
            else:
                act = opener.validate({"url": r["url"], "label": r["title"][:40]})
                if act:
                    self.run_open(act)
            return
        url = v.get("url") or opener.EMBED.get("page_url") or video.LAST["url"]
        if not url:
            self.say("Give me a link, or open the page first, then ask again.")
            return
        video.LAST["url"] = url
        if kind == "page":
            self.ask_text(f"Summarise the web page at {url} in three short spoken sentences. Fetch it first.", display="Summarise this page: " + url[:80], local=False)
            return
        data = video.gather(url)
        if data.get("error"):
            self.say(data["error"] + (" Say open that on my Mac and I'll send it." if data.get("blocked") else ""))
            return
        sheet = data.get("sheet")
        self.ask_text(video.prompt_for(data), image=sheet, display=f"Video: {data['title'][:70]}", local=False)

    def run_macsend(self, what):
        if what == "report_phone":
            if not research.LAST.get("summary"):                      # no report: "send that to my phone" means whatever is open (a map, a page, a video)
                url = opener.EMBED.get("page_url") or video.LAST["url"]
                if url:
                    self.run_phone({"title": "From Jarvis", "message": url[:200], "link": url})
                else:
                    self.say("There is nothing to send yet.")
                return
            link = research.LAST.get("link")
            self.run_phone({"title": "Research: " + (research.LAST.get("title") or "")[:50], "message": research.LAST["summary"][:900], **({"link": link} if link else {})})
            return
        if what == "report":
            if not research.LAST["path"]:
                self.say("There is no report yet. Ask me to research something first.")
                return
            self.run_mac({"file": research.LAST["path"]})
        else:
            url = opener.EMBED.get("page_url") or video.LAST["url"]
            if not url:
                self.say("There is nothing open to send.")
                return
            self.run_mac({"url": url})

    def run_send(self, to, what):
        """"send that to my phone / Mac / computer": works out what "that" is, sends it, and says what it sent."""
        item = outbox.resolve(self, what, to)
        if not item:
            self.say("There is nothing to send yet." if what == "current" else f"I don't have {'a' if what != 'report' else 'a'} {what} to send.")
            return
        if to == "mac":
            if item["kind"] == "url":
                r = self.run_mac({"url": item["value"], "label": item["label"]})
            elif item["kind"] == "file":
                r = self.run_mac({"file": item["value"]})
            else:
                r = self.run_mac({"file": outbox.text_to_note_file(item["value"], item["label"])})
        else:
            if item["kind"] == "url":
                r = self.run_phone({"title": "From Jarvis", "message": item["label"].capitalize(), "link": item["value"]})
            elif item["kind"] == "file":
                r = self.run_phone({"file": item["value"], "title": "From Jarvis", "message": item["label"].capitalize()})
            else:
                r = self.run_phone({"title": item["label"].capitalize()[:60], "message": item["value"][:900], **({"link": item["link"]} if item.get("link") else {})})
        if r and r.get("ok"):
            self.say(f"Sent {item['label']} to your {'Mac' if to == 'mac' else 'phone'}.")

    def send_text_draft(self):
        """Only here, only after your confirmation: the compose link goes to your phone (you choose the person and press send there)."""
        from . import notifier, textmsg
        d = textmsg.take_confirmed()
        if not d:
            self.say("That draft expired. Ask me again.")
            return
        r = phone.send({"title": f"Text to {d['to']}"[:60], "message": "Tap to open Messages with your text ready.", "sms": d["body"]})
        self.say(f"Done. Tap the notification on your phone to open the text to {d['to']}; I can't press send for you." if r.get("ok") else "I couldn't reach your phone: " + r["message"][:60])

    def run_backup(self):
        """One zip of settings (no secrets), notes, lists, projects, parts bin, flashcards and the day log in exports/, offered to the Mac. Secrets are excluded."""
        import zipfile
        from . import settingsio
        settingsio.EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        dst = settingsio.EXPORT_DIR / f"jarvis-backup-{time.strftime('%Y%m%d-%H%M')}.zip"
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
            try:
                z.write(settingsio.export(str(settingsio.EXPORT_DIR / "settings-in-backup.json")), "settings.json")
            except OSError:
                pass
            for f in ("notes.txt", "list.json", "projects.json", "learn.json", "daylog.jsonl", "memory.json", "reminders.json"):
                p = config.DATA / f
                if p.is_file():
                    z.write(p, f)
        try:
            os.unlink(settingsio.EXPORT_DIR / "settings-in-backup.json")
        except OSError:
            pass
        self.on_card({"type": "text", "title": "Backup", "text": f"Saved to {dst}. Secrets (keys, tokens, the phone topic, the Mac code) are not inside. Say: send the file backup to my Mac."})
        self.say("Backed up. No secrets are inside the file.")

    def run_settingsio(self, r):
        from . import settingsio
        k = r["kind"]
        if k == "export":
            self.say("Exported. The file has no secrets: " + os.path.basename(settingsio.export()))
        elif k == "import":
            files = sorted(settingsio.EXPORT_DIR.glob("settings-export-*.json"))
            if not files:
                self.say("Put a settings export file in the exports folder first.")
                return
            ok, bad = settingsio.import_file(str(files[-1]))
            opener.NOTIFY({"t": "prefs", **prefs.public(self.persona)})
            self.say(f"Imported {len(ok)} settings." + (f" {len(bad)} were refused." if bad else ""))
        elif k == "reset":
            gone = settingsio.reset_one(r["name"])
            opener.NOTIFY({"t": "prefs", **prefs.public(self.persona)})
            self.say("Reset." if gone else "I don't have a setting called that.")

    def run_projects(self, r):
        from . import projects
        k = r["kind"]
        if k == "have":
            added = projects.add_to_bin(r["text"])
            self.say("Added " + ", ".join(f"{a['qty']} {a['name']}" for a in added[:4]) + " to your parts." if added else "I didn't catch the parts.")
        elif k == "bin":
            b = projects.bin_items()
            self.on_card({"type": "list", "gid": "bin", "title": "My parts", "items": [f"{x['qty']}× {x['name']}" for x in b] or ["Nothing yet. Say: I have a Raspberry Pi 4 and two servos."]})
            self.say(f"You have {sum(x['qty'] for x in b)} parts." if b else "Your parts bin is empty.")
        elif k == "unbin":
            self.say("Removed." if projects.remove_from_bin(r["name"]) else "I didn't find that in your parts.")
        elif k == "plan":
            self.on_status(["Drafting a parts list"], True)
            plan = projects.draft_parts_list(r["topic"])
            self.on_status([], False)
            if not plan.get("parts"):
                self.say("I couldn't draft a parts list for that.")
                return
            self._last_plan = (r["topic"], plan)
            self.on_card(projects.parts_card(r["topic"], plan))
            have, need = projects.split_have(plan["parts"])
            self.say(f"{len(need)} parts to get" + (f", and you already have {len(have)}" if have else "") + ". The list is on screen. Prices appear only when a store page shows them.")
        elif k == "save":
            lp = getattr(self, "_last_plan", None)
            if not lp:
                self.say("There is no project to save yet. Ask for a parts list first.")
                return
            projects.save_project(r.get("name") or lp[0], lp[1])
            self.say(f"Saved the {r.get('name') or lp[0]} project.")
        elif k == "list":
            ps = projects.list_projects()
            self.on_card({"type": "list", "gid": "projects", "title": "My projects", "items": [p["name"] for p in ps] or ["No saved projects yet."]})
            self.say(f"You have {len(ps)} saved project{'s' if len(ps) != 1 else ''}." if ps else "No saved projects yet.")
        elif k == "open":
            p = projects.get_project(r["name"])
            if not p:
                self.say("I don't have a project called that.")
                return
            self._last_plan = (p["name"], p["plan"])
            self.on_card(projects.parts_card(p["name"], p["plan"]))
            self.say(f"Here is {p['name']}.")

    def run_product(self, r):
        from . import product
        name = r.get("name") or getattr(self, "last_object", "")
        if not name:
            self.say("Which product? Say: where can I buy a Raspberry Pi 4.")
            return
        self.on_status([f"Looking for {name[:30]}"], True)
        rows = product.lookup(name)
        self.on_status([], False)
        if not rows:
            self.say("I couldn't find stores for that.")
            return
        self.on_card(product.card(name, rows))
        priced = [x for x in rows if x["price"]]
        if r.get("price") and priced:
            self.say(f"The lowest I could confirm is {priced[0]['price']} at {priced[0]['store']}, as of {priced[0]['asof']}.")
        elif r.get("price"):
            self.say("I couldn't confirm a price on any store page.")
        else:
            self.say("I put prices and where to buy it on screen.")

    def run_learn(self, r):
        from . import learn
        k = r["kind"]
        if k in ("make", "wrong"):
            self.on_status(["Making your cards"], True)
            cards = learn.make(r["topic"]) if k == "make" else learn.wrong_deck()
            self.on_status([], False)
            c = learn.start(r.get("topic") or "what you missed", cards) if cards else None
            if c:
                self.on_card(c)
                self.say(f"{len(cards)} cards. Question one: {cards[0]['q']}")
            else:
                self.say("I couldn't make cards for that." if k == "make" else "You have no missed cards. Nice.")
        elif k in ("next", "prev"):
            c = learn.move(1 if k == "next" else -1)
            if c:
                self.on_card(c)
                self.say(c["q"])
        elif k == "forget":
            learn.forget()
            self.say("Your flashcards and missed list are deleted.")

    def presence_on_change(self, present, was):
        """Called by presence.py when you sit down or leave. Delivers the reminders that were waiting for you, once."""
        from . import notifier, presence
        opener.NOTIFY({"t": "presence", "on": presence.STATE["on"], "paused": presence.STATE["paused"], "present": present})
        if present and was is False:
            waiting = presence.take_desk_reminders()
            if waiting:
                sounds.play("done")
                self.on_card({"type": "list", "gid": "deskrem", "title": "Welcome back", "items": [w["label"] for w in waiting]})
                notifier.send("reminders", "Reminders waiting", f"{len(waiting)} reminder{'s' if len(waiting) != 1 else ''} for you at your desk.")
                if settings.get("welcome_speak"):
                    self.say("Welcome back. " + ". ".join(w["label"] for w in waiting[:2]) + ".")
            elif settings.get("welcome_card"):
                self.on_card({"type": "text", "gid": "deskrem", "title": "Welcome back", "text": "Welcome back."})

    def run_presence(self, r):
        from . import presence
        k = r["kind"]
        if k == "remind":
            presence.add_desk_reminder(r["label"])
            self.say("Okay, I'll tell you when you're back at your desk." + ("" if presence.STATE["on"] else " Desk presence is off, so turn it on for that to work."))
        elif k == "on":
            if presence.start(self.presence_on_change, lambda: bool(self.look.get("open"))):
                settings.update(desk_presence=True)
                self.say("Desk presence is on. A small dot shows while the camera is watching for movement. No faces, nothing recorded.")
            else:
                self.say("I can't turn that on: " + (presence.STATE["err"] or "no camera") + ".")
        elif k == "off":
            presence.stop(); settings.update(desk_presence=False); self.say("Desk presence is off.")
        elif k in ("pause", "resume"):
            presence.pause(k == "pause"); self.say("Paused." if k == "pause" else "Watching again.")
        opener.NOTIFY({"t": "presence", "on": presence.STATE["on"], "paused": presence.STATE["paused"], "present": presence.STATE["present"]})

    def show_widget(self, name):
        """The widgets (PROMPT26 0.6): music, chat, weather, timers, notes, today, news. Each opens at once from cached data."""
        from . import actions, buddy, extras, news, weather
        if name == "map":
            self.on_card({"type": "map", "title": "Map"})
        elif name == "music":
            actions.run("music_open", {}, self)
        elif name == "chat":
            self.on_card({"type": "chat", "gid": "chat", "title": "CHAT", "wide": 420, "small": True})
        elif name == "weather":
            w = weather.where()
            if w:
                self.on_card(weather.card(*w, imperial=settings.get("units") == "imperial"))
        elif name == "timers":
            self.on_card(buddy.reminders_card())
        elif name == "notes":
            items = extras.list_items()
            notes = extras.search_notes("", 8) if False else []
            try:
                notes = [ln for ln in open(extras.NOTES(), encoding="utf-8").read().splitlines()][-8:][::-1]
            except OSError:
                pass
            self.on_card({"type": "list", "gid": "notes", "title": "Notes and list", "items": (["LIST: " + i for i in items[:10]] + notes) or ["Nothing yet. Say: note, and what to remember; or add milk to my list."]})
        elif name == "today":
            self.on_card(self.today_card())
        elif name == "news":
            c = news.card("world")
            if c:
                self.on_card(c)

    def today_card(self):
        from . import buddy, extras, weather
        lines = [time.strftime("%A %-d %B")]
        try:
            w = weather.where()
            if w:
                c = weather.card(*w, imperial=settings.get("units") == "imperial")
                lines.append(f"Weather: {c['temp']}{c['unit']}, {c['desc']}, high {c['days'][0]['hi']}, rain {c['pop']}%")
        except Exception:
            pass
        pend = [p for p in extras.PENDING if p["due"] > time.time()]
        for p in sorted(pend, key=lambda x: x["due"])[:5]:
            lines.append(f"{time.strftime('%-I:%M %p', time.localtime(p['due']))}  {p['label'] or 'timer'}")
        lines += ["LIST: " + i for i in extras.list_items()[:4]]
        return {"type": "list", "gid": "today", "title": "Today", "items": lines}

    def show_outbox(self):
        L = outbox.lines(10)
        self._meta_card("What I sent", L or ["Nothing sent yet."], [{"label": "RESEND LAST FAILED", "action": "outbox_resend"}] if any("FAILED" in x or "NOT OPENED" in x for x in L) else [])

    def resend_failed(self):
        for r in outbox.recent(20):
            if r["status"] in ("failed", "dropped") and r.get("payload"):
                (self.run_mac if r["to"] == "mac" else self.run_phone)(r["payload"])
                return
        self.say("Nothing failed.")

    def run_mac_pair(self, new):
        """'pair my Mac' / 'show the pairing code': the code goes to the screen ONLY (a card that removes itself); never spoken, logged or printed."""
        if new:
            code = mac.pair()
        else:
            code = mac.current_code()
        if not code:
            self.on_msg("sys", "The code is only kept until the server restarts. Say 'pair my Mac' to make a new one.")
            return
        if self.on_card:
            self.on_card(mac.guide_card(code))
        threading.Thread(target=self.mac_watch, daemon=True).start()
        companion.start(self)

    # --- LOOK: the full-screen view with labelled boxes -------------------------------------------
    def _look_frame(self, retake=True, camera_only=False):
        """A fresh picture (the camera: phone first, one sharp frame) or, while the LOOK view is frozen on one, that same picture again."""
        if not retake and self.look["open"] and self.look["path"] and os.path.exists(self.look["path"]):
            return self.look["path"]
        explicit = bool(re.search(r"\b(?:my screen|the screen|my desktop|the desktop)\b", getattr(self, "_look_text", "") or "", re.I))
        path = self.grab(explicit_screen=explicit, camera_only=camera_only)
        self.on_msg("sys", f"Looking through the {self.last_source}.")
        if self.look["open"] and self.look.get("source") == self.last_source:      # already open on this source: just swap the frame, no re-mount
            self.look.update(path=path, live=False, boxes=[], sel=None)
            opener.NOTIFY({"t": "look", "op": "frame", "src": "/shot/" + os.path.basename(path), "source": self.last_source})
            return path
        self.look.update(path=path, source=self.last_source, live=False, boxes=[], sel=None)
        opener.NOTIFY({"t": "look", "op": "open", "src": "/shot/" + os.path.basename(path), "source": self.last_source, "boxes": []})
        self.look["open"] = True
        return path

    def _look_show(self, boxes, path):
        self.look["boxes"], self.look["sel"] = boxes, None
        opener.NOTIFY({"t": "look", "op": "boxes", "boxes": boxes})
        if boxes and self.look.get("source") == "phone camera":                 # the view stays LIVE under the boxes (no freeze); the boxes belong to the frame they were found on and fade as time passes
            self.look["live"] = True
            opener.NOTIFY({"t": "look", "op": "live", "stream": "/api/look/stream", "keep": True})

    def _look_command(self, cmd, text, turn, T):
        op = cmd["op"]
        say = ""
        if op == "clear":
            self.look["boxes"], self.look["sel"] = [], None
            opener.NOTIFY({"t": "look", "op": "boxes", "boxes": []}); say = "Boxes cleared."
        elif op == "freeze":
            self.look["live"] = False
            opener.NOTIFY({"t": "look", "op": "freeze"}); say = "Frozen."
        elif op == "live":
            self.look.update(live=True, boxes=[], sel=None)
            opener.NOTIFY({"t": "look", "op": "live", "stream": "/api/look/stream" if self.look["source"] == "phone camera" else None}); say = "Live."
        elif op == "close":
            self.look_close(); say = "Closed."
        elif op == "select":
            n = cmd["n"] if cmd["n"] > 0 else len(self.look["boxes"])
            if not 1 <= n <= len(self.look["boxes"]):
                say = "There is no box " + str(n) + "."
            else:
                self.look_select(n - 1, speak=cmd.get("speak", False))
                say = "" if cmd.get("speak") else f"{self.look['boxes'][n - 1]['name'].capitalize()}."
        self._answer_local(text, {"speech": say} if say else {"speech": ""}, turn, T)

    def look_select(self, i, speak=False):
        """Tap or voice select: highlight the box and fill its info card with a short description from the brain (shown, and spoken when asked by voice)."""
        if not 0 <= i < len(self.look["boxes"]):
            return
        self.look["sel"] = i
        b = self.look["boxes"][i]
        self.last_object = str(b.get("name", ""))[:60]                 # "where can I get one?" refers to what you selected
        opener.NOTIFY({"t": "look", "op": "select", "i": i, "text": ""})
        def go():
            try:
                q = (f"[LOOK follow-up: the picture is attached. In ONE short sentence (max 18 words), what is the {b['name'].lower()} in the box at x={b['x']}, y={b['y']}, w={b['w']}, h={b['h']}? "
                     "No JSON, no preamble.]")
                r = self.brain.ask(q, image=self.look["path"]) if self.look["path"] else ""
            except Exception as e:
                r = ""
                print("[look] describe failed:", type(e).__name__, flush=True)
            r = re.sub(r"\s+", " ", moods.strip(r or ""))[:200]
            opener.NOTIFY({"t": "look", "op": "select", "i": i, "text": r})
            if speak and r:
                self.say(r)
        threading.Thread(target=go, daemon=True).start()

    def look_close(self):
        self.look.update(open=False, live=False, boxes=[], sel=None)
        opener.NOTIFY({"t": "look", "op": "close"})

    def look_live_frame(self):
        """LIVE without a stream address (a USB webcam or the screen): a fresh picture about every two seconds, only while the LOOK view is open."""
        path = self.grab()
        self.look["path"] = path
        return path

    def _meta_card(self, title, lines, buttons=None, status=""):
        if self.on_card:
            self.on_card({"type": "guide", "gid": "meta", "title": title, "full": False, "paged": False, "status": status, "buttons": buttons or [],
                          "steps": [{"n": i + 1, "text": ln} for i, ln in enumerate(lines)]})

    def run_meta(self, kind):
        """Update / backup / restore / improve / publish. Each one that changes anything waits for a button or a spoken "apply"."""
        from . import publish, updater
        try:
            if kind == "tour":
                self.on_card({"type": "guide", "gid": "tour", "title": "Quick tour", "full": True, "paged": True, "status": "", "steps": [
                    {"n": 1, "text": "Tell me where you are, so maps and \"near me\" work.", "buttons": [{"label": "SET MY LOCATION", "action": "tour_loc"}], "hint": "You can type your address instead of saying it."},
                    {"n": 2, "text": "Say \"Hey Jarvis\" (or hold SUPER+J) and ask anything: the weather, directions, \"show me pictures of a red panda\", \"remind me in ten minutes\".", "hint": "Say \"that's wrong\" if I get something wrong, and \"improve yourself\" later."},
                    {"n": 3, "text": "Point a camera and say \"look at this\". Tap a box to ask about it.", "buttons": [{"label": "TRY LOOK NOW", "action": "tour_look"}]},
                    {"n": 4, "text": "Everything else is in MENU and SETTINGS: terminal, files, the iPhone voice, pairing your Mac, the clock.", "buttons": [{"label": "DONE", "action": "tour_done"}]}]})
            elif kind == "voicecard":
                from . import voiceapi
                self.on_card(voiceapi.guide_card(voiceapi.is_on()))
            elif kind == "check":
                c = updater.check()
                self._meta_card("Updates", [c["message"]] + c["changes"], [{"label": "UPDATE NOW", "action": "update_now"}] if c.get("behind") and not c.get("dirty") else [])
            elif kind == "update":
                ok, msg = updater.update()
                self._meta_card("Update", [msg])
                if ok and "Updated" in msg:
                    time.sleep(2)
                    updater.restart_server()
            elif kind == "backup":
                self._meta_card("Backup", ["Saved " + os.path.basename(updater.backup()) + " in ~/jarvis-backups (settings, pairings, memory, notes)."])
            elif kind == "restore":
                ok, msg = updater.restore()
                self._meta_card("Restore", [msg])
            elif kind == "improve":
                self._meta_card("Improve yourself", ["Working on a copy of my code (a few minutes). Nothing changes until you approve."])
                r = improve.propose()
                lines = [r["message"]] + ([r.get("summary", "")] if r.get("summary") else []) + ([f"Files: {', '.join(r['files'])}  ({r['diff_lines']} lines)", f"Checks: compiles {r['checks']['compiles']}, imports {r['checks']['imports']}"] if r.get("files") else [])
                self._meta_card("Improve yourself", lines, [{"label": "APPLY", "action": "improve_apply"}, {"label": "DISCARD", "action": "improve_discard"}] if r.get("ok") else [])
            elif kind == "improve_apply":
                ok, msg = improve.apply()
                self._meta_card("Improve yourself", [msg])
                if ok:
                    time.sleep(2)
                    updater.restart_server()
            elif kind == "improve_discard":
                self._meta_card("Improve yourself", [improve.discard()])
            elif kind == "publish":
                r = publish.prepare()
                if r["problems"]:
                    self._meta_card("Public update", ["STOPPED: private data would go public:"] + [f"{f}: {w}" for f, w in r["problems"][:6]] + ["Nothing was copied."])
                else:
                    self._meta_card("Public update", [f"{len(r['changes'])} file(s) would change in the public repository.", "The scan found no private data."] + [c[:90] for c in r["changes"][:8]],
                                    [{"label": "PUSH TO GITHUB", "action": "publish_push"}] if r["changes"] else [])
        except Exception as e:
            print("[meta] failed:", type(e).__name__, flush=True)
            self._meta_card("Something failed", [type(e).__name__ + ": " + str(e)[:120]])

    def look_photo(self, data, kind):
        """A photo from the phone (voiceapi /look): shown in the Look view with boxes, answered with ONE short sentence. The picture exists only in the RAM folder while the brain
        looks at it (the page gets a small copy to display) and is deleted afterwards, unless "keep phone photos" is on."""
        import base64
        from . import voiceapi
        path = voiceapi.to_jpeg(data, kind, screenhelp._ramdir())
        if not path:
            return "I could not read that photo." + (" HEIC needs the heif-convert program on the Go." if kind == "heic" and not shutil.which("heif-convert") else "")
        try:
            b64 = base64.b64encode(open(path, "rb").read()).decode()
            self.look.update(path=None, source="iPhone photo", live=False, boxes=[], sel=None, open=True)
            opener.NOTIFY({"t": "look", "op": "open", "src": "data:image/jpeg;base64," + b64, "source": "iPhone photo", "boxes": []})
            q = skills.context_line() + look.PROMPT + "Reply with ONE short plain sentence about what is in the photo, then the JSON line."
            reply = self.brain.ask(q, image=path, timeout=60)
            shown, _c, _o, _p, _m = split_reply_full(reply)
            shown = moods.strip(shown) or "I couldn't tell what that is."
            boxes = look.validate_boxes(look.extract(reply) or [])
            self._look_show(boxes, None)
            if prefs.get("keep_phone_photos"):
                config.SHOTS_DIR.mkdir(exist_ok=True)
                keep = config.SHOTS_DIR / f"phone-{int(time.time())}.jpg"
                shutil.copyfile(path, keep)
                self.look["path"] = str(keep)
            if prefs.get("phone_voice_speak"):
                self.say(shown)
            return shown
        except Exception as e:
            print("[look-photo] failed:", type(e).__name__, flush=True)
            return "I couldn't look at that photo."
        finally:
            screenhelp.drop(path)

    def phone_ask(self, text):
        """A request that came from the iPhone endpoint -> ONE short plain sentence. Same router as voice on the Go, but visual effects (windows, cards) are not opened on a screen nobody is
        watching, nothing is spoken on the Go unless "speak on the Go too" is on, and no terminal or phone control is reachable from here."""
        from . import prefs
        local = skills.handle(text, self.persona)
        if local:
            eff = {k: v for k, v in local.items() if k in ("timer", "volume", "mute", "scene", "focus", "translate", "set")}
            try:
                self._apply_effects(eff, None)
            except Exception:
                pass
            if prefs.get("phone_voice_speak") and local.get("speech"):
                self.say(local["speech"])
            return local.get("speech") or "Done."
        q = ("[PHONE REQUEST: reply with ONE short plain sentence, no JSON, no markdown. If you need the user's answer, end with a question mark.]\n" + skills.context_line() + memory.context(text) + text)
        reply = self.brain.ask(q)
        shown, _c, _o, _p, _m = split_reply_full(reply)
        shown = moods.strip(shown)
        if prefs.get("phone_voice_speak") and shown:
            self.say(shown)
        return shown or "OK."

    def _look_memory_command(self, cmd, text, turn, T):
        op = cmd["op"]
        if op == "find":
            ent = lookmem.find(cmd["what"])
            if not ent:
                self._answer_local(text, {"speech": f"I don't remember seeing {cmd['what']}. Say: remember this, while I'm looking at the place."}, turn, T)
                return
            names = ", ".join(n.lower() for n in ent["names"][:4])
            opener.NOTIFY({"t": "look", "op": "open", "src": "/lookmem/" + ent["file"], "source": "memory", "boxes": []})
            self.look.update(open=True, path=str(lookmem.DIR / ent["file"]), source="memory", boxes=[], sel=None)
            opener.NOTIFY({"t": "look", "op": "freeze"})
            self._answer_local(text, {"speech": f"I saw it {lookmem.when(ent['t'])}, in the picture I saved as {ent['label']}." + (f" It showed {names}." if names else "")}, turn, T)
        elif op == "read":
            threading.Thread(target=self.look_read, daemon=True).start()
            self._answer_local(text, {"speech": "Reading it."}, turn, T)
        elif op == "watch":
            self._answer_local(text, {"speech": self.start_watch(cmd["cond"])}, turn, T)
        else:
            self._watch_stop = True
            self._answer_local(text, {"speech": "I stopped watching."}, turn, T)

    def look_read(self):
        """Local OCR of the picture (the open LOOK frame, else a fresh one): a card you can copy, send to the Mac, or turn into a list."""
        from . import readscreen
        try:
            path = self._look_frame(retake=not self.look["open"])
        except Exception as e:
            self.say("I couldn't get a picture.")
            return
        txt = readscreen.ocr(path)
        if not txt:
            self.say("I couldn't find readable text in that picture.")
            return
        self._last_read = txt
        self.on_card({"type": "guide", "gid": "readtext", "title": "Text I read", "full": False, "paged": False, "status": "", "steps": [{"n": 1, "text": txt[:900]}],
                      "buttons": [{"label": "SEND TO MAC", "action": "read_to_mac"}, {"label": "ADD TO LIST", "action": "read_to_list"}, {"label": "SEND TO PHONE", "action": "read_to_phone"}]})
        self.say(" ".join(txt.split())[:220])

    def start_watch(self, cond):
        """Watch a camera for ONE condition (every 20 s, at most 10 minutes, cameras only)."""
        if getattr(self, "_watching", False):
            return "I'm already watching for something. Say: stop watching."
        if not (phonecam.get_url() or camera.usb_webcams()):
            return "I need a camera for that, and I don't have a phone camera set up or a USB webcam. I won't use your screen."
        self._watching, self._watch_stop = True, False
        threading.Thread(target=self._watch_loop, args=(cond[:120],), daemon=True).start()
        return f"Okay. I'll check every twenty seconds for up to ten minutes and tell you when {cond}."

    def _watch_loop(self, cond):
        from . import brain as _b
        t0, bot = time.time(), _b.OneShotBrain(system="You look at one picture and answer ONLY the word YES or NO.")
        try:
            while time.time() - t0 < lookmem.WATCH_MAX_S and not self._watch_stop:
                try:
                    path = phonecam.grab(timeout=3) if phonecam.get_url() else camera.capture()
                    ans = bot.ask(f"Is this true in the picture right now: {cond}? Reply only YES or NO.", image=path)
                except Exception as e:
                    print("[watch] check failed:", type(e).__name__, flush=True)
                    ans = "NO"
                bot.reset()
                if ans.strip().upper().startswith("YES"):
                    self.say(f"Heads up: {cond}.")
                    self.on_card({"type": "text", "title": "I noticed", "text": cond})
                    if phone.get_topic(create=False):
                        self.run_phone({"title": "Jarvis noticed", "message": cond[:200]})
                    return
                time.sleep(lookmem.WATCH_EVERY_S)
            if not self._watch_stop:
                self.say(f"I watched for ten minutes and did not see that {cond}.")
        finally:
            self._watching = False

    def mic_test(self):
        pcm = micheck.ring_pcm(self, 5)
        r = micheck.analyze(pcm) if pcm is not None else {"verdict": "nodata", "advice": "The microphone is not listening right now (muted or hands-free is off).", "rms": 0, "peak": 0, "clipped_pct": 0}
        self._meta_card("Microphone test", [r["advice"], f"level {r['rms']}, peak {r['peak']}, clipped {r['clipped_pct']}%  (speak normally while you run this)"],
                        [{"label": "FIX THE MIC", "action": "mic_fix"}] if r["verdict"] == "clipping" else [{"label": "TEST AGAIN", "action": "mic_test"}])
        return r

    def mic_watch(self):
        """Every 10 minutes: if the always-on microphone has been clipping, say so quietly (a chip), once."""
        pcm = micheck.ring_pcm(self, 8)
        if pcm is not None:
            r = micheck.analyze(pcm)
            if r["verdict"] == "clipping" and not getattr(self, "_mic_warned", False):
                self._mic_warned = True
                opener.NOTIFY({"t": "health", "msg": "Microphone is clipping: say 'fix the mic'"})
            elif r["verdict"] == "ok":
                self._mic_warned = False

    def term_focus(self, on):
        """The terminal has keyboard focus: nothing you type may wake or trigger Jarvis, so hands-free listening is muted while it does (unless "wake word in the terminal" is on)."""
        from . import prefs
        if on and not prefs.get("term_wake") and not self.muted and not getattr(self, "_term_muted", False):
            self._term_muted = True
            threading.Thread(target=self.set_muted, args=(True,), daemon=True).start()
        elif not on and getattr(self, "_term_muted", False):
            self._term_muted = False
            threading.Thread(target=self.set_muted, args=(False,), daemon=True).start()

    def apply_pref(self, key, value, sample=False):
        """One validated preference change (from the page or by voice): saved, applied to the next sentence, and pushed to every open page."""
        v = prefs.clean(key, value)
        if key == "volume":
            self.run_volume({"set": v})
        elif key == "speed":
            settings.update(**{"speed_" + self.persona: v})
            tts.set_speed(personas.voice_path(self.persona), v)
        else:
            settings.update(**{key: v})
        opener.NOTIFY({"t": "prefs", **prefs.public(self.persona)})
        if key in ("chime_volume", "chimes") and v:
            sounds.play("wake", force=True)                    # hear what you just set
        if sample and key == "speed" and not self.muted:
            gen = self.speaker.begin()
            self.speaker.add("This is how fast I speak now.", gen)
        return v

    def ask_for_location(self):
        """CHANGE on the map card: ask out loud, then keep the mic open (no wake word) for the answer."""
        self.expect = {"kind": "settemp", "text": ""}
        gen = self.speaker.begin()
        self.speaker.add("Where are you right now?", gen)
        while self.speaker.busy():
            time.sleep(0.05)
        self._conversation_open(wait=8.0)

    def mac_notify(self, state, text):
        opener.NOTIFY({"t": "guide", "gid": "mac", "state": state, "status": text})
        if state == "delivered":
            sounds.play("delivered")
        if state == "dropped" and self.on_card:                          # never fail silently: say why the Mac refused it
            self.on_card({"type": "text", "title": "Mac did not open it", "text": text})

    def mac_watch(self):
        mac.STATE["connected"] = 0.0
        mac.watch(self.mac_notify)

    def run_mac(self, payload):
        """Send a link or file to the Mac (signed). Never prints the code, topic or key."""
        r = mac.send(payload)
        print("[mac]", r["message"], flush=True)
        lbl = str(r.get("label") or payload.get("url") or os.path.basename(str(payload.get("file") or "")) or "item")[:70]
        if r["ok"]:
            sounds.play("send")
            rid = outbox.log("mac", lbl, "sent", payload=payload, msgid=r.get("id"))
        else:
            rid = outbox.log("mac", lbl, "failed", r["message"], payload=payload)
            if r["message"].startswith("could not reach"):
                outbox.retry_later(lambda: ((lambda q: (q["ok"], q["message"]))(mac.send(payload))), rid)
        extras.log_event("sent to your Mac" if r["ok"] else "could not send to your Mac", r.get("label") or r["message"] if not r["ok"] else r.get("label", ""))
        if r["ok"]:
            threading.Thread(target=lambda: mac.watch(self.mac_notify, 2), daemon=True).start()          # listen for the Mac's delivery reply
            if self.on_card:
                self.on_card({"type": "text", "title": "Sent to your Mac", "text": str(r.get("label") or "")[:120]})
        elif r["message"] == "not paired":
            self.on_msg("sys", "Your Mac is not paired. Say 'pair my Mac'.")
        else:
            self.on_msg("sys", "Could not send to your Mac: " + r["message"])
        return r

    def run_phone(self, payload):
        """Send a notification to the user's phone via ntfy. The topic is never printed or shown."""
        if payload.get("current"):                                       # "text me that": the page in the frame, the last map link, or the last answer
            link = opener.EMBED.get("page_url")
            payload = {"title": "From Jarvis", "message": (self.recent[-1][1] if self.recent else "")[:300], "link": link}
        if payload.get("file"):
            r = phone.send_file(payload["file"], payload.get("title", "From Jarvis"), payload.get("message", ""), payload.get("link"))
        else:
            r = phone.send(payload)
        print("[phone]", r["message"], flush=True)
        lbl = str(payload.get("title") or "note")[:40] + (": " + str(payload.get("message") or payload.get("link") or "")[:36] if (payload.get("message") or payload.get("link")) else "")
        if r["ok"]:
            outbox.log("phone", lbl, "sent", "alerts have no read receipt", payload=payload)
        else:
            rid = outbox.log("phone", lbl, "failed", r["message"], payload=payload)
            if r["message"].startswith("could not reach"):
                outbox.retry_later(lambda: ((lambda q: (q["ok"], q["message"]))(phone.send_file(payload["file"]) if payload.get("file") else phone.send(payload))), rid)
        extras.log_event("sent to your phone" if r["ok"] else "could not send to your phone", "" if r["ok"] else r["message"])
        if r["ok"]:
            v = phone.validate(payload) or {}
            if self.on_card:
                self.on_card({"type": "text", "title": "Sent to your phone", "text": (v.get("title") or "") + ((" - " + v["message"]) if v.get("message") else "")})
        else:
            self.on_msg("sys", "Could not send to your phone: " + r["message"])
        return r

    def run_volume(self, v):
        """Fixed wpctl commands only (the numbers are clamped); never text from the model."""
        sink = "@DEFAULT_AUDIO_SINK@"
        try:
            if "set" in v:
                subprocess.run(["wpctl", "set-volume", sink, f"{max(0, min(100, int(v['set']))) / 100:.2f}"], timeout=3)
            elif "delta" in v:
                subprocess.run(["wpctl", "set-volume", "-l", "1.0", sink, f"{abs(int(v['delta']))}%{'+' if v['delta'] > 0 else '-'}"], timeout=3)
            elif "mute" in v:
                subprocess.run(["wpctl", "set-mute", sink, "1" if v["mute"] else "0"], timeout=3)
        except Exception as e:
            print("[volume] failed:", type(e).__name__, flush=True)

    def _schedule(self, entry, delay=None):
        """One timer/reminder: a spoken alert plus a card when it is due; kept in data/reminders.json until then."""
        def fire():
            extras.log_event("timer went off")
            if entry.get("soft"):
                try:
                    audio.soft_tone()                          # focus-timer alerts are quiet: a soft tone and a card, then one short line
                except Exception:
                    pass
            extras.PENDING[:] = [p for p in extras.PENDING if p is not entry]
            extras.save_pending()
            if not entry.get("soft"):
                sounds.play("reminder")
            if self.on_card:
                self.on_card({"type": "text", "title": "Reminder" if entry.get("label") else "Timer", "text": entry.get("label") or "Time is up."})
            if not (entry.get("soft") and quiet.active()):        # a focus alert during quiet hours is the tone and the card only
                self.say(entry["text"])
            if phone.get_topic(create=False) and not entry.get("soft"):                              # a reminder or timer also reaches your phone (category on/off, priority and quiet hours: notifier.py)
                from . import notifier
                threading.Thread(target=notifier.send, args=("reminders" if entry.get("label") else "timers", "Reminder" if entry.get("label") else "Timer", entry.get("label") or "Your timer is done."), daemon=True).start()
        tm = threading.Timer(max(0.0, entry["due"] - time.time()) if delay is None else delay, fire)
        tm.daemon = True
        tm.start()
        self.timers = [x for x in self.timers if x.is_alive()] + [tm]

    def restore_timers(self):
        """After a restart: bring back what is still to go off, and say what went off while Jarvis was off (within the last 5 minutes)."""
        todo, missed = extras.load_pending()
        for entry in todo:
            entry.setdefault("text", f"Reminder: {entry['label']}." if entry.get("label") else "Your timer is done.")
            extras.PENDING.append(entry)
            self._schedule(entry)
        if missed:
            what = "; ".join((m.get("label") or "a timer") for m in missed)
            self.on_msg("sys", "Missed while I was off: " + what)
            threading.Timer(12, lambda: self.say("While I was off, this went off: " + what + ".")).start()
        if todo or missed:
            print(f"[timers] restored {len(todo)}, missed {len(missed)}", flush=True)

    def run_timer(self, t):
        """Timers and reminders: a spoken alert plus a card when due. Saved to data/reminders.json, so they survive a restart."""
        if t.get("cancel"):
            for tm in self.timers:
                tm.cancel()
            self.timers = []
            extras.PENDING.clear()
            extras.save_pending()
            return
        label = t.get("label") or ""
        text = t.get("text") or (f"Reminder: {label}." if label else "Your timer is done.")
        extras.log_event("set a timer", f"{int(t['seconds'])} seconds")
        entry = {"due": time.time() + int(t["seconds"]), "label": label, "text": text}
        extras.PENDING[:] = [p for p in extras.PENDING if p["due"] > time.time()] + [entry]
        extras.save_pending()
        self._schedule(entry)

    def run_open(self, action, T=None):
        """Open a validated website/app (or focus Jarvis again). Never touches the kiosk window itself."""
        t_start = time.time()
        try:
            r = opener.execute(action, timing=(lambda n: T.mark("open_" + n)) if T else None)
        except Exception as e:
            r = {"ok": False, "message": str(e)[:120]}
        since = f" | speech end -> window shown {T.marks['open_window']:.2f}s (dispatched {T.marks.get('open_dispatched', 0):.2f}s)" if T and "open_window" in T.marks else ""
        extras.log_event("opened" if r["ok"] else "could not open", action.get("label") or action.get("action"))
        print("[open]", action.get("action"), {k: v for k, v in r.items() if k != "address"}, f"took {time.time() - t_start:.2f}s" + since, flush=True)
        if action.get("action") in ("focus_jarvis", "close_last", "fullscreen_embed") and r["ok"]:
            return r
        self.on_msg("sys", ("Opened: " if r["ok"] else "Could not open: ") + str(action.get("label") or action.get("action")) + ("" if r["ok"] else f" ({r['message']})"))
        return r

    def say(self, text, wait=False):
        """Speak a line outside a normal turn (greetings, wake replies): shows 'speaking', always returns to idle."""
        self._timing = None                       # a stale turn timing must not hijack the state (see _on_play)
        self.turn += 1
        turn, gen = self.turn, self.speaker.begin()
        for sentence in tts.split_sentences(text):
            self.speaker.add(sentence, gen)
        self._set("speaking")

        def finish():
            while self.speaker.busy() and self.turn == turn:
                time.sleep(0.05)
            if self.turn == turn and self.state == "speaking":
                self._set("idle")
        if wait:
            finish()
        else:
            threading.Thread(target=finish, daemon=True).start()

    # --- hands-free (wake phrases) ----------------------------------------------
    def set_handsfree(self, on):
        """Wake phrases on/off (what the user wants). While muted nothing listens, whatever this says."""
        self.handsfree_want = bool(on)
        self._apply_handsfree(self.handsfree_want and not self.muted)

    def _apply_handsfree(self, run):
        from .wake import HandsFree
        if run and not self.hf:
            self.hf = HandsFree(self)
            self.hf.start()
        elif not run and self.hf:
            self.hf.stop()
            self.hf = None
        if self.on_listen:
            self.on_listen(bool(self.hf))

    def set_muted(self, on):
        """MUTE = deaf mode. Stops the wake listener and the always-on voice detection completely (no mic processing),
        and conversation mode must not open the mic. Hold-to-talk still works: that is a deliberate press.
        Voice can never call this; it is only reachable from the button, the key shortcut and jarvisctl."""
        on = bool(on)
        if on == self.muted:
            return
        self.muted = on
        settings.update(muted=on)
        if on:
            self.conversation_end()
            self._apply_handsfree(False)
        else:
            self._apply_handsfree(self.handsfree_want)
        self.on_muted(on)
        self.on_msg("sys", "Muted: not listening for wake phrases. Hold-to-talk still works." if on else "Unmuted: listening for wake phrases again." if self.handsfree_want else "Unmuted.")

    def _sound_failed(self):
        c = soundtest.check_failed_play()
        if c and self.on_card:
            self.on_card(c)

    def run_soundtest(self):
        """Chime + a spoken sentence, and a card that says which output was used. A failure is shown as 'No sound: <reason>', never silently."""
        d, code = soundtest.run_test(audio.chime)
        reason = d["reason"] or ("the player could not open the output device (exit code %s)" % code if code else "")
        voice = os.path.basename(str(getattr(tts, "_current", "") or "")) or self.persona
        if reason:
            card = soundtest.warn_card(reason)
        else:
            card = {"type": "list", "title": "Sound test", "items": ["Output: " + soundtest.describe(d), "Chime: sent to that output, no error", f"Voice: {voice} (spoken next, same output)",
                                                                       "Nothing here can hear the room: if you heard the chime, sound works."]}
        if self.on_card:
            self.on_card(card)
        gen = self.speaker.begin()
        self.speaker.add("Sound test. If you can hear this, the speakers are working.", gen)

    VOICES = {"spanish": "es_ES-davefx-medium", "french": "fr_FR-siwis-medium", "german": "de_DE-thorsten-medium",
              "italian": "it_IT-paola-medium", "portuguese": "pt_BR-faber-medium"}

    def _translate_voice(self):
        """Translate mode speaks in the target language when a voice for it is installed; otherwise (or when it ends) the persona's own voice."""
        path = None
        if self.translate_to:
            f = config.ROOT / "models" / (self.VOICES.get(self.translate_to.lower().strip(), "") + ".onnx")
            path = str(f) if f.is_file() else None
        try:
            tts.set_voice(path or personas.voice_path(self.persona))
        except Exception as e:
            print("[translate] voice failed:", type(e).__name__, flush=True)

    # --- personas: theme = voice + name + system prompt ------------------------
    def set_persona(self, name, announce=True):
        """Switch character. Starts a fresh Claude session (a resumed one stays in the old persona) and
        carries the last few exchanges over as quoted background so the thread of conversation survives."""
        if name not in personas.PERSONAS or name == self.persona:
            return
        if self.state not in ("idle", "speaking", "error", "loading"):
            self._pending = name                  # busy with a turn: switch as soon as it is done
            return
        p = personas.PERSONAS[name]
        self.turn += 1
        self.speaker.stop()
        self.persona = name
        sounds.set_persona(name)
        tts.set_voice(personas.voice_path(name))
        tts.set_speed(personas.voice_path(name), prefs.speed(name))      # personas can share a voice file but not a pace
        opener.NOTIFY({"t": "prefs", **prefs.public(name)})                      # each voice has its own remembered speed
        recap = "".join(f"User: {q}\nAssistant: {a}\n" for q, a in self.recent[-3:])
        self._carry = ("(Background only, from an earlier chat with a different assistant persona; "
                       f"you are {p['name']} now. Never mention this note, the other persona, or a change of persona.)\n{recap}---\n") if recap else ""
        self.brain.set_system(personas.system_prompt(name, self.cards))
        threading.Thread(target=self._preload_persona, args=(name,), daemon=True).start()
        if self.on_persona:
            self.on_persona(name)
        self.on_msg("sys", f"{p['name']} online." + (" Earlier conversation carried over." if recap else ""))
        if announce:
            self.say(p["greeting"])
        threading.Thread(target=self.brain.warm, daemon=True).start()

    # --- worker ---------------------------------------------------------
    def _alive(self, turn):
        return turn == self.turn

    def _pipeline(self, wav, shot, turn, T, ep):
        data = audio.read_wav(wav)
        os.unlink(wav)
        self._turn(data, shot, turn, T, ep)

    def submit_audio(self, data, shot=None, strip_wake=False, conv=False):
        """A finished utterance from somewhere other than the push-to-talk recorder (hands-free).
        strip_wake: it was captured right after a wake phrase and may start with the tail of it."""
        self.turn += 1
        T = Timing(time.time()); T.mark("ack")
        threading.Thread(target=self._turn, args=(data, shot, self.turn, T, None, None, strip_wake, conv), daemon=True).start()

    def ask_text(self, text, image=None, display=None, local=True):
        """The request is already text (e.g. 'Hey Jarvis, what's the time?'). image: a jpg for the brain; display: what the chat shows as 'you';
        local=False: do not run it through the local skill matcher (it is a prepared prompt, not something the user said)."""
        self.turn += 1
        T = Timing(time.time()); T.mark("ack"); T.mark("transcript")
        self._display, self._no_local = display, not local
        threading.Thread(target=self._turn, args=(None, image, self.turn, T, None, text), kwargs={"conv": bool(self.expect) and local}, daemon=True).start()

    def _turn(self, data, shot, turn, T, ep=None, text=None, strip_wake=False, conv=False):
        try:
            if text is None:
                self._set("transcribing")
                text = ep.finish(data) if ep else None          # speculative result, if still valid
                if text is None:
                    text = stt.transcribe(endpoint.trim(data), prompt=COMMAND_PROMPT)
                if strip_wake and text:
                    from . import wake
                    text = wake.strip_wake(text, personas.PERSONAS[self.persona]["name"])
            T.mark("transcript")
            if text:
                opener.NOTIFY({"t": "heard", "text": text[:140], "route": ""})                 # the small "heard: ..." line, so a wrong guess is easy to spot
            hint = False
            if conv:
                if not text:                                            # nothing recognised: close quietly
                    self.conversation_end(); self._set("idle"); return
                if convo.is_signoff(text) and not self.translate_to:    # "no thanks", "that's all", "thanks"... (in translate mode they are translated like anything else)
                    self._signoff(text, turn); return
                hint = convo.maybe_signoff(text)                        # unsure: let the model decide, once
                exp, self.expect = self.expect, None
                if exp and exp["kind"] in ("setloc", "settemp"):
                    self._answer_local(text, maps.apply_location(text, exp["kind"] == "settemp"), turn, T)
                    return
                if exp and exp["kind"] == "mkt_pick":
                    r2 = markets.validate_route({"intent": exp["text"] or "pull_up", "name": text})
                    self._answer_local(text, mktdesk.answer(r2, text) if r2 else {"speech": "I didn't catch which one."}, turn, T)
                    return
                if exp and exp["kind"] == "model_ok":
                    d = json.loads(exp["text"] or "{}")
                    if re.match(r"\s*(?:yes|yeah|yep|sure|ok|okay|please|download|go ahead)\b", text, re.I) and d.get("m"):
                        settings.update(models_ok=sorted(set((settings.get("models_ok") or []) + [d["m"].get("source", "commons")])))
                        self._answer_local(text, {"speech": "Downloading it."}, turn, T)
                        threading.Thread(target=self.holo_real, args=(d["m"], d.get("subject", "")), daemon=True).start()
                    else:
                        self._answer_local(text, {"speech": "Okay, building it from basic shapes."}, turn, T)
                        threading.Thread(target=self.holo_build, args=(d.get("subject", ""), True), daemon=True).start()
                    return
                if exp and exp["kind"] == "holo_name":
                    nm = re.sub(r"[^A-Za-z0-9 ]", "", text).strip()[:40]
                    if nm:
                        self.holo_save(nm)
                    self._answer_local(text, {"speech": f"Saved as {nm}." if nm else "I didn't catch a name."}, turn, T)
                    return
                if exp and exp["kind"] == "howto_goal":
                    self._answer_local(text, {"speech": "Okay. Let me look at your screen."}, turn, T)
                    self.howto_start(text)
                    return
                if exp and exp["kind"] == "circle_q":
                    if self.circle:
                        self.circle["q"] = text
                    self._set("idle")
                    self._circle_try()
                    return
                if exp and exp["kind"] == "promise_done":
                    cmd = buddy.parse_command(text)
                    if cmd and cmd["kind"] in ("done", "snooze"):
                        self._answer_local(text, buddy.run_command(cmd), turn, T)
                        return
                if exp and exp["kind"] in ("promise_save", "promise_auto"):
                    yes = re.match(r"\s*(?:yes|yeah|yep|sure|ok|okay|please|do it|save it|keep it|go ahead|always)\b", text, re.I)
                    if exp["kind"] == "promise_save":
                        pr = json.loads(exp["text"] or "{}")
                        if yes and pr.get("label"):
                            buddy.add_promise(pr["label"], pr.get("due"))
                            if settings.get("promises_asked_auto"):
                                self._answer_local(text, {"speech": "Saved."}, turn, T)
                            else:
                                settings.update(promises_asked_auto=True)
                                self._answer_local(text, {"speech": "Saved. Should I save these automatically from now on?", "expect": "promise_auto"}, turn, T)
                        else:
                            self._answer_local(text, {"speech": "Okay, I won't keep that one."}, turn, T)
                    else:
                        if yes:
                            settings.update(auto_promises=True)
                            opener.NOTIFY({"t": "prefs", **prefs.public(self.persona)})
                        self._answer_local(text, {"speech": "Okay, I'll save them automatically. Say stop saving promises to turn that off." if yes else "Okay, I'll keep asking first."}, turn, T)
                    return
                if exp and exp["kind"] == "confirmloc":
                    if re.match(r"\s*(?:yes|yeah|yep|correct|that'?s right|right|ok|okay|sure)\b", text, re.I):
                        g = maps.confirm_pending()
                        self._answer_local(text, {"speech": f"Saved. Home is {g['name']}."} if g else {"speech": "I lost that one. Say: set my location."}, turn, T)
                    else:
                        self._answer_local(text, maps.apply_location(text, False), turn, T)
                    return
                if exp and exp["kind"] == "confirmip":
                    if re.match(r"\s*(?:yes|yeah|yep|correct|that'?s right|right|ok|okay|sure)\b", text, re.I):
                        maps.confirm_ip()
                        again = skills.handle(exp["text"], self.persona) if exp["text"] else None
                        self._answer_local(text, again or {"speech": "Good, I'll use that for today."}, turn, T)
                    else:
                        r0 = maps.apply_location(text, True)
                        again = skills.handle(exp["text"], self.persona) if exp["text"] and r0["speech"].startswith("Got it") else None
                        self._answer_local(text, {"speech": (r0["speech"] + " " + (again or {"speech": ""})["speech"]).strip(), "cards": (again or {}).get("cards", []), "open": (again or {}).get("open")}, turn, T)
                    return
                if exp and exp["kind"] in ("map", "directions"):         # we asked "which place?": this is the answer
                    res = intents.run({"name": "show_map" if exp["kind"] == "map" else "directions", "query": text, "to": text})
                    self._answer_local(text, res or {"speech": "I couldn't find that."}, turn, T)
                    return
                if exp and exp["kind"] == "home":                       # we asked "which town?": this is the answer
                    r = skills.set_home("set my home to " + text)
                    if r and r["speech"].startswith("Home area set"):
                        local = skills.handle(exp["text"], self.persona) or {"speech": ""}
                        self._answer_local(text, {"speech": (r["speech"] + " " + local["speech"]).strip(), "cards": local.get("cards", [])}, turn, T)
                    else:
                        self._answer_local(text, {"speech": ((r or {"speech": ""})["speech"] + " Which town are you in?").strip(), "expect": "home", "retry": exp["text"]}, turn, T)
                    return
            if not text and not shot:
                self.on_msg("sys", "Didn't catch that.")
                self._set("idle")
                return
            skip_local, self._no_local = getattr(self, "_no_local", False), False
            if text and improve.is_wrong(text) and self.recent:                          # "that's wrong": remember what went wrong, for "improve yourself"
                improve.log("wrong", self.recent[-1][0], "answer was: " + self.recent[-1][1][:120])
                self._answer_local(text, {"speech": "Noted. I'll learn from it when you say improve yourself."}, turn, T)
                return
            if text and not shot and not self.translate_to and self.holo3:         # a 3D model is open: part commands, the tour, and questions about the selected part
                h3 = self.holo3
                tour = h3.get("tour")
                low3 = re.sub(r"[^a-z' ]", "", text.lower()).strip()
                if tour and re.fullmatch(r"(?:please )?(?:stop|cancel|enough|that'?s enough|never ?mind|stop the tour)", low3):
                    tour["stop"] = True; self.speaker.stop(); self.on_msg("you", text); self._set("idle"); return
                if tour and re.fullmatch(r"(?:please )?(?:next|skip|go on)", low3):
                    tour["jump"] = 1; self.speaker.stop(); self.on_msg("you", text); self._set("idle"); return
                if tour and re.fullmatch(r"(?:please )?(?:go back|back|previous|again)", low3):
                    tour["jump"] = -1; self.speaker.stop(); self.on_msg("you", text); self._set("idle"); return
                cmd3 = holo.parse_control(text, h3["spec"]["parts"])
                if cmd3:
                    if cmd3["op"] == "close":
                        opener.NOTIFY({"t": "holo", "op": "close_card"})
                    opener.NOTIFY({"t": "holo", "op": cmd3["op"], "id": cmd3.get("id")})
                    nm = next((p["label"] for p in h3["spec"]["parts"] if p["id"] == cmd3.get("id")), "")
                    say = {"select": f"{nm}.", "hide": f"Hiding the {nm.lower()}.", "isolate": f"Just the {nm.lower()}.", "explode": "Pulling it apart.", "implode": "Back together.", "close": "Closed."}.get(cmd3["op"], "")
                    self._answer_local(text, {"speech": say}, turn, T) if say else (self.on_msg("you", text), self._set("idle"))
                    return
                if h3.get("sel") and holo.parse_follow(text):
                    part = next((p for p in h3["spec"]["parts"] if p["id"] == h3["sel"]), None)
                    if part:
                        self.on_msg("you", text)
                        self._set("thinking")
                        reply = self.brain.ask(holo.part_prompt(h3["spec"], part, text), timeout=60)
                        mb = re.search(r"\{.*\}", reply, re.S)
                        ans = moods.strip(reply[: mb.start()] if mb else reply).strip() or "I'm not sure."
                        try:
                            bl = holo.bullets(json.loads(mb.group(0)).get("bullets")) if mb else []
                        except ValueError:
                            bl = []
                        if bl:
                            opener.NOTIFY({"t": "holo_bullets", "id": part["id"], "list": bl})
                        self._answer_local(text, {"speech": ans[:400]}, turn, T)
                        return
            if text and not shot and not self.translate_to:                      # screen helpers: "show me how ... in this app", "circle to ask", and their next / stop
                if self.howto and self.howto.get("active"):
                    ctl = screenhelp.parse_control(text)
                    if ctl:
                        self.on_msg("you", text)
                        self.howto_next() if ctl == "next" else self.howto_stop("Okay, stopping.")
                        self._set("idle")
                        return
                if self.circle and self.circle.get("box") and not self.circle.get("q") and time.time() - self.circle["t"] < 25 and len(text.split()) >= 2:
                    self.on_msg("you", text)                                   # the spoken question for the region you just circled
                    self.circle["q"] = text
                    self._set("idle")
                    self._circle_try()
                    return
                lowc = re.sub(r"[^a-z' ]", "", text.lower()).strip()
                if re.fullmatch(r"(?:please )?(?:turn on|switch on|start|open|show|activate|enable|put on)(?: up)?(?: the| my)?(?: phone)? (?:camera|cam)(?: please)?", lowc):
                    self._answer_local(text, {"speech": "Camera on."}, turn, T)
                    threading.Thread(target=self.camera_on, daemon=True).start()
                    return
                if re.fullmatch(r"(?:please )?(?:turn off|switch off|stop|close|shut|disable|hide|turn)(?: down)?(?: the| my)?(?: phone)? (?:camera|cam)(?: off)?(?: please)?", lowc):
                    self._answer_local(text, {"speech": "Camera off."}, turn, T)
                    self.look_close()
                    return
                goal = screenhelp.parse_howto(text)
                if goal is not None:
                    if goal:
                        self._answer_local(text, {"speech": "Okay. Let me look at your screen."}, turn, T)
                        self.howto_start(goal)
                    else:
                        self._answer_local(text, {"speech": "What do you want to do?", "expect": "howto_goal"}, turn, T)
                    return
                if screenhelp.parse_circle(text):
                    self._answer_local(text, {"speech": "Draw around it with your finger or the mouse, and tell me what you want to know."}, turn, T)
                    self.circle_start()
                    return
            lm = lookmem.parse_command(text) if text and not shot and not self.translate_to else None
            if lm and lm["op"] in ("find", "read", "watch", "unwatch"):
                self._look_memory_command(lm, text, turn, T)
                return
            lookq = None
            if lm and lm["op"] == "remember":
                self._remember_label = lm["label"] or "something"
                text = "what do you see"                                        # a fresh look; the result is saved below
            if text and not shot and not self.translate_to:
                cmd = look.parse_command(text, self.look)
                if cmd and cmd["op"] != "box":                                    # clear / freeze / live / close / select: no brain needed (select can speak)
                    self._look_command(cmd, text, turn, T)
                    return
                fresh = look.is_fresh(text) or (cmd and cmd["op"] == "box")
                self._look_text = text
                if fresh or self.look["open"]:                                    # "look at this", "what do you see", "box the cup", or any question while the LOOK view is open
                    focus = (cmd or {}).get("what") if cmd else None
                    try:
                        shot = self._look_frame(retake=bool(fresh and not (self.look["open"] and not look.FRESH.search(text) and not cmd)))
                    except Exception as e:
                        self._answer_local(text, {"speech": "I couldn't get a picture: " + str(e)[:80]}, turn, T)
                        return
                    lookq = {"fresh": bool(fresh), "focus": focus}
                    skip_local = True
            if text and not shot and not skip_local:
                from . import daylog
                daylog.add(text)
                from . import autopersona
                ap = autopersona.pick(text, self.persona)
                if ap and not (self.translate_to or self._pending):
                    self._pending = ap                                           # a topic that belongs to another persona: switch after this reply (it introduces itself unless "say it's switching" is off)
                    self._pending_quiet = settings.get("say_switching") is False
                local = skills.translate_command(text) if self.translate_to else skills.handle(text, self.persona)   # translate mode: only 'stop translating' is a command, everything else is translated
                if local:
                    self._answer_local(text, local, turn, T)
                    return
                pr = None if self.translate_to else buddy.detect_promise(text)
                if pr:                                                          # "I'll call mum tomorrow": keep it as a promise (asks the first time)
                    if settings.get("auto_promises"):
                        buddy.add_promise(pr["label"], pr["due"])
                        self._answer_local(text, {"speech": f"Noted: {pr['label'].lower()}, {buddy.when_words(pr['due'])}."}, turn, T)
                    else:
                        self._answer_local(text, {"speech": f"Want me to keep track of that? {pr['label']}, {buddy.when_words(pr['due'])}.", "expect": "promise_save", "retry": json.dumps(pr)}, turn, T)
                    return
            if text and not shot and not net.online():                    # offline: known within ~1.2 s; say so ONCE, answer small talk, and never show an error
                self.offline_changed(True, "net", speak=False)
                self._answer_local(text, offline.handle(text, personas.PERSONAS[self.persona]["name"]), turn, T)
                return
            question = text or "What is on my screen right now? Describe it briefly."
            if lookq:
                sel = self.look["sel"]
                selt = (f"The user selected box {sel + 1}: {self.look['boxes'][sel]['name']} at x={self.look['boxes'][sel]['x']}, y={self.look['boxes'][sel]['y']}, w={self.look['boxes'][sel]['w']}, h={self.look['boxes'][sel]['h']}. " if sel is not None and sel < len(self.look["boxes"]) else "")
                question = (look.PROMPT + (look.FOCUS.format(what=lookq["focus"]) if lookq["focus"] else "") if lookq["fresh"] and not self.look["open"] or lookq["focus"] else look.PROMPT_FOLLOW.format(sel=selt)) + question
            asked = question
            if self._carry:
                question, self._carry = self._carry + question, ""
            question = skills.context_line() + memory.context(asked) + (continuity.context() if continuity.is_resume(asked) else "") + question   # date/time + home area, plus the few remembered facts that match this question
            if self.translate_to and text:                                       # translate mode: just the translation, nothing else
                question = f"Translate the following into {self.translate_to}. Reply with ONLY the translation: no quotes, no notes, no English.\n{text}"
            if hint:
                question = "(The user may just be ending the conversation. If they are, and ask nothing, reply with exactly: END)\n" + question
            disp, self._display = getattr(self, "_display", None), None
            self.on_msg("you", disp or ((text or "(look)") + ("  [screenshot]" if shot else "")))
            self._set("thinking")
            self._timing, self._played_gen = T, None
            gen = self.speaker.begin()
            if lookq and getattr(self, "last_source", ""):                   # tell me which one it used: out loud (and the LOOK view shows it)
                self.speaker.add({"phone camera": "Looking through the phone camera.", "USB camera": "Looking through the USB camera.", "screenshot of the desktop": "Looking at your desktop."}.get(self.last_source, ""), gen)
            sents = Sentences()

            st = {"acked": False, "lines": [], "tools": False, "cleared": False}

            def status(lines, active=True):
                st["lines"] = lines
                self.on_status(lines, active)

            def ack():
                """Slow task: say something right away and show a live 'searching' card."""
                if st["acked"] or not self._alive(turn):
                    return
                st["acked"] = True
                import random
                line = random.choice(personas.PERSONAS[self.persona]["ack"])
                T.mark("first_sentence")
                self.speaker.add(line, gen)
                self.on_msg("sys", line)

            if text and SLOW_INTENT.search(text):                       # looks like a search: acknowledge immediately
                ack()
                status(["Looking into it…"])

            def on_event(kind, detail):
                bw.got_token()                                       # tools running = the brain is working, not away
                if not self._alive(turn):
                    return
                if kind == "tool":
                    st["tools"] = True
                    ack()
                    name, _, arg = detail.partition("|")
                    if name == "WebFetch":
                        from urllib.parse import urlparse
                        label = "Reading " + (urlparse(arg).netloc or arg)[:48]
                    else:
                        label = "Searching: " + arg[:70]
                    status([l for l in st["lines"] if l != "Looking into it…"][-3:] + [label])
                elif kind == "tool_done":
                    status([(l if l.endswith(" ✓") or not l.startswith(("Searching", "Reading")) else l + " ✓") for l in st["lines"]])
                elif kind == "cap":
                    status(st["lines"] + ["Out of time, answering with what I found"])

            mstream = moods.Stream()

            def on_delta(d):
                T.mark("first_token")
                bw.got_token()
                if not self._alive(turn):
                    return
                if st["tools"] and not st["cleared"]:
                    st["cleared"] = True
                    self.on_status([], False)
                if hint and not st.get("released"):                      # might be the bare END marker: hold it back until we know
                    st["acc"] = st.get("acc", "") + d
                    a = st["acc"].strip().rstrip(".").upper()
                    if len(a) <= 3 and "END".startswith(a):
                        return
                    st["released"], d = True, st["acc"]
                for s in sents.feed(mstream.feed(d)):
                    T.mark("first_sentence")
                    self.speaker.add(s, gen)

            bw = offline.BrainWatch(lambda: self._alive(turn) and self.offline_changed(True, "brain", speak=True))
            bw.start()
            try:
                reply = self.brain.ask(question, image=shot, on_delta=on_delta, on_event=on_event)
            finally:
                bw.cancel()
                self.on_status([], False)
            if offline.STATE["off"] and offline.STATE["why"] == "brain":
                self.offline_changed(False)                              # it answered: the brain is back
            if hint and reply.strip().rstrip(".").upper() == "END":
                self._signoff(text, turn); return
            if not self._alive(turn):
                return
            if os.environ.get("JARVIS_DEBUG_MOODS"):
                print("[mood] raw reply starts:", repr(reply[:120]), flush=True)
            for s in sents.feed(mstream.flush()) + sents.flush():
                T.mark("first_sentence")
                self.speaker.add(s, gen)
            shown, cards, open_act, phone_act, mac_act = split_reply_full(reply)
            shown = moods.strip(shown)
            if lookq is not None:
                rawb = look.extract(reply)
                boxes = look.validate_boxes(rawb)
                print(f"[look] brain returned {len(rawb) if rawb is not None else 'no'} boxes, {len(boxes)} valid", flush=True)
                self._look_show(boxes, shot)
                if getattr(self, "_remember_label", None) and shot:
                    ent = lookmem.remember(shot, self._remember_label, [b["name"] for b in boxes], shown)
                    self._remember_label = None
                    self.on_msg("sys", "Remembered this picture as: " + ent["label"])
            intent = intents.validate(split_intent(reply), text or "")
            if intent and intent["name"] != "answer":                   # the code, not the model, decides what runs: only an allowlisted intent, with the user's own words checked for sends
                res = intents.run(intent)
                print("[intent]", intent["name"], "->", "ok" if res else "nothing", flush=True)
                if res:
                    if res.get("speech"):
                        self.speaker.add(res["speech"], gen)
                        shown = (shown + " " + res["speech"]).strip()
                    self._apply_effects(res, T)
                    if res.get("expect"):
                        self.expect = {"kind": res["expect"], "text": text or ""}
                    open_act = None if intent["name"] in ("show_map", "directions", "show_images", "show_video") else open_act     # never also run a stray search
                    phone_act = mac_act = None if intent["name"] not in ("send_to_mac", "send_to_phone") else None
            elif split_intent(reply) is not None and not intent:
                print("[intent] rejected:", str(split_intent(reply))[:100], flush=True)
                improve.log("intent-rejected", text or "", str(split_intent(reply))[:120])
            if mac_act is not None:                                     # only when the user's own words mention their Mac
                if mac.SEND_INTENT.search(text or ""):
                    threading.Thread(target=self.run_mac, args=(mac_act,), daemon=True).start()
                else:
                    print("[mac] ignored: the user did not ask for the Mac", flush=True)
            if phone_act is not None:                                   # only when the user's own words asked for a send
                if phone.SEND_INTENT.search(text or ""):
                    threading.Thread(target=self.run_phone, args=(phone_act,), daemon=True).start()
                else:
                    print("[phone] ignored: the user did not ask for a send", flush=True)
            if open_act is not None:                                    # the brain asked to open something: validated against the allowlist,
                act = opener.validate(open_act, text)                   # and only if the USER asked (their own words contain an open intent)
                if act:
                    threading.Thread(target=self.run_open, args=(act, T), daemon=True).start()
                else:
                    print("[open] brain action rejected:", str(open_act)[:120], flush=True)
            self.recent = (self.recent + [(asked[:300], (shown or "")[:300])])[-6:]
            continuity.add(text or "", shown or "")
            opener.NOTIFY({"t": "heard", "text": (text or "")[:140], "route": ("brain: " + intent["name"]) if intent else "brain"})
            self.on_msg("jarvis", shown or "(on screen)")
            if self.on_card:
                for c in cards:
                    T.mark("first_visual")
                    self.on_card(c)
            while self.speaker.busy() and self._alive(turn):
                time.sleep(0.05)
            T.mark("done")
            extras.HISTORY.append(dict(T.marks)); del extras.HISTORY[:-20]
            line = T.line()
            print("TIMING", line, flush=True)
            self.on_msg("sys", "⏱ " + line)
            if self.on_timing:
                self.on_timing(T.marks)
            opened = self._after_reply(shown, turn, gen)
            if self._alive(turn) and not opened:
                self._set("idle")
        except Exception as e:
            if isinstance(e, RuntimeError) and re.search(r"brain|claude|no output|process exited|took too long|stopped responding", str(e), re.I) or not net.online():
                print("[brain] unavailable:", str(e)[:120], flush=True)
                why = "net" if not net.online() else "brain"
                self.offline_changed(True, why, speak=False)             # not an error screen: say it once, keep the local skills
                try:
                    self._answer_local(text, offline.handle(text or "", personas.PERSONAS[self.persona]["name"]), turn, T)
                    return
                except Exception:
                    pass
            else:
                self.on_msg("sys", f"Error: {e}")
            self._set("idle")
