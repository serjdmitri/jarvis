"""Mic recording and speaker playback via PipeWire (pw-record / pw-play)."""
import array
import math
import os
import signal
import subprocess
import tempfile
import wave

import numpy as np

RATE = 16000  # what whisper wants


MAX_RECORD_S = 120


def _die_with_parent():
    """Linux: ask the kernel to SIGKILL this child when its parent process dies."""
    import ctypes
    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, 9)          # PR_SET_PDEATHSIG = 1, SIGKILL = 9
    except Exception:
        pass


def cleanup_stale(max_age_s=600):
    """Delete Jarvis's own temp files that nobody is using any more (request recordings, wake-word keyword files, wake debug clips, pre-synthesis scratch)."""
    import glob
    import time as _t
    for prefix in ("jarvis-rec-", "jarvis-kw-", "jarvis-wake-", "jarvis-pre-", "jarvis-warm-", "phonecam-"):
        for f in glob.glob(os.path.join(tempfile.gettempdir(), prefix + "*")):
            try:
                if _t.time() - os.path.getmtime(f) > max_age_s:
                    if os.path.isdir(f):
                        import shutil
                        shutil.rmtree(f, ignore_errors=True)
                    else:
                        os.unlink(f)
            except OSError:
                pass


class Recorder:
    """Start/stop recorder writing 16 kHz mono s16 WAV."""

    def __init__(self):
        self.proc = None
        self.path = None

    def start(self):
        if self.proc:
            return
        fd, self.path = tempfile.mkstemp(suffix=".wav", prefix="jarvis-rec-")
        os.close(fd)
        self.proc = subprocess.Popen(
            ["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16", self.path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=_die_with_parent,        # a recorder can never outlive Jarvis (a restart used to leave it writing a growing file in /tmp for hours)
        )
        import threading
        proc = self.proc
        t = threading.Timer(MAX_RECORD_S, lambda: proc.poll() is None and proc.kill())              # and no request is longer than two minutes
        t.daemon = True
        t.start()

    def snapshot(self):
        """int16 samples recorded so far (readable while pw-record is still writing)."""
        if not self.path:
            return np.zeros(0, np.int16)
        with open(self.path, "rb") as f:
            raw = f.read()
        i = raw.find(b"data", 0, 256)
        if i < 0:
            return np.zeros(0, np.int16)
        pcm = raw[i + 8:]
        return np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16)

    def tail(self, n):
        """Last n samples (cheap: seeks instead of reading the whole file)."""
        if not self.path:
            return np.zeros(0, np.int16)
        try:
            with open(self.path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(44, size - n * 2))
                raw = f.read()
        except OSError:
            return np.zeros(0, np.int16)
        return np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16)

    def stop(self):
        """Stop and return the WAV path (None if not recording)."""
        if not self.proc:
            return None
        self.proc.send_signal(signal.SIGINT)  # lets pw-record finalize the WAV header
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc, path = None, self.path
        return path


def level(path):
    """Return (rms_dbfs, peak_dbfs, clipped_fraction, seconds) for a 16-bit WAV."""
    with wave.open(path) as w:
        n, rate = w.getnframes(), w.getframerate()
        samples = array.array("h", w.readframes(n))
    if not samples:
        return (-120.0, -120.0, 0.0, 0.0)
    peak = max(abs(s) for s in samples)
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    clipped = sum(1 for s in samples if abs(s) >= 32767) / len(samples)
    db = lambda v: 20 * math.log10(max(v, 1) / 32768)
    return (db(rms), db(peak), clipped, n / rate)


def play(path):
    subprocess.run(["pw-play", path], check=False)


def read_wav(path):
    with wave.open(path) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


_ACK = None


def ack(event="wake"):
    """The short chime in the active persona's style, played without blocking: wake = "I heard my name", done = "I heard you finish" (see sounds.py; off or quiet by setting)."""
    from . import sounds
    sounds.play(event)


def chime():
    """A longer two-note chime for the sound test; blocks, returns pw-play's exit code (non-zero = could not play)."""
    from . import config
    config.RUN_DIR.mkdir(exist_ok=True)
    path = str(config.RUN_DIR / "chime.wav")
    n = int(48000 * 0.5)
    t = np.arange(n) / 48000
    f = np.where(t < 0.25, 660, 880)
    y = np.sin(2 * np.pi * f * t) * np.minimum(1, np.minimum(t / 0.01, (0.25 - (t % 0.25)) / 0.08)) * 0.35
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
        w.writeframes((y * 32767).astype(np.int16).tobytes())
    try:
        return subprocess.run(["pw-play", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode
    except Exception:
        return 1


_SOFT = None


def soft_tone():
    """A tiny soft tone: 'your turn, I'm listening' (quieter and lower than the wake chime)."""
    global _SOFT
    from . import config
    if _SOFT is None:
        config.RUN_DIR.mkdir(exist_ok=True)
        _SOFT = str(config.RUN_DIR / "soft.wav")
        n = int(48000 * 0.075)
        t = np.arange(n) / 48000
        y = np.sin(2 * np.pi * 620 * t) * np.minimum(1, np.minimum(t / 0.01, (t[-1] - t) / 0.035)) * 0.09
        with wave.open(_SOFT, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
            w.writeframes((y * 32767).astype(np.int16).tobytes())
    subprocess.Popen(["pw-play", _SOFT], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
