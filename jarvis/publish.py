"""Keep the PUBLIC copy in sync with this working folder, safely. Copies only an allowlist of code, refuses if any of your private values appear in the files, shows what changed,
and pushes only when you say so. The public README, docs, licence and screenshots live in the public folder and are never overwritten."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import config, settings

SRC = config.ROOT
DST = Path(os.path.expanduser("~/jarvis-public"))
PATHS = ["jarvis", "bin", "mac", "tests", "hypr/jarvis.lua"]
SKIP = {"__pycache__"}
PATTERNS = [r"[A-Za-z0-9._%+-]+@(?:gmail|outlook|hotmail|icloud|yahoo)\.[a-z]+", r"/home/[a-z][a-z0-9_-]+/", r"\bjv-[0-9a-f]{16}\b"]


def private_values():
    """Strings that must never appear in the public tree: your address, coordinates, topics, tokens, anything in the settings that looks personal."""
    vals = set()
    s = settings.load()
    for k in ("home_area", "phone_cam_url", "phone_cam_typed", "claude_dir"):
        v = str(s.get(k) or "").strip()
        if len(v) >= 6:
            vals.add(v)
            vals.add(v.split(",")[0])
    for f in ("ntfy_topic", "spotify_client_id"):
        try:
            v = (Path(settings.PATH).parent / f).read_text().strip()
            if len(v) >= 8:
                vals.add(v)
        except OSError:
            pass
    user = os.environ.get("USER") or ""
    if len(user) >= 3:
        vals.add("/home/" + user)
    return {v for v in vals if len(v) >= 6}


def scan(root):
    """-> [(file, why)] for anything private in the tree at `root`."""
    bad = []
    vals = private_values()
    for p in Path(root).rglob("*"):
        if not p.is_file() or ".git" in p.parts or p.suffix in (".jpg", ".png", ".onnx", ".gz") or "vendor" in p.parts:
            continue
        try:
            t = p.read_text(errors="ignore")
        except OSError:
            continue
        for v in vals:
            if v in t:
                bad.append((str(p.relative_to(root)), "contains a private value from your settings"))
        for pat in PATTERNS:
            if re.search(pat, t):
                bad.append((str(p.relative_to(root)), "matches " + pat[:20]))
    return bad


def sync():
    """Copy the TRACKED files under the allowlisted paths into the public folder (files that no longer exist in the source are removed there). Untracked or git-ignored files never travel.
    -> list of changed paths (git status of the public folder)."""
    if not (DST / ".git").exists():
        raise RuntimeError("the public folder ~/jarvis-public is not a git clone")
    tracked = subprocess.run(["git", "-C", str(SRC), "ls-files", "--", *PATHS], capture_output=True, text=True).stdout.split("\n")
    tracked = [t for t in tracked if t and "__pycache__" not in t]
    for rel in PATHS:
        d = DST / rel
        if d.is_dir():
            shutil.rmtree(d)
        elif d.is_file():
            d.unlink()
    for rel in tracked:
        d = DST / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SRC / rel, d)
        shutil.copymode(SRC / rel, d)
    r = subprocess.run(["git", "-C", str(DST), "status", "--porcelain"], capture_output=True, text=True)
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def prepare():
    """-> {"changes": [...], "problems": [...]}. Syncs, then scans the PUBLIC tree. If anything private is found the sync is undone."""
    changes = sync()
    problems = scan(DST)
    if problems:
        subprocess.run(["git", "-C", str(DST), "checkout", "--", "."], capture_output=True)
        subprocess.run(["git", "-C", str(DST), "clean", "-fdq"], capture_output=True)
    return {"changes": changes, "problems": problems}


def push(message):
    """Commit and push the prepared changes (only called after you press PUSH). -> (ok, text)."""
    if scan(DST):
        return False, "Refused: private values found."
    g = lambda *a: subprocess.run(["git", "-C", str(DST), "-c", "user.name=Jarvis project", "-c", "user.email=noreply@example.invalid", *a], capture_output=True, text=True)
    g("add", "-A")
    if not g("status", "--porcelain").stdout.strip():
        return True, "Nothing to publish."
    r = g("commit", "-qm", message[:200])
    if r.returncode:
        return False, "Commit failed."
    r = g("push", "origin", "HEAD")
    return (r.returncode == 0), ("Published." if r.returncode == 0 else "Push failed: " + r.stderr.strip().splitlines()[-1][:100])


if __name__ == "__main__":
    r = prepare()
    print(f"{len(r['changes'])} changed files" + (f", {len(r['problems'])} PROBLEMS: {r['problems'][:5]}" if r["problems"] else ", clean"))
    if "--yes" in sys.argv and not r["problems"]:
        print(push(" ".join(a for a in sys.argv[1:] if not a.startswith("--")) or "Update")[1])
