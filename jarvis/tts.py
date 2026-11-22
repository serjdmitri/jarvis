"""Local text-to-speech: piper, kept loaded. Two stages (synthesize / play) so the next
sentence is synthesized while the current one is playing."""
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
import wave

from . import config

_voices = {}                          # path -> loaded PiperVoice
_current = str(config.PIPER_VOICE)
_lock = threading.Lock()


def set_voice(path):
    """Switch the voice used for the next sentences (loads it on first use)."""
    global _current
    _current = str(path)
    return _get()


def _get(path=None):
    path = str(path or _current)
    if path not in _voices:
        from piper import PiperVoice
        _voices[path] = PiperVoice.load(path)
    return _voices[path]


SPEED = {}                                   # voice path -> speaking speed multiplier (0.7 to 1.6), kept per voice (Jarvis and Rose separately)


def set_speed(voice, mult):
    SPEED[str(voice)] = float(mult)


def _scale(voice):
    """Piper length_scale = the voice's own value divided by the speed: pace changes, pitch barely does."""
    base = getattr(_get(voice).config, "length_scale", 1.0) or 1.0
    return base / max(0.5, SPEED.get(str(voice), 1.0))


CACHE = config.RUN_DIR / "ttscache"      # short, frequently repeated lines (acks, greetings, wake replies) are synthesized once


def prune_cache(max_files=500, max_age_days=30):
    """The pre-synthesised lines are cached per voice AND per speed, so the cache grows every time you change the speed: keep the newest 500 and drop anything older than a month."""
    import time as _t
    try:
        files = sorted(CACHE.glob("*.wav"), key=lambda f: f.stat().st_mtime, reverse=True)
    except OSError:
        return 0
    n = 0
    for i, f in enumerate(files):
        try:
            if i >= max_files or _t.time() - f.stat().st_mtime > max_age_days * 86400:
                f.unlink()
                n += 1
        except OSError:
            pass
    return n


def _cache_file(text, voice):
    import hashlib
    return CACHE / (hashlib.sha1((str(voice) + "|" + f"{SPEED.get(str(voice), 1.0):.2f}" + "|" + text).encode()).hexdigest() + ".wav")


def synth(text, path, voice=None, mood=None):
    """Write the speech for `text` to a wav. A mood (see moods.py) nudges speed and pitch a little; moods skip the cache (it holds neutral lines)."""
    voice = str(voice or _current)
    if mood:
        from . import moods
        sp, pi = moods.VOICE.get(mood, (1, 1))
        with _lock:
            import numpy as _np
            from piper import SynthesisConfig
            base = _scale(voice)
            tmp = path + ".m"
            with wave.open(tmp, "wb") as w:
                _get(voice).synthesize_wav(text, w, syn_config=SynthesisConfig(length_scale=base * pi / sp))      # slower when pitched up, so the net pace is the mood's speed
            with wave.open(tmp) as r:
                rate, ch, sw, raw = r.getframerate(), r.getnchannels(), r.getsampwidth(), r.readframes(r.getnframes())
            os.unlink(tmp)
            data = moods.retune(_np.frombuffer(raw, dtype=_np.int16), rate, mood)
            with wave.open(path, "wb") as w:
                w.setnchannels(ch); w.setsampwidth(sw); w.setframerate(rate); w.writeframes(data.tobytes())
        return path
    cf = _cache_file(text, voice) if len(text) <= 70 else None
    if cf is not None and cf.exists():                     # instant: no synthesis
        import shutil
        shutil.copyfile(cf, path)
        return path
    with _lock, wave.open(path, "wb") as w:
        from piper import SynthesisConfig
        _get(voice).synthesize_wav(text, w, syn_config=SynthesisConfig(length_scale=_scale(voice)))
    if cf is not None:
        import shutil
        CACHE.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, cf)
    return path


def prewarm(lines, voice):
    """Fill the cache for short lines in the background so the first use is instant."""
    p = tempfile.mktemp(suffix=".wav", prefix="jarvis-pre-")
    for t in lines:
        try:
            synth(t, p, voice)
        except Exception as e:
            print("prewarm failed:", t, e)
    if os.path.exists(p):
        os.unlink(p)


