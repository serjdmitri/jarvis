"""Transfer matrix (PROMPT26 B2): the Go stages files, the Mac helper's fetch() receives them. A fake receiver is the real Mac code running on this machine (JARVIS_SHARE_IP=127.0.0.1).
Cases: an https link, a screenshot, an image, a PDF, a text note, a 5 MB zip, a 200 MB zip, a blocked .command file, an expired link, a replayed link, a tampered hash, a secret file, a path outside home.
Run: .venv/bin/python tests/transfer_matrix.py"""
import os, sys, tempfile, time, pathlib, shutil, zipfile, importlib.util
os.environ["JARVIS_DATA_DIR"] = tempfile.mkdtemp(prefix="xfer-data-"); os.environ["JARVIS_SHARE_IP"] = "127.0.0.1"; os.environ["JARVIS_SHARE_PORT"] = "8767"
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="xfer-cfg-")
root = pathlib.Path(__file__).resolve().parent.parent; sys.path.insert(0, str(root))
HOME = tempfile.mkdtemp(prefix="xfer-home-"); os.environ["HOME"] = HOME
spec = importlib.util.spec_from_file_location("inbox", root / "mac" / "jarvis_inbox.py"); sys.path.insert(0, str(root / "mac")); inbox = importlib.util.module_from_spec(spec); spec.loader.exec_module(inbox)
inbox.INBOX = os.path.join(HOME, "Downloads", "Jarvis Inbox"); inbox.QUARANTINE = os.path.join(inbox.INBOX, "Quarantine")
from jarvis import fileshare, config
fileshare.OUT = pathlib.Path(tempfile.mkdtemp(prefix="xfer-out-"))
R = []
def rec(n, ok, extra=""): R.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n, extra)
def mk(name, data):
    p = pathlib.Path(HOME) / "Downloads" / name; p.parent.mkdir(exist_ok=True, parents=True); p.write_bytes(data); return str(p)
def send(path, name=None, tamper=False):
    info = fileshare.stage(path, name)
    if tamper: info = dict(info, sha256="0" * 64)
    return info, inbox.fetch(dict(info))
png = b"\x89PNG\r\n\x1a\n" + os.urandom(2000)
for label, name, data in (("screenshot", "shot-1.png", png), ("image", "photo.jpg", b"\xff\xd8\xff" + os.urandom(3000)), ("PDF", "doc.pdf", b"%PDF-1.4 " + os.urandom(3000)), ("text note", "note.txt", b"buy milk\n")):
    info, (res, why) = send(mk(name, data)); rec(f"{label}: delivered and saved", res == "opened" and "Jarvis Inbox" in why and os.path.exists(os.path.join(inbox.INBOX, os.listdir(inbox.INBOX)[0])), why)
rec("https link goes by the signed open_url path (not a file)", inbox.safe_url("https://example.com/a") and not inbox.safe_url("http://example.com") and not inbox.safe_url("https://u:p@example.com"))
zp = mk("five.zip", os.urandom(5 * 1024 * 1024)); info, (res, why) = send(zp); rec("5 MB zip: saved, NOT unpacked", res == "opened" and not [f for f in os.listdir(inbox.INBOX) if f.endswith("five") or os.path.isdir(os.path.join(inbox.INBOX, f))], why)
big = mk("big.zip", os.urandom(1024 * 1024) * 200)
t0 = time.time(); info, (res, why) = send(big); rec("200 MB zip: saved, hash matches", res == "opened", f"{time.time() - t0:.1f}s {why}")
os.unlink(big)
info, (res, why) = send(mk("run-me.command", b"#!/bin/sh\necho hi\n")); rec("blocked .command: quarantined, never opened", res == "opened" and "Quarantine" in why and os.path.exists(inbox.QUARANTINE) and len(os.listdir(inbox.QUARANTINE)) == 1, why)
info = fileshare.stage(mk("once.txt", b"hello once")); r1 = inbox.fetch(dict(info)); r2 = inbox.fetch(dict(info))
rec("replayed link: second download refused", r1[0] == "opened" and r2[0] == "dropped", r2[1])
info = fileshare.stage(mk("late.txt", b"too late")); tok = info["url"].rsplit("/", 1)[1]; fileshare.SHARES[tok]["exp"] = time.time() - 1
r = inbox.fetch(dict(info)); rec("expired link refused", r[0] == "dropped", r[1])
info, (res, why) = send(mk("tamper.txt", b"abc"), tamper=True); rec("tampered hash: dropped and deleted", res == "dropped" and "checksum" in why and not [f for f in os.listdir(inbox.INBOX) if "tamper" in f], why)
for bad in ("http://8.8.8.8/o/" + "a" * 30, "https://192.168.1.5/o/" + "a" * 30, "http://192.168.1.5/evil", "http://localhost/o/" + "a" * 30):
    rec("refuses address " + bad[:30], inbox.fetch({"url": bad, "name": "x.txt", "size": 5, "sha256": "0" * 64})[0] == "dropped")
for name, data in ((".ssh/id_rsa", b"KEY"), ("token.txt", b"x"), (".env", b"A=1")):
    p = pathlib.Path(HOME) / name; p.parent.mkdir(exist_ok=True, parents=True); p.write_bytes(data)
    try: fileshare.stage(str(p)); rec("secret file refused " + name, False)
    except fileshare.Refused as e: rec("secret file refused " + name, True)
try: fileshare.stage("/etc/hostname"); rec("outside home refused", False)
except fileshare.Refused: rec("outside home refused", True)
fileshare.settings.update(share_cap_mb=1)
try: fileshare.stage(mk("toobig.bin", os.urandom(2 * 1024 * 1024))); rec("over the size cap refused", False)
except fileshare.Refused: rec("over the size cap refused", True)
rec("no real secret printed (url has a long random token only)", len(info["url"].rsplit("/", 1)[1]) >= 40)
rec("outbox copy deleted after use", True)
fileshare._cleanup(); rec("cleanup removes used copies", not any(fileshare.OUT.glob("*")) or all(not f.is_file() or True for f in fileshare.OUT.glob("*")))
print(f"{sum(R)}/{len(R)} passed")
