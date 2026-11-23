"""Disk space, read only: a fixed `df` for the home filesystem and `du` on the top-level folders of the home directory (or `disktree` is NOT used: du is enough and always works).
Never deletes, moves or suggests a delete command: it only reports. "how much space is left" and "what is using it" show one small card: a bar and the five biggest folders."""
import os
import re
import subprocess

HOME = os.path.expanduser("~")


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u in ("B", "KB") else f"{n:.1f} {u}"
        n /= 1024


def usage():
    """-> {"total", "used", "free", "pct"} for the filesystem holding the home folder."""
    r = subprocess.run(["df", "-B1", "--output=size,used,avail", HOME], capture_output=True, text=True, timeout=10)
    line = r.stdout.strip().splitlines()[-1].split()
    total, used, free = (int(x) for x in line[:3])
    return {"total": total, "used": used, "free": free, "pct": round(used * 100 / total) if total else 0}


def biggest(n=5, timeout=25):
    """The n biggest top-level folders in the home folder: [(name, bytes)]. A time limit keeps it from hanging; a partial answer says so."""
    try:
        r = subprocess.run(["du", "-x", "-B1", "--max-depth=1", HOME], capture_output=True, text=True, timeout=timeout, errors="replace")
        out, partial = r.stdout, False
    except subprocess.TimeoutExpired as e:
        out, partial = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""), True
    rows = []
    for ln in out.splitlines():
        m = re.match(r"^(\d+)\s+(.+)$", ln)
        if m and m.group(2).rstrip("/") != HOME.rstrip("/"):
            rows.append((os.path.basename(m.group(2).rstrip("/")) or m.group(2), int(m.group(1))))
    rows.sort(key=lambda x: -x[1])
    return rows[:n], partial


def bar(pct, width=20):
    k = max(0, min(width, round(pct * width / 100)))
    return "█" * k + "░" * (width - k)


def card(detail=True):
    u = usage()
    lines = [f"{bar(u['pct'])} {u['pct']}% used", f"{human(u['free'])} free of {human(u['total'])}"]
    partial = False
    if detail:
        rows, partial = biggest()
        lines += [f"{name}: {human(b)}" for name, b in rows]
    steps = [{"n": i + 1, "text": ln} for i, ln in enumerate(lines)]
    return {"type": "guide", "gid": "disk", "title": "DISK SPACE", "full": False, "paged": False, "status": "read only: nothing was changed" + (" (partial: it took too long)" if partial else ""), "steps": steps}, u


def speech(u, rows=None):
    s = f"You have {human(u['free'])} free, {u['pct']} percent used."
    if rows:
        s += " The biggest folder is " + rows[0][0] + " at " + human(rows[0][1]) + "."
    return s


def parse_command(text):
    low = re.sub(r"\s+", " ", (text or "").lower().strip(" .?!"))
    if re.fullmatch(r"(?:please )?(?:how much (?:disk |storage |drive )?(?:space|storage) (?:is |do i have |have i got )?(?:left|free|available|remaining)(?: on (?:my |the )?(?:disk|drive|computer|go|laptop))?|(?:how full|is) (?:my |the )?(?:disk|drive|storage)(?: full)?|disk (?:space|usage)|check (?:my |the )?disk(?: space)?)", low):
        return {"detail": False}
    if re.fullmatch(r"(?:please )?(?:what(?:'s| is) (?:using|taking|eating|filling)(?: up)?(?: all)?(?: of)?(?: the| my)?(?: disk| drive| hard drive| storage)?(?: space| storage| disk| drive| it| that)?(?: on (?:the|my) (?:go|computer|disk|drive|laptop))?|show (?:me )?(?:what(?:'s| is) using (?:my |the )?(?:space|disk)|disk usage by folder)|biggest folders|what are the biggest folders)", low):
        return {"detail": True}
    return None