def warm():
    p = tempfile.mktemp(suffix=".wav", prefix="jarvis-warm-")
    synth("Ready.", p)
    os.unlink(p)


def clean(text):
    """Strip markdown noise that sounds bad when spoken (and any mood tag)."""
    from . import moods
    text = moods.strip(text)
    text = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]", "", text)   # emoji: nothing sensible to say
    return re.sub(r"[*_`#>]+", "", text).strip()


def split_sentences(text):
    parts = [p for p in re.split(r"(?<=[.!?])\s+|\n+", text.strip()) if p.strip()]
    if parts and len(parts[0]) > 46:                       # a long first sentence is cut at its first comma (a natural pause): the voice starts after the short piece is made, not after the whole sentence
        m = re.search(r"^(.{14,40}?[,:;])\s+(.{12,})$", parts[0])
        if m:
            parts[0:1] = [m.group(1), m.group(2)]
    return parts


class Speaker:
    """begin() -> gen; add(text, gen) queues a sentence; stop() interrupts everything.
    on_play(gen) fires when a sentence starts playing (first audio)."""

    def __init__(self, on_play=None):
        self.synthq, self.playq = queue.Queue(), queue.Queue()
        self.on_play = on_play
        self.on_mood = None                 # called with the mood of each sentence as it starts playing (None = neutral)
        self.mood = None
        self.on_fail = None                 # called when pw-play itself fails: the app shows a "No sound: <reason>" card
        self.muted = False
        self.last_end = 0.0                  # wall time the last sentence finished playing (conversation mode opens the mic from here)
        self.player = None
        self.gen = 0
        self.pending = 0
        self._plock = threading.Lock()
        threading.Thread(target=self._synth_worker, daemon=True).start()
        threading.Thread(target=self._play_worker, daemon=True).start()

    def begin(self):
        self.gen += 1
        self._kill()
        return self.gen

    def add(self, text, gen, mood=None):
        from . import moods, settings
        text, tagged = moods.take(text)
        text = clean(text)
        if not text or gen != self.gen:
            return
        mood = (tagged or mood or moods.guess(text)) if settings.get("feelings") is not False else None
        with self._plock:
            self.pending += 1
        self.synthq.put((gen, text, mood))

    def say(self, text):
        g = self.begin()
        for s in split_sentences(text):
            self.add(s, g)

    def stop(self):
        self.gen += 1
        self._kill()

    def busy(self):
        return self.pending > 0

    def wait(self):
        while self.busy():
            time.sleep(0.05)

    def _done(self):
        with self._plock:
            self.pending -= 1

    def _kill(self):
        p = self.player
        if p and p.poll() is None:
            p.terminate()

    def _synth_worker(self):
        while True:
            gen, text, mood = self.synthq.get()
            if gen != self.gen:
                self._done()
                continue
            fd, path = tempfile.mkstemp(suffix=".wav", prefix="jarvis-tts-")
            os.close(fd)
            try:
                synth(text, path, mood=mood)
                self.playq.put((gen, path, mood))
            except Exception as e:
                print("tts error:", e)
                os.unlink(path)
                self._done()

    def _play_worker(self):
        while True:
            gen, path, mood = self.playq.get()
            try:
                if gen == self.gen:
                    self.mood = mood
                    if self.on_mood:
                        self.on_mood(mood)
                    if self.on_play:
                        self.on_play(gen, path)
                    if self.muted:   # stay "speaking" for the sentence's length, but make no sound
                        with wave.open(path) as w:
                            end = time.time() + w.getnframes() / w.getframerate()
                        while time.time() < end and gen == self.gen:
                            time.sleep(0.05)
                    else:
                        from . import whisper_mode
                        self.player = subprocess.Popen(["pw-play"] + (["--volume", str(whisper_mode.GAIN)] if whisper_mode.active() else []) + [path], stderr=subprocess.DEVNULL)
                        if self.player.wait() not in (0, -15, -9) and gen == self.gen and self.on_fail:       # not killed by us (a new turn): the player could not play at all
                            self.on_fail()
            except Exception as e:
                print("play error:", e)
            finally:
                if os.path.exists(path):
                    os.unlink(path)
                self.last_end = time.time()
                self._done()
                if self.pending <= 0 and self.on_mood:
                    self.on_mood(None)
