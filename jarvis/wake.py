"""Hands-free wake phrases for the active persona's name.

Two paths share one continuous mic stream (the main loop is its ONLY reader):
  FAST  - a streaming keyword spotter (kws.py) sees every frame and fires ~0.1-0.4 s after the phrase ends.
  SLOW  - fallback for phrases it misses: energy VAD -> short clip -> faster-whisper -> fuzzy phrase match.
A rolling buffer of the last few seconds keeps what you say right after the phrase, so the request is not lost
while the wake path is still working. The chime plays the instant a phrase matches. Every stage is timed and
printed:  [wake] ...  Nothing here runs while muted, and it never runs while the assistant is busy."""
import difflib
import os
import queue
import random
import re
import subprocess
import threading
import time
from collections import deque

import numpy as np

from . import audio, kws, personas, settings, stt

RATE = 16000
FRAME = 480                      # 30 ms
MAX_WAKE_CLIP_S = 4.0            # a wake phrase (+ optional short request) is short; longer speech is just conversation, never transcribed
MIN_SPEECH_S = 0.35
WAKE_END_SILENCE_S = 0.5
CMD_END_SILENCE_S = 0.45          # after a longer request (see CMD_END_SHORT_S for one-word ones)
CMD_END_SHORT_S = 0.6
PATIENCE = {"fast": (0.45, 0.6), "normal": (0.8, 1.0), "patient": (1.2, 1.5)}      # (after a normal request, after a very short one): the "listening patience" setting
CMD_START_TIMEOUT_S = 6.0
ANSWER_WAIT_S = 8.0               # when the assistant asked you something: wait this long for the answer, no wake word
CMD_MAX_S = 25.0
MIN_GAP_S = 1.0                  # at most one whisper wake transcription per this long
BUDGET_S, BUDGET_WINDOW_S = 8.0, 60.0    # whisper may run at most this many seconds per window (it burns ~2.5 cores while it runs)
KWS_FLUSH_QUIET = 2                # frames (120 ms) of quiet after speech: then the spotter is fed half a second of silence at once, so it decides NOW instead of waiting ~0.25 s of real time for its chunk to fill
KWS_PREROLL_S = 0.6              # the spotter only runs while there is speech-like energy; it is first fed this much earlier audio
KWS_HANG_S = 1.2                 # ... and keeps running this long after the last loud frame
MAX_BACKOFF_S = 2.5               # (the duty-cycle budget below is the real CPU governor; a long back-off would starve real wake phrases)
GUARD_S = 0.8                    # stay deaf this long after the assistant stops talking
RING_S = 4.0                     # rolling buffer
CHIME_GRACE_S = 0.14             # while the chime plays the frames are kept but not counted as speech/silence
WINDOW_MARGIN_S = 0.45            # the follow-up window ignores this long after the last word (the room echo of Jarvis's own voice)
WINDOW_MIN_SPEECH = 10            # frames (0.3 s) of real speech before a follow-up counts (a cough or a door is not a command)
CONV_MARGIN_S = 0.0              # conversation mode: ignore this long after the assistant's last word (its own voice tail)
PREROLL_S = 0.5                  # keyword path: keep this much audio from before it fired (fast continuations)


# --------------------------------------------------------------------------- phrase matching
def norm(text):
    t = text.lower().replace("’", "'")
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _like(tok, name):
    return tok == name or difflib.SequenceMatcher(None, tok, name).ratio() >= .72


def match_wake(text, name):
    """-> (kind, remainder) or None.  kind: hey | wakeup | awake | home ; remainder = words after the phrase."""
    t = norm(text)
    if re.search(r"\b(daddy|dad|daddies)('s| is| has)? (home|back)\b", t):
        return ("home", "")
    toks, name = t.split(), name.lower()
    for i, tok in enumerate(toks):
        if not _like(tok, name):
            continue
        before, after = toks[:i], toks[i + 1:]
        a0 = " ".join(after)
        if re.fullmatch(r"(?:please )?(?:wake up|wakeup|wake)(?: please)?|(?:are )?you (?:there|awake|up)", a0) and (not before or before[-1] in ("hey", "hi", "hello", "ok", "okay", "yo")):
            return ("wakeup", "")                                   # "hey Jarvis wake up", "Jarvis wake up": just wake, no brain question
        if before and before[-1] in ("hey", "hi", "hello", "ok", "okay", "yo"):
            return ("hey", " ".join(after))
        if len(before) >= 2 and before[-2:] == ["wake", "up"]:
            return ("wakeup", " ".join(after))
        a = " ".join(after)
        m = re.match(r"(are )?you (awake|up|there|asleep)\b", a)
        if m:
            return ("awake", a[m.end():].strip())
    return None


