"""Moods: the brain tags each spoken sentence with how it should sound, like "[happy] It's sunny." The tag never reaches the speaker or the screen text.

* Stream.feed() turns tags in the streamed text into a one-letter invisible marker (so a tag split across two chunks still works and nothing slow happens).
* take() pulls the mood back off a sentence when it is queued to speak; strip() removes tags/markers from any text that is shown.
* Each mood nudges the Piper voice a little (speed within +-10 %, pitch within +-5 %) and the orb (hue, pulse, spin) on the page."""
import re

import numpy as np

MOODS = ("calm", "happy", "excited", "thinking", "sorry", "serious", "playful")
CODE = {"calm": "c", "happy": "h", "excited": "e", "thinking": "t", "sorry": "s", "serious": "r", "playful": "p"}
NAME = {v: k for k, v in CODE.items()}
TAG = re.compile(r"\[\s*(" + "|".join(MOODS) + r")\s*\]\s*", re.I)
MARK = re.compile("⁣([a-z])")
PARTIAL = re.compile(r"\[[a-z ]{0,9}$", re.I)          # "[hap" at the end of a chunk: wait for the rest
# mood -> (speed multiplier, pitch multiplier): subtle on purpose
VOICE = {"calm": (0.97, 0.99), "happy": (1.04, 1.03), "excited": (1.10, 1.05), "thinking": (0.95, 0.98), "sorry": (0.93, 0.97), "serious": (0.95, 0.96), "playful": (1.05, 1.04)}
# mood -> what the orb does: hue shift in degrees (positive = warmer), pulse amplitude, spin speed (multipliers)
ORB = {"calm": (0, 0.9, 0.9), "happy": (14, 1.05, 1.05), "excited": (26, 1.3, 1.35), "thinking": (-14, 0.95, 1.1), "sorry": (-22, 0.8, 0.75), "serious": (-6, 0.85, 0.85), "playful": (34, 1.15, 1.2)}

PROMPT = (" Start every spoken sentence with one mood tag in square brackets, [calm] [happy] [excited] [thinking] [sorry] [serious] or [playful], matching how that sentence should sound "
          "([calm] by default). The tag is removed before speaking, so never mention it and never put it inside a JSON block.")


class Stream:
    """Stateful: feed each streamed chunk, get the chunk back with tags replaced by markers."""

    def __init__(self):
        self.tail = ""

    def feed(self, d):
        s = self.tail + d
        self.tail = ""
        m = PARTIAL.search(s)
        if m:
            self.tail, s = s[m.start():], s[: m.start()]
        return TAG.sub(lambda t: "⁣" + CODE[t.group(1).lower()], s)

    def flush(self):
        t, self.tail = self.tail, ""
        return TAG.sub("", t)


def take(text):
    """-> (clean text, mood or None) for one queued sentence."""
    mood = None
    for m in MARK.finditer(text):
        mood = NAME.get(m.group(1), mood)
    return strip(text), mood


def strip(text):
    """Remove mood tags and markers from any text."""
    return TAG.sub("", MARK.sub("", text or "")).strip()


_SORRY = re.compile(r"\b(sorry|couldn't|could not|can't|cannot|unable|didn't catch|no such|not found|failed)\b", re.I)


def guess(text):
    """A mood for a sentence that has no tag (local answers): sorry for refusals, happy for exclamations, else None (neutral)."""
    if _SORRY.search(text):
        return "sorry"
    if text.rstrip().endswith("!"):
        return "happy"
    return None


def retune(samples, rate, mood):
    """Pitch shift by resampling (the caller already slowed/sped synthesis to compensate, so only the pitch changes). samples: int16 array."""
    p = VOICE.get(mood, (1, 1))[1]
    if abs(p - 1) < 1e-3:
        return samples
    n = int(len(samples) / p)
    return np.interp(np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples.astype(np.float32)).astype(np.int16)
