"""USB webcam support. The camera is OFF until a frame is asked for: each capture opens the device, grabs one frame with ffmpeg and
closes it again. The Surface Go's built-in cameras (IPU3/CIO2, OmniVision sensors) are blacklisted on purpose because loading them
hard-locks this machine: this module never loads, probes or opens anything except a /dev/video node whose sysfs parent is a USB device."""
import os
import re
import subprocess
import time

from . import config

NO_WEBCAM = "No USB webcam found. The built-in cameras are disabled on purpose."
_BAD = re.compile(r"ipu3|cio2|imgu|ov\d{4}|int3472|atomisp|surface|intel", re.I)
SYS = "/sys/class/video4linux"


def usb_webcams():
    """-> [(device path, name)] for capture-capable USB video nodes. Reads sysfs only; never opens a device."""
    out = []
    try:
        names = sorted(os.listdir(SYS))
    except OSError:
        return out
    for n in names:
        if not n.startswith("video"):
            continue
        base = os.path.join(SYS, n)
        try:
            real = os.path.realpath(base)
            name = open(os.path.join(base, "name")).read().strip()
            idx = open(os.path.join(base, "index")).read().strip()
        except OSError:
            continue
        if "/usb" not in real or _BAD.search(name) or _BAD.search(real) or idx != "0":   # index 0 = the capture node (others are metadata)
            continue
        out.append(("/dev/" + n, name))
    return out


def capture():
    """Grab one frame from the first USB webcam -> path of a jpg in shots/. Raises RuntimeError(NO_WEBCAM) if there is none."""
    cams = usb_webcams()
    if not cams:
        raise RuntimeError(NO_WEBCAM)
    dev, _ = cams[0]
    config.SHOTS_DIR.mkdir(exist_ok=True)
    for f in sorted(config.SHOTS_DIR.glob("shot-*.jpg"))[:-9]:
        f.unlink()
    path = str(config.SHOTS_DIR / f"shot-{int(time.time() * 1000)}.jpg")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "v4l2", "-i", dev, "-frames:v", "8", "-update", "1",
           "-vf", "scale=960:-2", "-q:v", "4", path]                     # a few frames so auto-exposure can settle; the last one is kept
    try:
        subprocess.run(cmd, check=True, timeout=8, capture_output=True)
    except subprocess.TimeoutExpired:
        raise RuntimeError("the webcam did not answer in time")
    except subprocess.CalledProcessError as e:
        raise RuntimeError("webcam capture failed: " + e.stderr.decode(errors="ignore")[-120:])
    return path
