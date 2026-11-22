"""Self-update, backup and restore. Nothing here runs unless you ask ("update yourself", "back up yourself").
update(): backup first, `git pull --ff-only`, then a health check (compile + import); if the check fails the old version is put back (rollback). The brain is not involved."""
import json
import os
import subprocess
import tarfile
import time
from pathlib import Path

from . import config

ROOT = config.ROOT
BACKUPS = Path(os.path.expanduser("~/jarvis-backups"))
CFG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "jarvis"


def git(*args, timeout=60):
    r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def source():
    """-> (remote url or None, branch). A folder with no remote has no update source."""
    rc, url = git("remote", "get-url", "origin")
    rc2, br = git("rev-parse", "--abbrev-ref", "HEAD")
    return (url if rc == 0 else None), (br if rc2 == 0 else "main")


def check():
    """-> {"ok", "behind", "changes": [lines], "message"}; fetches from the remote (network)."""
    url, br = source()
    if not url:
        return {"ok": False, "behind": 0, "changes": [], "message": "This copy has no update source (no git remote). Use bin/jarvis-publish to push your changes, or clone the public repository to get updates."}
    rc, out = git("fetch", "--quiet", "origin", timeout=60)
    if rc != 0:
        return {"ok": False, "behind": 0, "changes": [], "message": "Could not reach the update source."}
    rc, n = git("rev-list", "--count", f"HEAD..origin/{br}")
    behind = int(n) if rc == 0 and n.isdigit() else 0
    rc, log = git("log", "--oneline", "--no-decorate", f"HEAD..origin/{br}", "-n", "12")
    changes = [ln[:110] for ln in log.splitlines()] if rc == 0 else []
    dirty = git("status", "--porcelain", "--untracked-files=no")[1].strip()
    msg = "You are up to date." if behind == 0 else f"{behind} new change{'s' if behind != 1 else ''} available."
    if behind and dirty:
        msg += " You have local edits; they must be committed first."
    return {"ok": True, "behind": behind, "changes": changes, "message": msg, "dirty": bool(dirty)}


def backup(keep=10):
    """Your private data (settings, tokens, pairings, memory, notes, reminders, lists, scenes) -> ~/jarvis-backups/jarvis-<time>.tar.gz (mode 600). Models and caches are not included."""
    BACKUPS.mkdir(mode=0o700, exist_ok=True)
    path = BACKUPS / time.strftime("jarvis-%Y%m%d-%H%M%S.tar.gz")
    with tarfile.open(path, "w:gz") as t:
        for base, arc in ((CFG, "config"), (config.DATA, "data"), (ROOT / "notes", "notes")):
            if base.exists():
                t.add(base, arcname=arc)
    os.chmod(path, 0o600)
    for old in sorted(BACKUPS.glob("jarvis-*.tar.gz"))[:-keep]:
        old.unlink()
    return str(path)


def list_backups():
    return sorted(BACKUPS.glob("jarvis-*.tar.gz"), reverse=True)


def restore(path=None):
    """Put a backup back (default: the newest). The current files are backed up first. Refuses archives with paths outside config/, data/, notes/."""
    files = list_backups()
    src = Path(path) if path else (files[0] if files else None)
    if not src or not src.exists():
        return False, "No backup found."
    backup()
    with tarfile.open(src) as t:
        for m in t.getmembers():
            if not (m.name.split("/")[0] in ("config", "data", "notes")) or ".." in m.name or m.name.startswith("/") or not (m.isfile() or m.isdir()):
                return False, "That backup looks wrong, so I did not restore it."
        for m in t.getmembers():
            dest = {"config": CFG, "data": config.DATA, "notes": ROOT / "notes"}[m.name.split("/")[0]]
            m.name = "/".join(m.name.split("/")[1:])
            if m.name:
                t.extract(m, dest, filter="data")
    return True, f"Restored from {src.name}. Restart Jarvis to use it."


