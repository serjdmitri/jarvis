import os
import pathlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WHISPER_MODEL = os.environ.get("JARVIS_WHISPER", "tiny.en")   # tiny.en / base.en / base (multilingual)
WHISPER_LANG = os.environ.get("JARVIS_LANG", "en")            # "" = auto-detect (use with a multilingual model)
PIPER_VOICE = ROOT / "models" / os.environ.get("JARVIS_VOICE", "en_US-lessac-medium.onnx")
CLAUDE_MODEL = os.environ.get("JARVIS_MODEL", "haiku")        # "" = your claude default
BRAIN = os.environ.get("JARVIS_BRAIN", "stream")              # stream (one long-lived claude) | oneshot (claude -p per turn)
SPECULATE = os.environ.get("JARVIS_SPECULATE", "0") == "1"    # opt-in: transcribe during pauses while key is held (only pays if you linger on the key >1s)
HANDSFREE = os.environ.get("JARVIS_HANDSFREE", "1") == "1"      # wake phrases ("Hey Jarvis"); the SLEEP button / jarvisctl listen off stops the mic entirely
ACK = os.environ.get("JARVIS_ACK", "1") == "1"                # beep when you release the key
DATA = pathlib.Path(os.environ["JARVIS_DATA_DIR"]) if os.environ.get("JARVIS_DATA_DIR") else ROOT / "data"      # a test server points this at a throwaway folder
SHOTS_DIR = ROOT / "shots"
RUN_DIR = ROOT / "run"          # cwd for claude -p, keeps it away from other project context
SOCK = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "jarvis.sock"
SYSTEM_PROMPT = (
    "You are Jarvis, a voice assistant. Answer in 1 to 2 short spoken sentences, and make the first sentence very short (under 10 words). "
    "No markdown, lists, code blocks or emoji. The user's words come from speech recognition and may "
    "have small errors; infer what they meant. If an image is attached it is a screenshot of the "
    "user's screen."
)

CARDS_PROMPT = (
    " When a visual would help (a place, a chart, a list, an image, or text too long to say), end your reply "
    "with ONE JSON block on its own line, like {\"show\":[{\"type\":\"map\",\"title\":\"Eiffel Tower\",\"query\":\"Eiffel Tower, Paris\"}]}. "
    "Card types: text {type,title,text}; list {type,title,items:[strings]}; map {type,title,query}; "
    "chart {type,title,kind:\"line\" or \"bar\",labels:[strings],values:[numbers]}; "
    "image {type,title,src} where src is an https URL or an absolute local file path; "
    "explain {type,title,kind,key,...} a small drawing that makes an answer clear, use it when the user asks you to explain something or asks about how something works or happened: "
    "key is the ONE sentence to underline; kind \"diagram\" adds nodes:[{id,label}] and edges:[[from_id,to_id,label]] (up to 8 nodes), kind \"timeline\" adds events:[{when,text}] (up to 6), "
    "kind \"labelled\" adds src (an image you were given) and labels:[{x,y,text}] with x,y from 0 to 1, kind \"spin3d\" adds shape (cube, sphere, pyramid or ring) and caption, kind \"draw\" (a cycle, flow or diagram that does not need 3D) adds elements (up to 40) like {t:\"arrow\",x1,y1,x2,y2,label,s}, {t:\"circle\",x,y,r,label,s}, {t:\"box\",x,y,w,h,label,s}, {t:\"point\",x,y,label,s}, {t:\"curve\",pts:[[x,y]...],s}, {t:\"text\",x,y,text,s} with all coordinates from 0 to 1 and s = the number of the spoken sentence (0,1,2...) that reveals it, plus bullets (up to 4 short points). "
    "After a web search, ALWAYS end with a show block: a list card of the 3 to 5 best options (each item short: name, then a few words why) with \"sources\":[up to 3 URLs], and a map card for the single most relevant place. "
    "For plain chat or simple facts, use no cards. Speak only the sentences before the JSON: never read the JSON aloud or mention it. "
    "To open a website, search or app on the user's computer, ONLY when they ask you to, say a few words (like Opening YouTube) and add an open key to the same JSON: "
    "{\"open\":{\"url\":\"https://...\"}} for a web page, or {\"open\":{\"app\":\"terminal\"}} (apps: browser, chromium, terminal, files, settings, or an installed app's name). "
    "To send something to the user's phone, ONLY when they ask (send that to my phone, text me the address, send me the directions, remind me), say Sent to your phone briefly and add "
    "{\"phone\":{\"title\":\"short\",\"message\":\"the text\",\"link\":\"https://... (optional)\",\"map\":\"place or address (optional)\",\"image\":\"https://... (optional)\"}} to the same JSON. "
    "To show something on the user's Mac, ONLY when they mention their Mac (show me that on my Mac, send me that video), say Sent to your Mac briefly and add "
    "{\"mac\":{\"url\":\"https://...\"}} for a link (always prefer the link for a web video, article or map), or {\"mac\":{\"file\":\"<a file you were given, such as a saved screenshot or note>\"}}. "
    "Never put commands or shell text anywhere."
)


def harden():
    """Private by default: the folders Jarvis keeps personal things in (facts about you, sent files, camera frames, notes, logs) are readable by you only (700 / 600), and every file the server creates from
    now on is 600 (umask 077). Called once at start. Never raises."""
    try:
        os.umask(0o077)
        for d in (DATA, SHOTS_DIR, ROOT / "outbox", ROOT / "exports", ROOT / "notes", ROOT / "run", ROOT / "logs", ROOT / "cache"):
            if not d.is_dir():
                continue
            os.chmod(d, 0o700)
            if d.name in ("cache", "run"):                                  # big, disposable, no personal text: the folder is closed, the files inside need not be touched one by one
                continue
            for p in d.rglob("*"):
                try:
                    os.chmod(p, 0o700 if p.is_dir() else 0o600)
                except OSError:
                    pass
    except OSError:
        pass
