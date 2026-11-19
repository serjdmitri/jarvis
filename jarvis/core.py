"""The assistant pipeline: hold-to-talk -> whisper -> claude (streamed) -> piper (per sentence).
UI-agnostic; reports through callbacks (may be called from any thread)."""
import json
import math
import os
import random
import re
import subprocess
import threading
import time
import wave

import numpy as np

from . import audio, camera, look, phonecam, maps, intents, prefs, soundtest, spotify, memory, music, net, quiet, readscreen, scenes, dictate, extras, research, video, config, convo, endpoint, mac, opener, phone, personas, settings, skills, stt, tts
from .brain import make_brain

MIN_RECORD_S = 0.4
COMMAND_PROMPT = "Jarvis. Rose. Hide the interface. Show the interface. Open YouTube. Back to Jarvis."   # biases spelling of the command words
SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
CLAUSE_END = re.compile(r"(?<=[,;:])\s+")
HOLD_RE = re.compile(r"```|\{")                    # start of a (possible) JSON card block
SHOW_RE = re.compile(r'(?:```(?:json)?\s*)?\{\s*"(?:show|open|phone|mac|intent|look)"')
KIOSK_CLASSES = ("jarvis-ui", "local.jarvis", "chrome-127.0.0.1__dock-Default")       # our own windows: never what "look" should see
LAST_NOTE = ""                                      # set by screenshot() when it could not step away from our own window
CARD_TYPES = {"text", "list", "map", "chart", "image"}
SLOW_INTENT = re.compile(r"\b(near me|nearby|near here|around here|best|find|search|look up|latest|news|tonight|open(?:ing)? hours|opening|price|cost|recommend|events?|restaurants?|cafes?|things to do|where can|directions|route|reviews?|compare|research)\b", re.I)