def lint_names(pkg_dir):
    """Undefined global names in every module (the kind of mistake that crashed start-up once: a module used but never imported). -> ["file: name", ...]"""
    import ast
    import builtins
    bad = []
    for f in sorted(Path(pkg_dir).glob("*.py")):
        try:
            t = ast.parse(f.read_text())
        except SyntaxError as e:
            bad.append(f"{f.name}: syntax error line {e.lineno}")
            continue
        defined = set(dir(builtins)) | {"__file__", "__name__"}
        for n in ast.walk(t):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    defined.add((a.asname or a.name).split(".")[0])
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(n.name)
            elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                defined.add(n.id)
            elif isinstance(n, ast.arg):
                defined.add(n.arg)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                defined.add(n.name)
            elif isinstance(n, ast.MatchAs) and n.name:
                defined.add(n.name)
        used = {n.id for n in ast.walk(t) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        bad += [f"{f.name}: {u}" for u in sorted(used - defined)]
    return bad


def preflight(root=None):
    """Before ANY restart: compile, no undefined names, and the server module imports. -> (ok, plain reason)."""
    root = Path(root or ROOT)
    py = root / ".venv" / "bin" / "python"
    py = str(py) if py.exists() else str(ROOT / ".venv" / "bin" / "python")
    r = subprocess.run([py, "-m", "compileall", "-q", str(root / "jarvis")], capture_output=True, text=True, timeout=120)
    if r.returncode:
        return False, "does not compile: " + (r.stdout or r.stderr).strip().splitlines()[-1][:100]
    bad = lint_names(root / "jarvis")
    if bad:
        return False, "undefined names: " + ", ".join(bad[:4])
    r = subprocess.run([py, "-c", "import sys; sys.path.insert(0, '.'); import jarvis.webapp"], capture_output=True, text=True, timeout=60, cwd=str(root))
    if r.returncode:
        return False, "import failed: " + (r.stderr.strip().splitlines() or ["?"])[-1][:100]
    return True, "ok"


def health_ok():
    """After an update: everything compiles and the server module imports."""
    py = ROOT / ".venv" / "bin" / "python"
    py = str(py) if py.exists() else "python3"
    r = subprocess.run([py, "-m", "compileall", "-q", str(ROOT / "jarvis")], capture_output=True, text=True, timeout=120)
    if r.returncode:
        return False, "compile failed"
    r = subprocess.run([py, "-c", "import jarvis.webapp"], capture_output=True, text=True, timeout=60, cwd=str(ROOT))
    return (r.returncode == 0), ("" if r.returncode == 0 else "import failed: " + (r.stderr.strip().splitlines() or ["?"])[-1][:100])


def update():
    """-> (ok, message). Backup, fast-forward pull, health check, rollback on failure. The caller restarts the server afterwards."""
    c = check()
    if not c["ok"]:
        return False, c["message"]
    if c["behind"] == 0:
        return True, "Already up to date."
    if c.get("dirty"):
        return False, "You have local edits that are not committed, so I will not pull over them."
    b = backup()
    rc, old = git("rev-parse", "HEAD")
    _, br = source()[1], source()[1]
    rc, out = git("merge", "--ff-only", f"origin/{br}")
    if rc != 0:
        return False, "The update could not be applied cleanly (" + out.splitlines()[-1][:80] + "). Nothing changed."
    ok, why = health_ok()
    if not ok:
        git("reset", "--hard", old)
        return False, f"The new version failed its check ({why}), so I put the old one back. Backup kept at {Path(b).name}."
    return True, f"Updated ({c['behind']} change{'s' if c['behind'] != 1 else ''}). Backup: {Path(b).name}. Restarting."


def restart_server():
    """Restart only if the code passes preflight; otherwise keep the running server and say why. -> (ok, message)."""
    ok, why = preflight()
    if not ok:
        return False, "Not restarting: the new code " + why
    subprocess.run(["pkill", "-f", "[.]venv/bin/python -m jarvis[.]webapp"], capture_output=True)       # the watchdog starts the new one
    return True, "restarting"


def parse_command(text):
    import re
    low = text.lower().strip(" .?!")
    if re.fullmatch(r"(?:please )?(?:check for (?:an )?updates?|are you up to date|any updates?|is there (?:a|an) update|what'?s new in jarvis)", low):
        return {"kind": "check"}
    if re.fullmatch(r"(?:please )?(?:update|upgrade) (?:yourself|jarvis|now)|(?:please )?install (?:the )?update", low):
        return {"kind": "update"}
    if re.fullmatch(r"(?:please )?(?:back ?up|backup) (?:yourself|jarvis|my (?:data|settings))|(?:please )?make a backup", low):
        return {"kind": "backup"}
    if re.fullmatch(r"(?:please )?restore (?:the |my )?(?:latest |last )?backup", low):
        return {"kind": "restore"}
    return None
