"""Microphone self-test and clipping detection. analyze() reads audio levels (from the always-on ring buffer, so no second recording is needed); fix() lowers the ALSA 'Internal Mic Boost' one step
when the signal clips (the cause of "Jarvis doesn't hear me correctly" earlier: boost at 30 dB, half the samples clipped)."""
import re
import subprocess

import numpy as np


def analyze(pcm):
    """pcm: int16 numpy array (mono). -> {"rms","peak","clipped_pct","verdict","advice"}."""
    a = np.asarray(pcm, dtype=np.float32)
    if a.size < 1600:
        return {"rms": 0, "peak": 0, "clipped_pct": 0.0, "verdict": "nodata", "advice": "I could not read the microphone."}
    rms, peak = float(np.sqrt((a ** 2).mean())), float(np.abs(a).max())
    clipped = float((np.abs(a) >= 32000).mean() * 100)
    if peak < 60:
        v, adv = "silent", "The microphone is silent: check it is not muted and the right input is selected."
    elif clipped > 5:
        v, adv = "clipping", f"The signal is clipping ({clipped:.0f}% of samples), which makes speech hard to recognise. Say: fix the mic."
    elif rms < 80 and peak < 3000:
        v, adv = "quiet", "The microphone is very quiet. Move closer or raise the input volume."
    else:
        v, adv = "ok", "The microphone level looks healthy."
    return {"rms": round(rms), "peak": round(peak), "clipped_pct": round(clipped, 1), "verdict": v, "advice": adv}


def ring_pcm(assistant, seconds=5):
    hf = getattr(assistant, "hf", None)
    if hf is None or not getattr(hf, "ring", None):
        return None
    frames = [f for (_t, f) in list(hf.ring)][-int(seconds * 50):]
    return np.concatenate(frames) if frames else None


def boost():
    try:
        out = subprocess.run(["amixer", "-c", "0", "sget", "Internal Mic Boost"], capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"Front Left: (\d+)", out)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def fix():
    """One step down on the mic boost when it is above 0. -> plain sentence."""
    b = boost()
    if b is None:
        return "I could not reach the mixer, so lower the microphone volume by hand."
    if b <= 0:
        return "The mic boost is already at its lowest. Lower the microphone volume instead."
    subprocess.run(["amixer", "-c", "0", "sset", "Internal Mic Boost", str(b - 1)], capture_output=True, timeout=3)
    return f"I lowered the microphone boost one step, from {b} to {b - 1}. Try talking again, or say: test the microphone."


def parse_command(text):
    low = text.lower().strip(" .?!")
    if re.fullmatch(r"(?:please )?(?:test|check) (?:the |my )?(?:mic|microphone)|why can'?t you hear me|(?:are you|can you) (?:hear|hearing) me(?: ok(?:ay)?)?|is the mic (?:working|ok|okay)", low):
        return {"kind": "test"}
    if re.fullmatch(r"(?:please )?(?:fix|repair|adjust) (?:the |my )?(?:mic|microphone)(?: level| boost)?|(?:the )?mic is too loud", low):
        return {"kind": "fix"}
    return None
