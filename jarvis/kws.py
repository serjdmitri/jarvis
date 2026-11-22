"""Streaming keyword spotter for the wake phrases (sherpa-onnx, 3.3M zipformer, int8, ~1 core-percent).

Unlike the whisper path (wait for a pause, then ~2 s of fixed whisper cost) this fires about 0.1-0.3 s after the
phrase ends. It spots arbitrary text phrases (no training), so "Hey Rose" works exactly like "Hey Jarvis".
Phrases are tokenised with the model's own BPE; each has a boost and a trigger threshold."""
import os
import tempfile

import numpy as np

from . import config

MODEL = config.ROOT / "models" / "kws"
RATE = 16000

# name -> (spoken text, boost, threshold).  kind is derived from the name: hey_*, wakeup_*, *_awake, daddys_home
# Tuned on 88 real speaker->room->mic recordings (48 phrases, 40 negatives): 42/48 detected, 0/40 false triggers.
THRESH = 0.05
BJ, BR = 2.0, 2.0                       # boosts: Jarvis phrases / Rose phrases
PHRASES = {   # name -> (spoken variants, boost, threshold)
    "hey_jarvis":    (["HEY JARVIS"], BJ, THRESH),
    "wakeup_jarvis": (["WAKE UP JARVIS"], BJ, THRESH),
    "jarvis_awake":  (["JARVIS YOU AWAKE", "JARVIS ARE YOU AWAKE"], BJ, THRESH),
    "hey_rose":      (["HEY ROSE", "HEY ROSES", "HEY ROWS"], BR, THRESH),
    "wakeup_rose":   (["WAKE UP ROSE", "WAKE UP ROSES", "WAKE UP ROWS"], BR, THRESH),
    "rose_awake":    (["ROSE YOU AWAKE", "ROSE ARE YOU AWAKE", "ROWS YOU AWAKE"], BR, THRESH),
    "daddys_home":   (["DADDYS HOME", "DADDY'S HOME", "DADDY IS HOME", "DAD'S HOME"], BJ, THRESH),
}


def kind_of(name):
    return {"hey": "hey", "wakeup": "wakeup", "daddys": "home"}.get(name.split("_")[0]) or ("awake" if name.endswith("_awake") else None)


OWNER = {"hey_jarvis": "jarvis", "wakeup_jarvis": "jarvis", "jarvis_awake": "jarvis", "hey_rose": "rose", "wakeup_rose": "rose", "rose_awake": "rose"}


def all_phrases(shared=()):
    """The wake phrases of every persona (the built-in Jarvis and Rose use the tuned ones above; the others get "HEY <NAME>", "WAKE UP <NAME>", "<NAME> ARE YOU AWAKE"),
    plus a few shared ones you choose (any persona answers). Only the ACTIVE persona's phrases (and the shared ones) wake it: see persona_of()."""
    import re
    from . import personas
    out = dict(PHRASES)
    for pid, p in personas.PERSONAS.items():
        if pid in ("jarvis", "rose"):
            continue
        w = re.sub(r"[^A-Z ]", "", p["name"].upper()).strip()
        out[f"hey_{pid}"] = ([f"HEY {w}"], 2.0, THRESH)
        out[f"wakeup_{pid}"] = ([f"WAKE UP {w}"], 2.0, THRESH)
        out[f"{pid}_awake"] = ([f"{w} ARE YOU AWAKE"], 2.0, THRESH)
        OWNER.update({f"hey_{pid}": pid, f"wakeup_{pid}": pid, f"{pid}_awake": pid})
    for i, ph in enumerate(shared or ()):
        t = re.sub(r"[^A-Z' ]", "", str(ph).upper()).strip()
        if 3 <= len(t) <= 30:
            out[f"hey_shared{i}"] = ([t], 2.0, THRESH)                     # kind "hey": it wakes whichever persona is active
    return out


def persona_of(name):
    return OWNER.get(name)                                  # None = shared (daddy's home, your own shared phrases): any persona answers


def _shared():
    from . import settings
    v = settings.get("wake_shared")
    return v if isinstance(v, list) else []


class Spotter:
    def __init__(self, phrases=None, threads=1):
        import sentencepiece as spm
        import sherpa_onnx
        phrases = phrases or all_phrases(_shared())
        sp = spm.SentencePieceProcessor(model_file=str(MODEL / "bpe.model"))
        lines = []
        for name, (texts, boost, thr) in phrases.items():
            for text in texts:
                lines.append(" ".join(sp.encode(text, out_type=str)) + f" :{boost} #{thr} @{name}")
        fd, self._kw_path = tempfile.mkstemp(suffix=".txt", prefix="jarvis-kw-")
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines) + "\n")
        tag = "epoch-12-avg-2-chunk-16-left-64.int8.onnx"
        self.spotter = sherpa_onnx.KeywordSpotter(
            tokens=str(MODEL / "tokens.txt"), encoder=str(MODEL / f"encoder-{tag}"), decoder=str(MODEL / f"decoder-{tag}"),
            joiner=str(MODEL / f"joiner-{tag}"), keywords_file=self._kw_path, num_threads=threads,
            keywords_score=1.0, keywords_threshold=0.25, num_trailing_blanks=1)
        try:
            os.unlink(self._kw_path)                      # the spotter has read it: do not leave one file in /tmp per start
        except OSError:
            pass
        self.stream = self.spotter.create_stream()

    def feed(self, samples):
        """samples: int16 or float32 array (16 kHz mono). Returns the names of keywords that fired."""
        x = samples.astype(np.float32) / 32768.0 if samples.dtype == np.int16 else samples
        self.stream.accept_waveform(RATE, x)
        fired = []
        while self.spotter.is_ready(self.stream):
            self.spotter.decode_stream(self.stream)
            r = self.spotter.get_result(self.stream)
            if r:
                fired.append(r.strip().lstrip("@"))
                self.spotter.reset_stream(self.stream)
        return fired

    def reset(self):
        self.spotter.reset_stream(self.stream)
