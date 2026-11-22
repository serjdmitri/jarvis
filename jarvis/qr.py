"""Show a QR code for the link that is open (a page, a video, a map, a route) or for a link you say. qrencode runs with fixed arguments on a validated https URL;
the PNG goes into shots/ and is shown as an image card (tap it to make it full screen)."""
import re
import subprocess
import time

from . import config, opener

QR_FILE = re.compile(r"qr-\d+\.png")


def make(url):
    """-> path of a PNG, or None. Only https URLs (opener.clean_url rules) are encoded."""
    u = opener.clean_url(url or "")
    if not u or not u.startswith("https://"):
        return None
    config.SHOTS_DIR.mkdir(exist_ok=True)
    for f in sorted(config.SHOTS_DIR.glob("qr-*.png"))[:-4]:
        f.unlink()
    path = config.SHOTS_DIR / f"qr-{int(time.time() * 1000)}.png"
    try:
        subprocess.run(["qrencode", "-t", "PNG", "-s", "12", "-m", "3", "-o", str(path), u], check=True, timeout=8, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return str(path)


def parse_command(text):
    low = text.lower().strip(" .?!")
    m = re.fullmatch(r"(?:please )?(?:show|make|give me|display|generate)(?: me)? (?:a )?qr(?: code)?(?: for| of| to)? (?:the (?:link|address|website|site) )?(https?://\S+|(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?:/\S*)?)", re.sub(r"\s+dot\s+", ".", low))
    if m:                                                           # a link you say: "make a qr code for example dot org"
        u = m.group(1)
        return {"kind": "link", "url": u if u.startswith("http") else "https://" + u}
    if re.fullmatch(r"(?:please )?(?:show|make|give me|display|generate)(?: me)? (?:a )?qr(?: code)?(?: for| of)?(?: this| that| it| the (?:link|page|map|video|route))?|(?:please )?qr code(?: for this| for that)?", low):
        return {"kind": "qr"}
    return None
