"""Text messages with approval. "text Mom I'm on my way" makes a DRAFT on screen and asks for "send it" (spoken) or a tap on SEND, in that same turn (60 seconds). Jarvis cannot send an iMessage or SMS
itself and does not know phone numbers: "send it" pushes a notification to your iPhone whose tap opens Messages with the words already typed (a plain sms: link); you pick the person and press send.
Nothing is ever sent without your confirmation in that turn. A "trusted contacts" setting (default OFF) skips the confirmation only for names you list: it still only prepares the compose
notification. The brain never sends; only this code does, and only after the confirmation."""
import re
import time

from . import settings

PENDING = {"draft": None}
CONFIRM_S = 60


def parse_command(text):
    low = re.sub(r"\s+", " ", text.strip(" .?!"))
    m = re.fullmatch(r"(?i)(?:please )?(?:text|message|send a (?:text|message) to|tell) (?:my )?([A-Za-z][\w' -]{0,24}?)[,:]? (?:that |and say |saying |to say )?(.{2,300})", low)
    if m and not re.fullmatch(r"(?i)(?:me|myself|you|jarvis|it|this|that)", m.group(1)):
        return {"kind": "draft", "to": m.group(1).strip().title(), "body": m.group(2).strip()}
    if re.fullmatch(r"(?i)(?:please )?(?:send it|yes,? send it|yes send|go ahead and send|confirm)", low) and PENDING["draft"] and time.time() - PENDING["draft"]["t"] < CONFIRM_S:
        return {"kind": "confirm"}
    if re.fullmatch(r"(?i)(?:please )?(?:cancel|don'?t send(?: it)?|never ?mind|scrap it|no)", low) and PENDING["draft"]:
        return {"kind": "cancel"}
    return None


def trusted(name):
    return name.lower() in [str(x).lower() for x in (settings.get("trusted_contacts") or [])] and settings.get("trusted_auto_send") is True


def make_draft(to, body):
    PENDING["draft"] = {"to": to, "body": body[:300], "t": time.time()}
    return PENDING["draft"]


def take_confirmed():
    """-> the draft when a valid, fresh one exists (and clears it: one confirmation sends one message), else None."""
    d = PENDING["draft"]
    PENDING["draft"] = None
    return d if d and time.time() - d["t"] < CONFIRM_S else None


def clear():
    PENDING["draft"] = None


def card(d):
    return {"type": "guide", "gid": "textdraft", "title": f"TEXT TO {d['to'].upper()[:20]}", "paged": False, "status": "not sent: say send it, or tap SEND within a minute", "steps": [
        {"n": 1, "text": d["body"], "buttons": [{"label": "SEND", "action": "text_send", "payload": {}}, {"label": "CANCEL", "action": "text_cancel", "payload": {}}]}]}
