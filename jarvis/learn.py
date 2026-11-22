"""Learning helper: flashcards and quizzes on any topic, with next / back buttons, a record of what you got wrong, and the wrong ones brought back later.
The brain writes the cards as JSON; this file validates and clamps them (at most 12 cards, short text) and keeps the deck, your progress and the missed cards in data/learn.json (outside git,
mode 600, viewable and deletable: "forget my flashcards"). "Explain it simply", "quiz me on X", "make flashcards about X", "quiz me on what I got wrong"."""
import json
import os
import re
import time

from . import config, webjson

FILE = config.DATA / "learn.json"
DECK = {"topic": "", "cards": [], "i": 0, "t": 0.0}


def _load():
    try:
        return json.loads(FILE.read_text())
    except (OSError, ValueError):
        return {"wrong": []}


def _save(d):
    config.DATA.mkdir(parents=True, exist_ok=True)
    fd = os.open(FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f)


def clean(cards):
    out = []
    for c in cards if isinstance(cards, list) else []:
        if isinstance(c, dict) and isinstance(c.get("q"), str) and isinstance(c.get("a"), str) and c["q"].strip() and c["a"].strip():
            out.append({"q": re.sub(r"\s+", " ", c["q"]).strip()[:220], "a": re.sub(r"\s+", " ", c["a"]).strip()[:320]})
    return out[:12]


def make(topic, n=6, level=""):
    """-> cards or []. Asks the brain; clamps the answer."""
    prompt = (f"Make {n} flashcards to learn: {topic[:120]}. {('Level: ' + level + '. ') if level else ''}Short, clear, one fact each. "
              'Answer with a JSON list of {"q": question, "a": answer} and nothing else.')
    return clean(webjson.ask_json(prompt))


def start(topic, cards):
    DECK.update(topic=topic[:80], cards=cards, i=0, t=time.time())
    return card()


def wrong_deck():
    return _load().get("wrong", [])[:12]


def record(ok):
    """Mark the current card right or wrong. A wrong one is remembered (and a right one forgotten from the wrong list)."""
    if not DECK["cards"]:
        return
    c = DECK["cards"][DECK["i"]]
    d = _load()
    w = [x for x in d.get("wrong", []) if x["q"] != c["q"]]
    if not ok:
        w.append(dict(c, topic=DECK["topic"], t=time.time()))
    d["wrong"] = w[-60:]
    _save(d)


def move(delta):
    if DECK["cards"]:
        DECK["i"] = max(0, min(len(DECK["cards"]) - 1, DECK["i"] + delta))
    return card()


def card():
    if not DECK["cards"]:
        return None
    c = DECK["cards"][DECK["i"]]
    return {"type": "flash", "gid": "flash", "wide": 380, "small": True, "title": "STUDY · " + (DECK["topic"] or "cards").upper()[:30], "q": c["q"], "a": c["a"], "i": DECK["i"] + 1, "n": len(DECK["cards"]),
            "wrong": len(_load().get("wrong", []))}


def forget():
    try:
        FILE.unlink()
    except OSError:
        pass
    DECK.update(topic="", cards=[], i=0)


def parse_command(text):
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    if re.fullmatch(r"(?:please )?quiz me on (?:what i got wrong|my mistakes|the ones i missed|the wrong ones)", low):
        return {"kind": "wrong"}
    m = re.fullmatch(r"(?:please )?(?:quiz me (?:on|about)|test me on|make (?:me )?(?:some )?(?:flash ?cards?|a quiz|a study deck) (?:about|on|for)|help me (?:learn|study|revise)|i(?:'m| am) (?:learning|studying)|flash ?cards? (?:about|on|for)) (.+)", low)
    if m and len(m.group(1)) > 2:
        return {"kind": "make", "topic": m.group(1)}
    if re.fullmatch(r"(?:please )?(?:next|next card|next question)", low) and DECK["cards"] and time.time() - DECK["t"] < 1800:
        return {"kind": "next"}
    if re.fullmatch(r"(?:please )?(?:previous|back|previous card|go back)", low) and DECK["cards"] and time.time() - DECK["t"] < 1800:
        return {"kind": "prev"}
    if re.fullmatch(r"(?:please )?(?:forget|delete|clear) my (?:flash ?cards?|study (?:history|deck)|mistakes)", low):
        return {"kind": "forget"}
    return None
