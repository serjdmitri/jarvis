import os
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
    "image {type,title,src} where src is an https URL or an absolute local file path. "
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
