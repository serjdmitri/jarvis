"""Read this to me: a screenshot of what is on screen, read by the local OCR (tesseract) when it finds real text, otherwise by the brain looking at the picture.
tesseract runs with fixed arguments on a file we just made; nothing from the model is ever run."""
import re
import subprocess

MIN_CHARS = 25


def ocr(path):
    """-> cleaned text ("" if nothing readable). Local only."""
    try:
        out = subprocess.run(["tesseract", path, "stdout", "-l", "eng", "--psm", "3"], capture_output=True, text=True, timeout=25).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in out.splitlines()]
    lines = [ln for ln in lines if len(re.findall(r"[A-Za-z0-9]", ln)) >= 3]          # drop OCR noise lines
    text = "\n".join(lines)
    return text if len(re.findall(r"[A-Za-z]{3,}", text)) >= 4 and len(text) >= MIN_CHARS else ""


def parse_command(text):
    low = text.lower().strip(" .?!")
    if re.fullmatch(r"(?:please )?read (?:this|that|it|the screen|what(?:'s| is) on (?:the )?screen|this screen)(?: out| out loud| aloud)?(?: to me)?|(?:please )?read (?:me )?(?:this|the screen)|what does (?:this|that|the screen) say|read the text(?: on (?:the )?screen)?(?: to me)?", low):
        return {"speech": "Looking at the screen.", "readscreen": True}
    return None
