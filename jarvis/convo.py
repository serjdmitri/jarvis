"""Conversation mode helpers: is the assistant asking a question, and is the user ending the conversation?"""
import re

# Whole-utterance sign-offs ("no", "no thanks", "that's all", "thanks", "I'm good", "nothing", "goodnight", "stop", ...).
_SIGNOFF = re.compile(
    r"^(?:(?:no|nope|nah|naw)(?: thanks| thank you| that'?s (?:all|it)| i'?m (?:good|fine|ok|okay))?|"
    r"that'?s (?:all|it|everything|enough)|that is (?:all|it|everything|enough)|that will be all|"
    r"(?:thanks|thank you|cheers)(?: so much| very much| a lot| mate)?|"
    r"i'?m (?:good|fine|ok|okay|done|all set)|all (?:good|set)|"
    r"nothing(?: else| more| for now| right now)?|not (?:now|right now|today)|"
    r"good ?night|good ?bye|bye(?: bye)?|see you(?: later| soon)?|"
    r"stop|(?:you can |please )?stop listening|never ?mind|cancel|enough|done|no more|that'?s fine)"
    r"(?: thanks| thank you| please)?$")
# short, ambiguous things worth one model call ("yeah no", "I think that's okay") rather than a guess
_MAYBE = re.compile(r"\b(no|nope|nothing|thanks|thank|good|fine|okay|ok|done|enough|later|bye|goodbye|cancel|stop|all)\b")


def norm(text):
    t = text.lower().replace("’", "'")
    t = re.sub(r"[^a-z' ]+", " ", t)
    t = re.sub(r"^(?:hey |ok |okay |well |um |uh |erm |yeah |yes |so )+", "", re.sub(r"\s+", " ", t).strip())
    t = re.sub(r"\s+(?:jarvis|rose)$", "", t)
    return t.strip()


_NOISE = re.compile(r"^(?:you|uh|um|hmm+|huh|oh|ah|the end|thanks? for watching|thank you for watching|please subscribe|subtitles by.*|www\..*|music|applause|laughter|\.+)$")


def is_noise(text):
    """What a speech recogniser makes up from a TV, a cough or room noise: never a command."""
    n = re.sub(r"[^a-z. ]+", " ", (text or "").lower()).strip()
    return not n or bool(_NOISE.match(re.sub(r"\s+", " ", n)))


def is_signoff(text):
    return bool(_SIGNOFF.match(norm(text)))


def maybe_signoff(text):
    """Short and signoff-flavoured but not a clean match: ask the model once instead of guessing."""
    n = norm(text)
    return 0 < len(n.split()) <= 4 and bool(_MAYBE.search(n)) and not is_signoff(text)


def is_question(text):
    """The assistant is asking the user something: its last sentence ends with a question mark."""
    t = (text or "").strip()
    if t.endswith("?"):
        return True
    last = re.split(r"(?<=[.!])\s+", t)[-1].lower() if t else ""
    # a request for a missing detail is a question too, even without a question mark: "give it a name or place", "tell me which one", "I need the address"
    return bool(re.search(r"\b(?:give|tell|say|name|pick|choose|type|spell)\b.*\b(?:name|place|address|town|city|number|one|which|where|what|when|who)\b|\bi (?:need|'ll need|would need) (?:a|an|the|to know)\b|\b(?:which|what) (?:place|one|town|city|address|day|time)\b.*(?:you|do)", last))
