"""Self-improvement, with you in charge. Jarvis notes where it let you down (you say "that's wrong", an intent is rejected, a skill fails). When you say "improve yourself" a separate Claude run, which can only
read and edit files (no shell, no web), works on a COPY of the code in ~/jarvis-improve/, aiming at those failures. My own code then checks the result (protected files untouched, size limit, compiles, imports)
and shows you the change. Nothing touches the live code until you press APPLY (or say "apply the improvement"); APPLY makes a backup, commits, health-checks and rolls back if the check fails."""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import config, updater

ROOT = config.ROOT
LOG = config.DATA / "friction.jsonl"
STATE = config.DATA / "improve.json"
WORK = Path(os.path.expanduser("~/jarvis-improve"))
PROTECTED = ("jarvis/terminal.py", "jarvis/actions.py", "jarvis/intents.py", "jarvis/mac.py", "jarvis/jarvis_pair.py", "jarvis/phone.py", "jarvis/spotify.py", "jarvis/syshold.py",
             "jarvis/improve.py", "jarvis/updater.py", "jarvis/publish.py", "jarvis/watchdog.py", "jarvis/config.py", "jarvis/settings.py", "jarvis/opener.py", "jarvis/phoneuse.py", "jarvis/voiceapi.py",
             "jarvis/companion.py", "bin/", "hypr/", "mac/", ".gitignore")
MAX_DIFF_LINES = 500
WRONG = re.compile(r"^(?:no[, ]+)?(?:that'?s (?:wrong|not right|not what i (?:asked|said|meant))|(?:that is|you(?:'re| are)) wrong|wrong answer|you got that wrong|not what i asked)\b", re.I)


def log(kind, text, detail=""):
    """One line of friction. Stays on this machine (data/ is never committed or published)."""
    try:
        LOG.parent.mkdir(exist_ok=True)
        with open(LOG, "a") as f:
            f.write(json.dumps({"t": int(time.time()), "kind": kind, "text": (text or "")[:200], "detail": (detail or "")[:300]}) + "\n")
        lines = LOG.read_text().splitlines()
        if len(lines) > 500:
            LOG.write_text("\n".join(lines[-400:]) + "\n")
    except OSError:
        pass


def recent(n=20):
    try:
        return [json.loads(x) for x in LOG.read_text().splitlines()[-n:]]
    except (OSError, ValueError):
        return []


def is_wrong(text):
    return bool(WRONG.match((text or "").strip()))


PROMPT = """You are improving a voice assistant's source code (this folder is a copy; the live one is untouched). Below are recent failures. For each, find the cause in the code and fix it with the SMALLEST
sensible change: add a missing phrase pattern to a skill, fix a regex, improve the system prompt text in jarvis/config.py is NOT allowed (protected), fix a bug in a skill or the web page.
Your working directory is a copy of the project: use RELATIVE paths only (for example jarvis/prefs.py), never absolute paths. Rules: you may only read and edit files; edit only under jarvis/ (jarvis/web/ included) and tests/; do NOT touch these protected files: {protected}; never add network calls, never add shell execution, never weaken
any safety check; keep the whole change under 300 changed lines; if a failure needs a protected file or a big redesign, do not fix it and say so. Finish with 3 short lines: what you changed, why, what you did NOT fix.

Failures (newest last):
{items}
"""


def _run_agent(workdir, items, timeout=240):
    cmd = ["claude", "-p", "--output-format", "text", "--tools", "Read,Edit,Write,Grep,Glob", "--allowedTools", "Read,Edit,Write,Grep,Glob", "--permission-mode", "acceptEdits",
           "--strict-mcp-config", "--disable-slash-commands", "--setting-sources", "project", "--system-prompt", "You are a careful code maintainer. Output only what is asked."]
    if config.CLAUDE_MODEL:
        cmd += ["--model", config.CLAUDE_MODEL]
    prompt = PROMPT.format(protected=", ".join(PROTECTED), items="\n".join(f"- [{i['kind']}] said: {i['text']!r} {i['detail']}" for i in items))
    r = subprocess.run(cmd + [prompt], cwd=workdir, capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "").strip()[-600:]


