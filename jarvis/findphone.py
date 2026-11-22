"""Find my phone, honestly. There is NO official way for a program to ring an iPhone: that needs Apple's Find My and your Apple ID, which Jarvis never stores. What is safe and works:
 (1) a LOUD, urgent push notification through ntfy (priority "urgent"): it makes the phone buzz and show a banner if the phone is online, the ntfy app is allowed to notify, and Silent mode
     or a Focus is not blocking it (iOS may stay silent; ntfy cannot override the silent switch);
 (2) a card with the one tap way to the real ring: icloud.com/find in the browser, or the Find My app on the Mac.
A Pushover-style emergency priority (breaks through silent) would need Pushover (a paid app, one time cost per platform): not set up, it is only described here."""
import re

from . import phone


def parse_command(text):
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    if re.fullmatch(r"(?:please )?(?:find|ring|locate|call|buzz|where(?:'s| is)|i lost|i can't find|i cannot find|help me find) (?:my |the )?(?:iphone|phone|cell|mobile)(?: for me)?(?: please)?|where(?:'s| is) my (?:iphone|phone)|i (?:lost|misplaced) my (?:iphone|phone)", low):
        return {"kind": "find"}
    return None


def ring():
    """-> {"ok": bool, "message": str} (the notification is sent now; nothing about the phone's whereabouts is known)."""
    return phone.send({"title": "Find my phone", "message": "Jarvis is looking for this phone. Tap to dismiss.", "priority": "urgent"})


def card(sent_ok):
    steps = [{"n": 1, "text": ("I sent a loud notification to your phone." if sent_ok else "I could not send the notification (is the phone alert topic set up?).") + " It buzzes only if the phone is online, the ntfy app may notify you, and Silent mode or a Focus is not blocking it. iOS can stay silent: that cannot be overridden from here."},
             {"n": 2, "text": "For the real ring: open icloud.com/find on any computer and press Play Sound, or use the Find My app on your Mac. That needs your Apple ID, which I never keep.",
              "buttons": [{"label": "OPEN ICLOUD FIND", "action": "open_link", "payload": {"url": "https://www.icloud.com/find"}}]},
             {"n": 3, "text": "A phone alert that breaks through Silent (Pushover emergency priority) exists but costs money and is not set up. Tell me if you want it."}]
    return {"type": "guide", "gid": "findphone", "title": "FIND MY PHONE", "steps": steps, "paged": False, "status": "notification sent" if sent_ok else "not sent"}
