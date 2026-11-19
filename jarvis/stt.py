"""Local speech-to-text: faster-whisper, int8 on CPU, kept loaded."""
import threading
import wave

import numpy as np

from . import config

_model = None
_lock = threading.Lock()   # one transcription at a time (speculative + final may overlap)


def _get():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8", cpu_threads=4)
    return _model


def _to_float(src):
    if isinstance(src, str):
        with wave.open(src) as w:
            src = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if src.dtype == np.int16:
        src = src.astype(np.float32) / 32768.0
    return src


def transcribe(src, prompt=None):
    """src: path to a 16 kHz mono WAV, or an int16/float32 numpy array. prompt: biases spelling (names)."""
    audio = _to_float(src)
    with _lock:
        segs, _ = _get().transcribe(
            audio, language=config.WHISPER_LANG or None, beam_size=1, without_timestamps=True,
            vad_filter=True, condition_on_previous_text=False, initial_prompt=prompt,
        )
        return " ".join(s.text.strip() for s in segs).strip()


def warm():
    _get()
    transcribe(np.zeros(16000, np.int16))
