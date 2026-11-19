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


def _cache_file(text, voice):
    import hashlib
    return CACHE / (hashlib.sha1((str(voice) + "|" + f"{SPEED.get(str(voice), 1.0):.2f}" + "|" + text).encode()).hexdigest() + ".wav")


def synth(text, path, voice=None):
    voice = str(voice or _current)
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
    """Strip markdown noise that sounds bad when spoken."""
    text = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]", "", text)   # emoji: nothing sensible to say
    return re.sub(r"[*_`#>]+", "", text).strip()


def split_sentences(text):
    return [p for p in re.split(r"(?<=[.!?])\s+|\n+", text.strip()) if p.strip()]


class Speaker:
    """begin() -> gen; add(text, gen) queues a sentence; stop() interrupts everything.
    on_play(gen) fires when a sentence starts playing (first audio)."""

    def __init__(self, on_play=None):
        self.synthq, self.playq = queue.Queue(), queue.Queue()
        self.on_play = on_play
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

    def add(self, text, gen):
        text = clean(text)
        if not text or gen != self.gen:
            return
        with self._plock:
            self.pending += 1
        self.synthq.put((gen, text))

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
            gen, text = self.synthq.get()
            if gen != self.gen:
                self._done()
                continue
            fd, path = tempfile.mkstemp(suffix=".wav", prefix="jarvis-tts-")
            os.close(fd)
            try:
                synth(text, path)
                self.playq.put((gen, path))
            except Exception as e:
                print("tts error:", e)
                os.unlink(path)
                self._done()

    def _play_worker(self):
        while True:
            gen, path = self.playq.get()
            try:
                if gen == self.gen:
                    if self.on_play:
                        self.on_play(gen, path)
                    if self.muted:   # stay "speaking" for the sentence's length, but make no sound
                        with wave.open(path) as w:
                            end = time.time() + w.getnframes() / w.getframerate()
                        while time.time() < end and gen == self.gen:
                            time.sleep(0.05)
                    else:
                        self.player = subprocess.Popen(["pw-play", path], stderr=subprocess.DEVNULL)
                        if self.player.wait() not in (0, -15, -9) and gen == self.gen and self.on_fail:       # not killed by us (a new turn): the player could not play at all
                            self.on_fail()
            except Exception as e:
                print("play error:", e)
            finally:
                if os.path.exists(path):
                    os.unlink(path)
                self.last_end = time.time()
                self._done()
