"""Auto persona by topic: a small editable table topic -> persona. Homework and studying go to Sage, fun and music to Captain, building and electronics to Maker, fitness and habits to Coach,
reading and research to Page. Settings: "auto_persona" (on), "say_switching" (on), and your own rows in "persona_topics" ({"coach": ["gym", "run"], ...}). A switch never happens twice
within 10 minutes, never while you named a persona yourself in the last 15 minutes, and happens after the current reply."""
import re
import time

from . import personas, settings

TOPICS = {
    "sage": ["homework", "study", "studying", "revise", "revision", "exam", "essay", "assignment", "algebra", "geometry", "calculus", "chemistry", "biology", "physics", "history homework"],
    "captain": ["tell me a joke", "joke", "riddle", "play a game", "i'm bored", "im bored", "fun fact", "movie", "tv show", "trivia"],
    "maker": ["raspberry pi", "arduino", "solder", "soldering", "wiring", "wire up", "circuit", "breadboard", "servo", "sensor", "robot", "3d print", "3d printer", "electronics", "esp32", "motor driver"],
    "coach": ["workout", "exercise", "push-ups", "pushups", "gym", "habit", "motivate me", "motivation", "stretch", "running"],
    "page": ["book", "novel", "library", "poem", "poetry", "reading list", "author"],
}
_state = {"last": 0.0, "named": 0.0}


def table():
    t = {k: list(v) for k, v in TOPICS.items()}
    mine = settings.get("persona_topics")
    if isinstance(mine, dict):
        for k, v in mine.items():
            if k in personas.PERSONAS and isinstance(v, list):
                t[k] = [str(x).lower()[:40] for x in v[:30]]
    return t


def named_by_user():
    _state["named"] = time.time()


def pick(text, current):
    """-> a persona id to switch to, or None."""
    if settings.get("auto_persona") is False:
        return None
    now = time.time()
    if now - _state["last"] < 600 or now - _state["named"] < 900:
        return None
    low = " " + re.sub(r"[^a-z0-9' -]", " ", text.lower()) + " "
    for pid, words in table().items():
        if pid in personas.PERSONAS and pid != current and any(f" {w} " in low or f" {w}s " in low for w in words):
            _state["last"] = now
            return pid
    return None
