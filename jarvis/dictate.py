"""Hold-to-dictate: hold SUPER+SHIFT+D (or hold the on-screen mic button), speak, release, and the words are typed into whatever window has focus.
Same warm faster-whisper as everything else; typing is done with wtype (Wayland) as fixed argument lists, never through a shell and never through the clipboard.
Dictation does not wake Jarvis and gets no reply: while it runs the assistant is in the 'recording' state, which the wake-word paths ignore.
Spoken commands: "new line" (a line break: Shift+Enter), "new paragraph" (two), "press enter" (Return). Enter is NEVER pressed unless you say so.
Punctuation words: comma, period / full stop, question mark, exclamation mark / point, colon, semicolon. Sentences start with a capital letter and a lone "i" becomes "I"."""
import os
import re
import subprocess
import threading
import time

from . import audio, settings, stt

MAX_CHARS = 3000
LIVE_EVERY_S = 1.3
PROMPT = "Dictation into a terminal. Names and commands: Claude Code, git, sudo, Hyprland, Jarvis, Python, npm."

_P = r"[,.;:!?]*"                                                # whatever punctuation the recogniser put next to a spoken command or punctuation word is dropped
_PUNCT = [(_P + r"\s*\bquestion mark\b" + _P, "?"), (_P + r"\s*\bexclamation (?:mark|point)\b" + _P, "!"), (_P + r"\s*\bfull stop\b" + _P, "."),
          (_P + r"\s*\bperiod\b" + _P, "."), (_P + r"\s*\bsemicolon\b" + _P, ";"), (_P + r"\s*\bcolon\b" + _P, ":"), (_P + r"\s*\bcomma\b" + _P, ",")]
# tiny.en often hears "new line" as "new lying" / "new lion"; the command phrases are matched loosely
_CMD = re.compile(_P + r"\s*\b(new paragraph|new line|new lines|new lying|new lion|newline|press enter|hit enter|press return|press the enter key)\b" + _P, re.I)
_GIT = re.compile(r"\bit (status|commit|push|pull|diff|log|add|checkout|branch|clone|stash|rebase|merge)\b", re.I)


