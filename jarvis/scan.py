"""Barcode and QR scanner for the camera, with zbar (zbarimg, already installed: no download). A QR code's text is SHOWN, never opened or run; a link in it is an OPEN PAGE button you press.
A product barcode (EAN/UPC) can be looked up for stores. Nothing is stored."""
import re
import subprocess


def scan_codes(path):
    """-> [{"type": "QR-Code"|"EAN-13"|..., "data": str}] (at most 5), [] when there is none."""
    try:
        r = subprocess.run(["zbarimg", "-q", "--raw", "-Sdisable", "-Sqrcode.enable", "-Sean13.enable", "-Sean8.enable", "-Supca.enable", "-Supce.enable", "-Scode128.enable", "-Scode39.enable", "-Sdatabar.enable", path],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = [ln.strip()[:300] for ln in r.stdout.splitlines() if ln.strip()]
    res = []
    for d in out[:5]:
        res.append({"type": "barcode" if re.fullmatch(r"\d{8,14}", d) else "code", "data": d})
    return res


def card(codes):
    steps = []
    for i, c in enumerate(codes, 1):
        btn = []
        if re.fullmatch(r"https://\S+", c["data"]):
            btn = [{"label": "OPEN PAGE", "action": "open_link", "payload": {"url": c["data"]}}]
        steps.append({"n": i, "text": ("PRODUCT BARCODE  " if c["type"] == "barcode" else "CODE  ") + c["data"], "buttons": btn})
    return {"type": "guide", "gid": "scancode", "title": "SCANNED", "paged": False, "status": "shown only: nothing is opened or run", "steps": steps or [{"n": 1, "text": "No barcode or QR code found. Hold it flat, closer and in good light."}]}