# --- cards ----------------------------------------------------------------
def _s(v, n):
    return str(v)[:n] if v is not None else ""


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
        self.recent, self._carry, self._pending = [], "", None     # last exchanges (for persona switches)
        tts.set_voice(personas.voice_path(self.persona))
        self.rec, self.brain = audio.Recorder(), make_brain(personas.system_prompt(self.persona, cards))
        for n in personas.PERSONAS:                                                    # a remembered speaking speed for each voice
            tts.set_speed(personas.voice_path(n), prefs.speed(n))
        self.speaker = tts.Speaker(on_play=self._on_play)
        self.speaker.on_fail = self._sound_failed
        self.state = "loading"
        self.shot = None
        self.last_source = ""
        self.look = {"open": False, "path": None, "boxes": [], "sel": None, "source": "", "live": False}
        self.source = "auto"         # what "look" captures: auto (USB webcam, else screen) | screen | window | camera
        self.t0 = 0.0
        self.turn = 0
        self.ep = None
        self._timing = None
        self._played_gen = None
        self.brain_ready = threading.Event()

    def _set(self, state, detail=""):
        self.state = state
        self.on_state(state, detail)
        if music.STATE["active"]:                                    # music is lowered while he listens, thinks or speaks, and comes back afterwards
            quiet = state in ("recording", "transcribing", "thinking", "speaking")
            if quiet != music.STATE.get("ducked", False):
                music.STATE["ducked"] = quiet
                opener.NOTIFY({"t": "embed", "kind": "win", "cmd": "duck" if quiet else "unduck", "type": "video"})
        if state == "idle" and self._pending:
            name, self._pending = self._pending, None
            threading.Thread(target=self.set_persona, args=(name,), daemon=True).start()

    def _on_play(self, gen, path):
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

    def _preload_other_voices(self):
        for n in personas.PERSONAS:
            try:
                tts._get(personas.voice_path(n))
            except Exception as e:
                print("voice preload failed:", n, e)
        for n, p in personas.PERSONAS.items():             # short lines play instantly (no synthesis) from then on
            tts.prewarm(p["ack"] + p["awake"] + [p["greeting"], p["welcome_home"]], personas.voice_path(n))
        fast = opener.ack_phrases() + ["Here it is.", "Hiding the interface. Tap the orb to bring it back.", "Louder.", "Quieter.", "Sound muted.", "Sound on.",
                                       "Timers cancelled.", "Your timer is done.",
                                       *[f"Timer set for {n} minute{'s' if n != 1 else ''}." for n in (1, 2, 3, 5, 10, 15, 20, 30, 45)], "Timer set for 1 hour.",
                                       *[f"Volume {v} percent." for v in range(10, 101, 10)], "Muted. Press Super Alt M, or tap the mute icon, to wake me."]
        for n in sorted(personas.PERSONAS, key=lambda x: x != self.persona):   # the fast path's acks, current voice first (cached on disk: only the first start is slow)
            tts.prewarm(fast, personas.voice_path(n))

    # --- push to talk -------------------------------------------------
    def grab(self):
        """The picture for 'look', from the source you chose: auto = the phone camera if saved and reachable, else a USB webcam, else the screen; phone / camera / screen / window force one.
        Sets self.last_source to what was really used ("phone camera", "USB webcam", "screenshot") so it can be said and shown."""
        def phone():
            if not phonecam.get_url():
                return None
            try:
                return phonecam.grab(timeout=3)
            except RuntimeError as e:
                print("[look] phone camera not reachable:", str(e)[:60], flush=True)
                return None
        def usb():
            if not camera.usb_webcams():
                return None
            try:
                return camera.capture()
            except RuntimeError:
                return None
        order = {"phone": ("phone", "usb", "screen"), "camera": ("usb", "screen"), "auto": ("phone", "usb", "screen")}.get(self.source)
        for kind in order or (self.source,):
            if kind == "phone" and (p := phone()):
                self.last_source = "phone camera"; return p
            if kind == "usb" and (p := usb()):
                self.last_source = "USB webcam"; return p
            if kind in ("screen", "window"):
                if self.source in ("phone", "camera"):
                    self.show_no_camera("The phone camera is not reachable." if self.source == "phone" else camera.NO_WEBCAM)
                self.last_source = "screenshot"
                return screenshot("window" if self.source == "window" else "screen")
        self.last_source = "screenshot"
        return screenshot("screen")

    def show_no_camera(self, text=camera.NO_WEBCAM):
        self.on_msg("sys", text + " Using a screenshot instead.")
        if self.on_card:
            self.on_card({"type": "text", "title": "No camera", "text": text})

    def camera_preview(self):
        """Camera icon / source option: one fresh frame as a card (or the no-webcam card). Never raises."""
        try:
            path = camera.capture()
        except RuntimeError as e:
            if self.on_card:
                self.on_card({"type": "text", "title": "No camera", "text": str(e) if str(e) == camera.NO_WEBCAM else str(e)})
            return False
        if self.on_card:
            self.on_card({"type": "screenshot", "title": "Camera", "file": path})
        return True

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
                self.shot = self.grab()
                if LAST_NOTE:
                    self.on_msg("sys", LAST_NOTE)
                if self.on_card:
                    self.on_card({"type": "screenshot", "title": "What I saw", "file": self.shot})
            except Exception as e:
                self._set("error", f"screenshot failed: {e}")
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
            audio.ack()
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
        if local.get("idle"):
            opener.NOTIFY({"t": "idle", "on": True})
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
        opened = self._after_reply(local["speech"], turn, gen, allow_followup=local.get("ui") is None and not local.get("open") and not local.get("mute"))
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

    # --- LOOK: the full-screen view with labelled boxes -------------------------------------------
    def _look_frame(self, retake=True):
        """A fresh picture (the chosen source, sharp frame) or, while the LOOK view is frozen on one, that same picture again."""
        if not retake and self.look["open"] and self.look["path"] and os.path.exists(self.look["path"]):
            return self.look["path"]
        path = self.grab()
        self.on_msg("sys", f"Looking through the {self.last_source}.")
        self.look.update(path=path, source=self.last_source, live=False, boxes=[], sel=None)
        opener.NOTIFY({"t": "look", "op": "open", "src": "/shot/" + os.path.basename(path), "source": self.last_source, "boxes": []})
        self.look["open"] = True
        return path

    def _look_show(self, boxes, path):
        self.look["boxes"], self.look["sel"] = boxes, None
        opener.NOTIFY({"t": "look", "op": "boxes", "boxes": boxes})

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
            opener.NOTIFY({"t": "look", "op": "live", "stream": phonecam.get_url() if self.look["source"] == "phone camera" else None}); say = "Live."
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
        opener.NOTIFY({"t": "look", "op": "select", "i": i, "text": ""})
        def go():
            try:
                q = (f"[LOOK follow-up: the picture is attached. In ONE short sentence (max 18 words), what is the {b['name'].lower()} in the box at x={b['x']}, y={b['y']}, w={b['w']}, h={b['h']}? "
                     "No JSON, no preamble.]")
                r = self.brain.ask(q, image=self.look["path"]) if self.look["path"] else ""
            except Exception as e:
                r = ""
                print("[look] describe failed:", type(e).__name__, flush=True)
            r = re.sub(r"\s+", " ", (r or "").strip())[:200]
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
        if state == "dropped" and self.on_card:                          # never fail silently: say why the Mac refused it
            self.on_card({"type": "text", "title": "Mac did not open it", "text": text})

    def mac_watch(self):
        mac.STATE["connected"] = 0.0
        mac.watch(self.mac_notify)

    def run_mac(self, payload):
        """Send a link or file to the Mac (signed). Never prints the code, topic or key."""
        r = mac.send(payload)
        print("[mac]", r["message"], flush=True)
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
        r = phone.send(payload)
        print("[phone]", r["message"], flush=True)
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
            if self.on_card:
                self.on_card({"type": "text", "title": "Reminder" if entry.get("label") else "Timer", "text": entry.get("label") or "Time is up."})
            if not (entry.get("soft") and quiet.active()):        # a focus alert during quiet hours is the tone and the card only
                self.say(entry["text"])
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
        tts.set_voice(personas.voice_path(name))
        opener.NOTIFY({"t": "prefs", **prefs.public(name)})                      # each voice has its own remembered speed
        recap = "".join(f"User: {q}\nAssistant: {a}\n" for q, a in self.recent[-3:])
        self._carry = ("(Background only, from an earlier chat with a different assistant persona; "
                       f"you are {p['name']} now. Never mention this note, the other persona, or a change of persona.)\n{recap}---\n") if recap else ""
        self.brain.set_system(personas.system_prompt(name, self.cards))
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
        threading.Thread(target=self._turn, args=(None, image, self.turn, T, None, text), daemon=True).start()

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
            lookq = None
            if text and not shot and not self.translate_to:
                cmd = look.parse_command(text, self.look)
                if cmd and cmd["op"] != "box":                                    # clear / freeze / live / close / select: no brain needed (select can speak)
                    self._look_command(cmd, text, turn, T)
                    return
                fresh = look.is_fresh(text) or (cmd and cmd["op"] == "box")
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
                local = skills.translate_command(text) if self.translate_to else skills.handle(text, self.persona)   # translate mode: only 'stop translating' is a command, everything else is translated
                if local:
                    self._answer_local(text, local, turn, T)
                    return
            if text and not shot and not net.online():                    # offline: say so at once instead of waiting a minute for the brain
                self._answer_local(text, {"speech": "I can't reach the internet right now. I can still do timers, volume, the time and opening things."}, turn, T)
                return
            question = text or "What is on my screen right now? Describe it briefly."
            if lookq:
                sel = self.look["sel"]
                selt = (f"The user selected box {sel + 1}: {self.look['boxes'][sel]['name']} at x={self.look['boxes'][sel]['x']}, y={self.look['boxes'][sel]['y']}, w={self.look['boxes'][sel]['w']}, h={self.look['boxes'][sel]['h']}. " if sel is not None and sel < len(self.look["boxes"]) else "")
                question = (look.PROMPT + (look.FOCUS.format(what=lookq["focus"]) if lookq["focus"] else "") if lookq["fresh"] and not self.look["open"] or lookq["focus"] else look.PROMPT_FOLLOW.format(sel=selt)) + question
            asked = question
            if self._carry:
                question, self._carry = self._carry + question, ""
            question = skills.context_line() + memory.context(asked) + question   # date/time + home area, plus the few remembered facts that match this question
            if self.translate_to and text:                                       # translate mode: just the translation, nothing else
                question = f"Translate the following into {self.translate_to}. Reply with ONLY the translation: no quotes, no notes, no English.\n{text}"
            if hint:
                question = "(The user may just be ending the conversation. If they are, and ask nothing, reply with exactly: END)\n" + question
            disp, self._display = getattr(self, "_display", None), None
            self.on_msg("you", disp or ((text or "(look)") + ("  [screenshot]" if shot else "")))
            self._set("thinking")
            self._timing, self._played_gen = T, None
            gen = self.speaker.begin()
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

            def on_delta(d):
                T.mark("first_token")
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
                for s in sents.feed(d):
                    T.mark("first_sentence")
                    self.speaker.add(s, gen)

            try:
                reply = self.brain.ask(question, image=shot, on_delta=on_delta, on_event=on_event)
            finally:
                self.on_status([], False)
            if hint and reply.strip().rstrip(".").upper() == "END":
                self._signoff(text, turn); return
            if not self._alive(turn):
                return
            for s in sents.flush():
                T.mark("first_sentence")
                self.speaker.add(s, gen)
            shown, cards, open_act, phone_act, mac_act = split_reply_full(reply)
            if lookq is not None:
                rawb = look.extract(reply)
                boxes = look.validate_boxes(rawb)
                print(f"[look] brain returned {len(rawb) if rawb is not None else 'no'} boxes, {len(boxes)} valid", flush=True)
                self._look_show(boxes, shot)
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
            self.on_msg("sys", f"Error: {e}")
            self._set("idle")