def format_text(raw):
    """Spoken text -> a list of actions: ("text", str) | ("nl",) | ("enter",). Pure function (unit-tested)."""
    s = _GIT.sub(lambda m: "git " + m.group(1), re.sub(r"\s+", " ", raw or "").strip())
    out = []
    pos = 0
    for m in _CMD.finditer(s):
        seg = s[pos:m.start()]
        if seg.strip():
            out.append(("text", seg.strip()))
        w = m.group(1).lower()
        if w in ("press enter", "hit enter", "press return", "press the enter key"):
            out.append(("enter",))
        elif w == "new paragraph":
            out += [("nl",), ("nl",)]
        else:
            out.append(("nl",))
        pos = m.end()
    tail = s[pos:]
    if tail.strip():
        out.append(("text", tail.strip()))
    flat = out
    res, cap = [], True                                        # capitalise the start and after sentence ends / line breaks
    for a in flat:
        if a[0] != "text":
            res.append(a)
            cap = True
            continue
        t = a[1]
        for pat, rep in _PUNCT:
            t = re.sub(r"\s*" + pat + r"\s*", rep + " ", t, flags=re.I)
        t = re.sub(r"\s+([,.;:?!])", r"\1", t)
        t = re.sub(r"([,.;:?!])(?:\s*[,.;:?!])+", r"\1", t)            # never doubled punctuation
        t = re.sub(r"\bi\b", "I", t).strip()
        t = re.sub(r"([.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), t)
        if cap and t:
            t = t[0].upper() + t[1:]
        cap = bool(re.search(r"[.!?]$", t))
        res.append(("text", t))
    return res


def _clean(t):
    return "".join(ch for ch in t if ch == "\t" or ch >= " ")[:MAX_CHARS]


def type_actions(actions):
    """Type with wtype: fixed argument lists. Text segments end with a space unless a line break or Enter follows."""
    for i, a in enumerate(actions):
        if a[0] == "text":
            nxt = actions[i + 1][0] if i + 1 < len(actions) else None
            t = _clean(a[1]) + ("" if nxt in ("nl", "enter") else " ")
            subprocess.run(["wtype", "--", t], check=False, timeout=15)
        elif a[0] == "nl":
            subprocess.run(["wtype", "-M", "shift", "-k", "Return", "-m", "shift"], check=False, timeout=5)
        elif a[0] == "enter":
            subprocess.run(["wtype", "-k", "Return"], check=False, timeout=5)


class Dictation:
    def __init__(self, assistant, on_caption):
        self.a, self.on_caption = assistant, on_caption
        self.rec = None
        self.active = False
        self.t0 = 0.0

    def start(self):
        a = self.a
        if self.active or a.state not in ("idle", "speaking", "error") or getattr(a, "muted_hard", False):
            return False
        a.speaker.stop()
        if a.in_conv:
            a.conversation_end()
        a.turn += 1                                            # any pending turn is stale
        self.active, self.t0 = True, time.time()
        self.rec = audio.Recorder()
        self.rec.start()
        a._set("recording", "dictation")                      # not idle: the wake-word paths ignore the mic while this runs
        threading.Thread(target=self._live, daemon=True).start()
        return True

    def _live(self):
        """Every ~1.3 s transcribe what has been said so far, for the live caption."""
        last = ""
        while self.active:
            time.sleep(LIVE_EVERY_S)
            if not self.active or not self.rec:
                break
            snap = self.rec.snapshot()
            if len(snap) < 16000 * 0.7:
                continue
            try:
                txt = stt.transcribe(snap, prompt=PROMPT)
            except Exception:
                continue
            if txt and txt != last and self.active:
                last = txt
                self.on_caption(txt, True)

    def stop(self):
        if not self.active:
            return None
        self.active = False
        path = self.rec.stop() if self.rec else None
        self.rec = None
        return self._finish(path)

    def _finish(self, path):
        a = self.a
        text = ""
        try:
            if path and os.path.exists(path):
                text = stt.transcribe(path, prompt=PROMPT)
        finally:
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
        actions = format_text(text)
        if actions:
            self.on_caption(" ".join(x[1] if x[0] == "text" else ("↵" if x[0] == "enter" else "⏎") for x in actions), False)
            try:
                type_actions(actions)
            except Exception as e:
                print("[dictate] typing failed:", type(e).__name__, flush=True)
        else:
            self.on_caption("", False)
        a._set("idle")
        return text

    def from_file(self, path):
        """Test hook (jarvisctl dictate file <wav>): the same as releasing the key after speaking this recording."""
        if self.active or self.a.state not in ("idle", "speaking", "error"):
            return None
        self.a.speaker.stop()
        import shutil
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="jarvis-dict-")
        os.close(fd)
        shutil.copyfile(path, tmp)                              # _finish deletes its input
        self.a._set("recording", "dictation")
        return self._finish(tmp)


def set_micbtn(on):
    """Show / hide the on-screen dictation mic button (remembered across restarts)."""
    settings.update(micbtn=bool(on))
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.Popen([os.path.join(base, "bin", "jarvis-micbtn")] + ([] if on else ["stop"]), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def parse_command(text):
    low = text.lower().strip(" .?!")
    if re.search(r"\b(?:show|bring up|turn on|enable)\b.*\b(?:mic(?:rophone)? button|dictation button|dictate button)\b", low):
        return {"speech": "Mic button on. Hold it to dictate, or drag it where you like.", "micbtn": True}
    if re.search(r"\b(?:hide|remove|turn off|disable|get rid of)\b.*\b(?:mic(?:rophone)? button|dictation button|dictate button)\b", low):
        return {"speech": "Mic button hidden.", "micbtn": False}
    return None
