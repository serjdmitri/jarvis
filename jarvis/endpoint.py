"""Speculative endpointing. While the talk key is held, watch the mic with a cheap energy VAD.
When you pause (>= SILENCE_S after speech), start transcribing right away in the background.
If you then release without saying more, that transcript is already done."""
import os
import threading
import time
from types import SimpleNamespace

import numpy as np

from .audio import RATE

DEBUG = os.environ.get("JARVIS_DEBUG") == "1"
FRAME = 480            # 30 ms
SILENCE_S = 0.6
MIN_SPEECH_FRAMES = 8  # ~0.25 s, ignore clicks


def _frame_db(x):
    n = len(x) // FRAME
    if n == 0:
        return np.zeros(0, np.float32)
    f = x[: n * FRAME].astype(np.float32).reshape(n, FRAME) / 32768.0
    return 20 * np.log10(np.maximum(np.sqrt((f * f).mean(axis=1)), 1e-5))


def speech_span(x):
    """(first_sample, end_sample) of speech in an int16 array, or None."""
    db = _frame_db(x)
    if len(db) == 0:
        return None
    noise = min(float(np.percentile(db, 10)), -35.0)
    mask = db > max(noise + 9.0, -50.0)
    if mask.sum() < MIN_SPEECH_FRAMES:
        return None
    idx = np.flatnonzero(mask)
    return int(idx[0]) * FRAME, (int(idx[-1]) + 1) * FRAME


def trim(x):
    """Cut leading/trailing silence (keeps a little margin). Returns x unchanged if no speech found."""
    span = speech_span(x)
    if not span:
        return x
    return x[max(0, span[0] - int(0.2 * RATE)): min(len(x), span[1] + int(0.3 * RATE))]


class Endpointer:
    def __init__(self, rec, transcribe):
        self.rec, self.transcribe = rec, transcribe
        self.stop_evt = threading.Event()
        self.spec = None
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self.thread.start()

    def _loop(self):
        while not self.stop_evt.wait(0.15):
            data = self.rec.snapshot()
            span = speech_span(data)
            if not span or len(data) - span[1] < SILENCE_S * RATE:
                continue
            s = self.spec
            if s and (not s.done.is_set() or abs(s.end - span[1]) <= 3 * FRAME):
                continue  # already covers this utterance (or one is still running)
            self._launch(data, span[1])

    def _launch(self, data, end):
        spec = SimpleNamespace(end=end, text=None, done=threading.Event())
        self.spec = spec
        if DEBUG:
            print(f"[ep] launch spec: audio={len(data)/RATE:.2f}s speech_end={end/RATE:.2f}s", flush=True)

        def run():
            t = time.time()
            try:
                spec.text = self.transcribe(trim(data))
            except Exception:
                spec.text = None
            spec.done.set()
            if DEBUG:
                print(f"[ep] spec done in {time.time()-t:.2f}s -> {spec.text!r}", flush=True)
        threading.Thread(target=run, daemon=True).start()

    def finish(self, final):
        """Call after the recorder stopped, with the final int16 samples.
        Returns the speculative transcript if it still applies, else None."""
        self.stop_evt.set()
        self.thread.join(timeout=1)
        s, span = self.spec, speech_span(final)
        if DEBUG:
            print(f"[ep] finish: final={len(final)/RATE:.2f}s span_end={span and span[1]/RATE} spec_end={s and s.end/RATE}", flush=True)
        if s and span and abs(s.end - span[1]) <= 3 * FRAME:
            s.done.wait(30)
            return s.text
        return None