def propose():
    """-> {"ok", "message", "summary", "files", "diff_lines", "checks"}. Creates ~/jarvis-improve/<time>/ (a copy), runs the editor agent there, checks the result."""
    items = recent(20)
    if not items:
        return {"ok": False, "message": "I have nothing to learn from yet. When I get something wrong, say: that's wrong."}
    WORK.mkdir(exist_ok=True)
    wd = WORK / time.strftime("%Y%m%d-%H%M%S")
    subprocess.run(["git", "clone", "-q", "--local", str(ROOT), str(wd)], check=True, capture_output=True)
    try:
        summary = _run_agent(wd, items)
    except subprocess.TimeoutExpired:
        shutil.rmtree(wd, ignore_errors=True)
        return {"ok": False, "message": "The improvement run took too long, so I stopped it. Nothing changed."}
    diff = subprocess.run(["git", "-C", str(wd), "diff", "HEAD", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/"], capture_output=True, text=True).stdout
    files = [ln[6:] for ln in diff.splitlines() if ln.startswith("+++ b/")]
    lines = sum(1 for ln in diff.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---")))
    if not files:
        print("[improve] no diff; raw diff head:", diff[:300].replace("\n", " | "), "; status:", subprocess.run(["git", "-C", str(wd), "status", "--short"], capture_output=True, text=True).stdout[:200], "| agent said:", summary[:200].replace("\n", " "), flush=True)
        shutil.rmtree(wd, ignore_errors=True)
        return {"ok": False, "message": "I looked, but found nothing I could safely change. " + summary[:160], "summary": summary}
    bad = [f for f in files if f.startswith(PROTECTED) or not (f.startswith("jarvis/") or f.startswith("tests/"))]
    if bad:
        shutil.rmtree(wd, ignore_errors=True)
        return {"ok": False, "message": "The proposed change touched protected files (" + ", ".join(bad[:3]) + "), so I threw it away."}
    if lines > MAX_DIFF_LINES:
        shutil.rmtree(wd, ignore_errors=True)
        return {"ok": False, "message": f"The proposed change was too big ({lines} lines), so I threw it away."}
    py = ROOT / ".venv" / "bin" / "python"
    py = str(py) if py.exists() else "python3"
    c1 = subprocess.run([py, "-m", "compileall", "-q", str(wd / "jarvis")], capture_output=True, text=True)
    c2 = subprocess.run([py, "-c", "import sys; sys.path.insert(0, '.'); import jarvis.webapp, jarvis.skills"], capture_output=True, text=True, cwd=str(wd))
    checks = {"compiles": c1.returncode == 0, "imports": c2.returncode == 0}
    patch = wd / "proposal.patch"
    patch.write_text(diff)
    STATE.write_text(json.dumps({"dir": str(wd), "patch": str(patch), "files": files, "lines": lines, "summary": summary, "checks": checks, "t": int(time.time())}))
    ok = all(checks.values())
    return {"ok": ok, "message": "Here is what I would change." if ok else "The change failed its checks, so I kept it out of the live code.", "summary": summary, "files": files, "diff_lines": lines, "checks": checks}


def pending():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return None


def apply():
    """-> (ok, message). Only after you pressed APPLY."""
    st = pending()
    if not st or not all(st["checks"].values()):
        return False, "There is no improvement waiting."
    if updater.git("status", "--porcelain", "--untracked-files=no")[1].strip():
        return False, "You have uncommitted edits, so I will not apply over them. Commit them first."
    b = updater.backup()
    rc, old = updater.git("rev-parse", "HEAD")
    r = subprocess.run(["git", "-C", str(ROOT), "apply", "--check", st["patch"]], capture_output=True, text=True)
    if r.returncode:
        return False, "The change no longer fits the current code, so I did not apply it."
    subprocess.run(["git", "-C", str(ROOT), "apply", st["patch"]], check=True, capture_output=True)
    ok, why = updater.health_ok()
    if not ok:
        updater.git("checkout", "--", ".")
        return False, f"The change failed the health check ({why}); I put everything back."
    updater.git("add", "-A")
    updater.git("-c", "user.name=Jarvis", "-c", "user.email=jarvis@localhost", "commit", "-qm", "self-improve: " + (st["summary"].splitlines() or ["change"])[0][:80])
    discard(keep_state=False)
    return True, f"Applied ({len(st['files'])} file{'s' if len(st['files']) != 1 else ''}). Backup: {Path(b).name}. Restarting."


def discard(keep_state=False):
    st = pending()
    if st:
        shutil.rmtree(st["dir"], ignore_errors=True)
    try:
        STATE.unlink()
    except OSError:
        pass
    return "Discarded."