def strip_wake(text, name):
    """The request captured after a wake may start with the tail of the wake phrase: drop it."""
    hit = match_wake(text, name)
    if hit:
        return hit[1]
    toks, nm = text.split(), name.lower()
    while toks:
        w = norm(toks[0])
        if _like(w, nm) or w in ("hey", "hi", "ok", "okay") or (len(w) >= 3 and w != nm and nm.endswith(w)):   # name, greeting, or a clipped tail like "vis"
            toks.pop(0)
        else:
            break
    return " ".join(toks)


# --------------------------------------------------------------------------- audio source
class PwStream:
    """Continuous 16 kHz mono int16 frames from pw-record. close() stops the capture completely."""

    def __init__(self):
        self.p = subprocess.Popen(["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16", "-"],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        head = self.p.stdout.read(44)
        self.buf = b"" if head[:4] == b"RIFF" else head

    def frames(self):
        while True:
            while len(self.buf) < FRAME * 2:
                chunk = self.p.stdout.read(FRAME * 2)
                if not chunk:
                    return
                self.buf += chunk
            f, self.buf = self.buf[:FRAME * 2], self.buf[FRAME * 2:]
            yield np.frombuffer(f, dtype=np.int16)

    def close(self):
        if self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.p.kill()


def frame_db(f):
    x = f.astype(np.float32) / 32768.0
    return 20 * np.log10(max(float(np.sqrt((x * x).mean())), 1e-5))


class FifoSource:
    """Test hook: JARVIS_MIC_FIFO=/path makes the microphone a named pipe of raw 16 kHz mono s16 audio. Room noise when nothing
    is written, real time pacing. Lets a test feed the REAL app exact audio at exact moments, independent of the room."""

    def __init__(self):
        path = os.environ["JARVIS_MIC_FIFO"]
        if not os.path.exists(path):
            os.mkfifo(path)
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        self.buf, self.closed, self.i, self.t0 = b"", False, 0, time.time()
        self.rng = np.random.default_rng(1)

    def frames(self):
        while not self.closed:
            due = self.t0 + self.i * FRAME / RATE
            if time.time() < due:
                time.sleep(due - time.time())
            try:
                self.buf += os.read(self.fd, FRAME * 2 - len(self.buf))
            except BlockingIOError:
                pass
            if len(self.buf) >= FRAME * 2:
                f, self.buf = np.frombuffer(self.buf[:FRAME * 2], dtype=np.int16), self.buf[FRAME * 2:]
            else:
                f = self.rng.normal(0, 58, FRAME).astype(np.int16)          # ~ -55 dBFS room noise
            self.i += 1
            yield f

    def close(self):
        self.closed = True


def default_source():
    return FifoSource() if os.environ.get("JARVIS_MIC_FIFO") else PwStream()


# --------------------------------------------------------------------------- the listener
class HandsFree:
    def __init__(self, assistant, source_factory=None, use_kws=True, wake=True):
        self.a, self.source_factory, self.use_kws = assistant, source_factory or default_source, use_kws
        self.wake = wake                     # False: only keeps the rolling buffer and request capture (conversation mode with wake phrases off)
        self.stop_evt = threading.Event()
        self.thread = None
        self.work = queue.Queue(maxsize=1)
        self.stream = None
        self.quiet_until = 0.0
        self.last_submit = 0.0
        self.last_wake = 0.0
        self.noise = -50.0
        self.cap = None                      # request capture in progress (owned and fed by the main loop only)
        self.ring = deque(maxlen=int(RING_S * RATE / FRAME))     # (time, frame) rolling buffer
        self.utt_start = None                # when the current utterance's first loud frame arrived
        self.spotter = None
        self.stats = {"clips": 0, "whisper_s": 0.0, "wakes": 0, "skipped": 0, "kws_wakes": 0}
        self.spent = deque()
        self.misses = 0
        self.timings = []                    # last wake timing dicts (for tests / logs)
        self.lag = self.max_lag = 0.0

    # -- lifecycle
    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_evt.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        threading.Thread(target=self._worker, daemon=True).start()

    def stop(self):
        self.stop_evt.set()
        if self.cap is not None:                                 # sleeping mid-request: give the state back
            self.cap = None
            self.a._set("idle")
        if self.stream:
            self.stream.close()

    def _busy(self):
        a = self.a
        busy = a.state != "idle" or a.speaker.busy()
        if busy:
            self.quiet_until = time.time() + GUARD_S
        return busy or time.time() < self.quiet_until

    # -- main audio loop (the only reader of the pipe)
    def _loop(self):
        if self.use_kws:
            try:
                self.spotter = kws.Spotter()
            except Exception as e:                               # no spotter: the whisper path still works
                print("[wake] keyword spotter unavailable, whisper path only:", e, flush=True)
        try:
            self.stream = self.source_factory()
        except Exception as e:
            print("hands-free: cannot open mic:", e)
            return
        hist, n = deque(maxlen=100), 0
        t_start, n_frames = time.time(), 0
        pre = deque(maxlen=10)
        clip, in_speech, speech_frames, silence_frames, run, last_loud_t = [], False, 0, 0, 0, 0.0
        was_busy = False
        kws_on_until, kws_active, kws_quiet, kws_flushed = 0.0, False, 0, True
        for f in self.stream.frames():
            if self.stop_evt.is_set():
                break
            now = time.time()
            self.alive_t = now                                   # heartbeat for the watchdog
            n_frames += 1
            self.lag = now - (t_start + n_frames * FRAME / RATE)       # how far behind real time the loop is (a backlog means lost audio)
            self.max_lag = max(self.max_lag, self.lag)
            db = frame_db(f)
            if self.cap is not None:                            # capturing the request after a wake
                self.ring.append((now, f))
                self._capture_frame(f, db, now)
                continue
            if self._busy():
                hist.clear(); pre.clear(); self.ring.append((now, f))      # keep recording: a conversation reply starts the moment speech ends
                clip, in_speech, speech_frames, silence_frames, run = [], False, 0, 0, 0
                self.utt_start = None
                was_busy = True
                continue
            if was_busy:                                        # back from busy: stale spotter context
                was_busy = False
                kws_active = False
            self.ring.append((now, f))
            if not self.wake:
                continue
            hist.append(db); n += 1
            if n % 10 == 0 and len(hist) > 20:
                self.noise = float(np.percentile(hist, 20))
            loud = db > max(self.noise + 10.0, -48.0)
            if loud:
                last_loud_t = now
            # ---- FAST path: streaming keyword spotter sees every frame
            if self.spotter:
                if loud:
                    kws_on_until = now + KWS_HANG_S
                fired = []
                if now < kws_on_until:                               # speech-like energy: run the spotter (silence costs ~nothing)
                    if not kws_active:
                        kws_active = True
                        self.spotter.reset()
                        for (_, pf) in list(self.ring)[-int(KWS_PREROLL_S * RATE / FRAME) - 1:-1]:
                            fired += self.spotter.feed(pf)
                    fired += self.spotter.feed(f)
                    if loud:
                        kws_quiet, kws_flushed = 0, False
                    else:
                        kws_quiet += 1
                        if kws_quiet == KWS_FLUSH_QUIET and not kws_flushed:
                            kws_flushed = True
                            fired += self.spotter.feed(np.zeros(8000, np.int16))      # the end of a phrase is known: flush the spotter's chunk (about 120 ms after the last word instead of ~300)
                else:
                    kws_active = False
                if fired:
                    name = next((x for x in fired if kws.persona_of(x) in (None, self.a.persona)), None)
                    if name and now - self.last_wake > 1.0:
                        self._kws_wake(name, now)
                        kws_active = False
                        clip, in_speech, speech_frames, silence_frames, run = [], False, 0, 0, 0
                        continue
            # ---- SLOW path: energy VAD segments an utterance for whisper
            if not in_speech:
                pre.append(f)
                run = run + 1 if loud else 0
                if run >= 4:
                    in_speech, clip, speech_frames, silence_frames = True, list(pre), run, 0
                    self.utt_start = now - run * FRAME / RATE
            else:
                clip.append(f)
                if loud:
                    speech_frames += 1; silence_frames = 0
                else:
                    silence_frames += 1
                too_long = len(clip) * FRAME / RATE > MAX_WAKE_CLIP_S
                if silence_frames * FRAME / RATE >= WAKE_END_SILENCE_S or too_long:
                    secs = speech_frames * FRAME / RATE
                    gap = min(MAX_BACKOFF_S, MIN_GAP_S * (1.5 ** min(self.misses, 6)))
                    if (not too_long and secs >= MIN_SPEECH_S and now - self.last_submit >= gap and now - self.last_wake > 8.0):
                        self._submit(np.concatenate(clip), self.utt_start or now, last_loud_t, now)
                    in_speech, clip, run = False, [], 0
                    pre.clear()
        if self.stream:
            self.stream.close()

    # -- FAST path wake -------------------------------------------------------------
    def _kws_wake(self, name, t_fired):
        a = self.a
        kind, p = kws.kind_of(name), personas.PERSONAS[a.persona]
        self.last_wake = t_fired
        self.stats["wakes"] += 1; self.stats["kws_wakes"] += 1
        a.turn += 1
        a.speaker.stop()
        tm = {"path": "keyword", "name": name, "t_fired": t_fired, "utt_start": self.utt_start or t_fired}
        if kind == "home":
            a.on_msg("sys", "[wake] home")
            tm["t_chime"] = time.time(); a.say(p["welcome_home"]); tm["t_ready"] = time.time()
            self._log(tm); return
        a.on_msg("sys", f"[wake] {kind}")
        if a.maybe_morning_brief():                             # the first wake of the day, if you switched the morning brief on
            return
        if kind == "awake":                                     # a spoken reply first, then listen
            def reply():
                a.say(random.choice(p["awake"]), wait=True)
                time.sleep(0.15)
                self._arm_capture(time.time(), tm, chime=False)
            threading.Thread(target=reply, daemon=True).start()
            tm["t_chime"] = time.time()
            return
        audio.ack(); tm["t_chime"] = time.time()                # the instant it matches, before anything slow
        self._arm_capture(t_fired - PREROLL_S, tm, chime=True)

    # -- SLOW path ------------------------------------------------------------------
    def _submit(self, data, t_start, t_speech_end, t_clip_done):
        self.last_submit = time.time()
        try:
            self.work.put_nowait((data, t_start, t_speech_end, t_clip_done))
        except queue.Full:
            pass

    def _worker(self):
        while not self.stop_evt.is_set():
            try:
                data, t_start, t_end, t_clip = self.work.get(timeout=0.5)
            except queue.Empty:
                continue
            if self.a.state != "idle" or time.time() - self.last_wake < 8.0:
                continue
            now = time.time()
            while self.spent and now - self.spent[0][0] > BUDGET_WINDOW_S:
                self.spent.popleft()
            if sum(d for _, d in self.spent) > BUDGET_S:
                self.stats["skipped"] += 1
                continue
            p = personas.PERSONAS[self.a.persona]
            t0 = time.time()
            try:
                text = stt.transcribe(data, prompt=f"Hey {p['name']}. {p['name']}, you awake? Wake up {p['name']}. Daddy's home.")
            except Exception as e:
                print("hands-free transcribe failed:", e); continue
            t_text = time.time()
            self.spent.append((t_text, t_text - t0)); self.stats["clips"] += 1; self.stats["whisper_s"] += t_text - t0
            hit = match_wake(text, p["name"])
            t_match = time.time()
            self.misses = 0 if hit else self.misses + 1
            if hit or os.environ.get("JARVIS_WAKE_DEBUG"):           # never log what was merely overheard in the room
                print(f"[wake] heard {text!r} -> {hit}", flush=True)
            if hit and self.a.state == "idle" and not self.stop_evt.is_set():
                self.stats["wakes"] += 1
                self._woke(hit[0], hit[1], p, {"path": "whisper", "name": hit[0], "utt_start": t_start, "t_clip": t_clip, "t_text": t_text, "t_match": t_match}, t_end)

    def _woke(self, kind, rest, p, tm, t_speech_end):
        a = self.a
        self.last_wake = time.time()
        a.turn += 1
        a.speaker.stop()
        if kind == "home":
            a.on_msg("sys", f"[wake] {kind}")
            tm["t_chime"] = time.time(); a.say(p["welcome_home"]); tm["t_ready"] = time.time(); self._log(tm); return
        if len(rest.split()) >= 2:                              # "Hey Jarvis, what's the time?" -> just answer it
            a.on_msg("sys", "[wake] " + kind)
            tm["t_chime"] = tm["t_ready"] = time.time(); self._log(tm)
            a.ask_text(rest); return
        a.on_msg("sys", "[wake] " + kind)
        if kind == "awake":
            tm["t_chime"] = time.time()
            a.say(random.choice(p["awake"]), wait=True)
            time.sleep(0.15)
            self._arm_capture(time.time(), tm, chime=False)
        else:
            audio.ack(); tm["t_chime"] = time.time()
            self._arm_capture(t_speech_end - 0.1, tm, chime=True)   # keep everything said after the phrase while whisper was busy

    # -- request capture ------------------------------------------------------------
    def _arm_capture(self, since, tm, chime):
        """Arm request capture; the main loop feeds it. `since` = wall time to start from (rolling buffer)."""
        a = self.a
        if self.stop_evt.is_set() or a.muted or a.state not in ("idle",):
            return
        a._set("recording", "listening")
        now = time.time()
        pre = [f for (t, f) in list(self.ring) if t >= since]
        self.cap = {"clip": pre, "speech": 0, "silence": 0, "t0": now, "lvl": 0.0, "hist": deque(maxlen=60), "noise": self.noise,
                    "grace_until": now + (CHIME_GRACE_S if chime else 0.0), "strip": True}
        tm["t_ready"] = time.time(); tm["preroll_s"] = len(pre) * FRAME / RATE
        self._log(tm)

    def open_conversation(self, tts_end, wait=None, window=False):
        """Conversation mode: the mic opens the moment the assistant stops talking, with no wake word. Audio from just after its
        last word is already in the rolling buffer, so a quick reply is not lost."""
        a = self.a
        if self.stop_evt.is_set() or a.muted:
            return False
        now = time.time()
        since = (tts_end or now) + (WINDOW_MARGIN_S if window else CONV_MARGIN_S)               # skip the tail of the assistant's own voice (the speaker's echo in the room)
        pre = [f for (t, f) in list(self.ring) if t >= since]
        noise, speech, silence = self.noise, 0, 0
        thr = max(noise + 10.0, -48.0)
        for pf in pre:                                          # did the user already start (or even finish) talking?
            if frame_db(pf) > thr:
                speech += 1; silence = 0
            else:
                silence += 1
        if not window:
            audio.soft_tone()
        self.cap = {"clip": pre, "speech": speech, "silence": silence, "t0": now, "lvl": 0.0, "hist": deque(maxlen=60), "noise": noise,
                    "grace_until": now + CHIME_GRACE_S, "conv": True, "wait": wait or CMD_START_TIMEOUT_S, "window": bool(window)}
        if window:                                              # the follow-up window: a quiet countdown ring, no tone
            from . import opener
            opener.NOTIFY({"t": "window", "on": True, "secs": int(wait or 0)})
        a._set("recording", "conversation")
        print(f"[conv] mic open {now - (tts_end or now):.2f}s after the assistant stopped speaking | kept {len(pre) * FRAME / RATE:.2f}s of audio from just after its last word", flush=True)
        return True

    def _capture_frame(self, f, db, now):
        a, c = self.a, self.cap
        c["clip"].append(f); c["hist"].append(db)
        if c.get("window") and a.speaker.busy():                # Jarvis started talking again (a reminder, a timer): that is not the user; the window waits and its clock stops
            c["t0"] += FRAME / RATE; c["clip"] = []; c["speech"] = c["silence"] = 0
            return
        if now < c["grace_until"]:                              # the chime/tone is playing: keep the audio, do not count it as speech or silence
            return
        if len(c["hist"]) > 15:
            c["noise"] = float(np.percentile(c["hist"], 20))
        loud = db > max(c["noise"] + (13.0 if c.get("window") else 10.0), -46.0 if c.get("window") else -48.0)       # the window is stricter: a TV or a room full of talk should not look like a command
        if now - c["lvl"] > .05:
            a.on_level("mic", min(1.0, max(0.0, (db + 55) / 35)), db); c["lvl"] = now
        if loud:
            c["speech"] += 1; c["silence"] = 0
        else:
            c["silence"] += 1
        el = now - c["t0"]
        if (c["speech"] == 0 or (c.get("window") and c["speech"] < WINDOW_MIN_SPEECH and c["silence"] > 15)) and el > c.get("wait", CMD_START_TIMEOUT_S):
            conv, self.cap = c.get("conv"), None
            if c.get("window"):
                from . import opener
                opener.NOTIFY({"t": "window", "on": False})
            if conv:                                            # nothing said for ~6 s: close quietly, back to wake-word listening
                a._set("idle"); a.conversation_end()
            else:
                a.on_msg("sys", "(no request heard)"); a._set("idle")
            return
        if (c["speech"] >= (WINDOW_MIN_SPEECH if c.get("window") else 8) and c["silence"] * FRAME / RATE >= PATIENCE.get(settings.get("listen_patience"), PATIENCE["fast"])[0 if c["speech"] >= 20 else 1]) or (el > CMD_MAX_S and not (c.get("window") and c["speech"] < WINDOW_MIN_SPEECH) and (not c.get("window") or el > c.get("wait", 0) + CMD_MAX_S - 5)):
            data, self.cap = np.concatenate(c["clip"]), None
            if self.spotter:
                self.spotter.reset()                            # fresh context after a request
            if os.environ.get("JARVIS_WAKE_DEBUG"):
                import wave
                with wave.open(f"/tmp/jarvis-wake-{int(time.time())}.wav", "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(data.tobytes())
            conv = bool(c.get("conv"))
            if c.get("window"):
                from . import opener
                opener.NOTIFY({"t": "window", "on": False})
            a.submit_audio(endpoint_trim(data), strip_wake=not conv, conv=conv)

    # -- timing report ----------------------------------------------------------------
    def _log(self, tm):
        self.timings.append(tm)
        us, t = tm.get("utt_start"), tm
        if tm["path"] == "keyword":
            msg = (f"[wake] KWS {tm['name']}: speech start -> keyword fired {tm['t_fired'] - us:+.2f}s | fired -> chime {tm['t_chime'] - tm['t_fired']:.3f}s"
                   f" | chime -> mic ready {tm['t_ready'] - tm['t_chime']:.3f}s | fired -> ready {tm['t_ready'] - tm['t_fired']:.3f}s"
                   + (f" | kept {tm['preroll_s']:.2f}s of audio from before" if "preroll_s" in tm else "") + f" | loop lag {self.lag:.2f}s (max {self.max_lag:.2f}s)")
        else:
            msg = (f"[wake] whisper {tm['name']}: speech start -> clip ready {tm['t_clip'] - us:.2f}s | transcribed +{tm['t_text'] - tm['t_clip']:.2f}s"
                   f" | matched +{tm['t_match'] - tm['t_text']:.3f}s | chime +{tm['t_chime'] - tm['t_match']:.3f}s | mic ready +{tm['t_ready'] - tm['t_chime']:.3f}s"
                   + (f" | kept {tm['preroll_s']:.2f}s of audio said meanwhile" if "preroll_s" in tm else ""))
        print(msg, flush=True)


def endpoint_trim(data):
    from . import endpoint
    return endpoint.trim(data)
