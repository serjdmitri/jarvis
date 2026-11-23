"""Use LibreOffice (already on the Go) quietly: "make that a spreadsheet" turns a parts list / buy list / any list into .xlsx or .ods, "make that a document" turns notes or a tutorial into .docx or .pdf.
Only the FIXED command `soffice --headless --convert-to <format> --outdir <dir> <file>` runs (no window, its own throwaway profile, a time limit); the text is written to a temp file by this code, never typed into a shell.
Results go to ~/jarvis/office/ with a number added if the name exists (never overwritten), then to the Mac's Downloads through the helper."""
import csv
import html
import os
import re
import shutil
import subprocess
import tempfile

from . import config

OUT = config.DATA / "office"
FORMATS = {"xlsx": ("sheet", "xlsx"), "ods": ("sheet", "ods"), "docx": ("doc", "docx:MS Word 2007 XML"), "pdf": ("doc", "pdf:writer_web_pdf_Export")}
BULLET = re.compile(r"^\s*(?:[-*•–]|\d+[.)])\s+")


def available():
    return bool(shutil.which("soffice"))


def _lines(text):
    return [BULLET.sub("", ln).strip() for ln in (text or "").replace("\r", "").split("\n") if ln.strip()]


def rows_from_text(text, title=""):
    """A list or notes -> table rows: one row per line; "name - qty - note", "name: qty", "name, qty" or "2 x name" become columns. The first row is a header."""
    out = []
    for ln in _lines(text):
        m = re.match(r"^(\d+)\s*[x×]\s+(.+)$", ln, re.I)
        if m:
            out.append([m.group(2).strip(), m.group(1)])
            continue
        parts = [p.strip() for p in re.split(r"\s+[-–—]\s+|:\s+|\t|,\s*(?=\d|x\d)", ln) if p.strip()]
        out.append(parts or [ln])
    w = max((len(r) for r in out), default=1)
    head = ["Item", "Quantity", "Notes"][:w] if w <= 3 else ["Item"] + ["Column %d" % i for i in range(2, w + 1)]
    return [head] + [r + [""] * (w - len(r)) for r in out]


def doc_html(text, title="Notes from Jarvis"):
    parts = ['<html><head><meta charset="utf-8"><title>%s</title></head><body><h1>%s</h1>' % (html.escape(title), html.escape(title))]
    items = []
    for ln in (text or "").replace("\r", "").split("\n"):
        if BULLET.match(ln):
            items.append("<li>%s</li>" % html.escape(BULLET.sub("", ln).strip()))
            continue
        if items:
            parts.append("<ul>%s</ul>" % "".join(items)); items = []
        if ln.strip():
            parts.append("<p>%s</p>" % html.escape(ln.strip()))
    if items:
        parts.append("<ul>%s</ul>" % "".join(items))
    return "".join(parts) + "</body></html>"


def numbered(name, ext):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"{name}.{ext}"
    n = 2
    while p.exists():                                              # never overwrite
        p = OUT / f"{name} {n}.{ext}"
        n += 1
    return p


def convert(text, fmt, title=""):
    """-> (path, "") or (None, plain reason). The result is checked: it must exist and not be empty."""
    if fmt not in FORMATS:
        return None, "I can make xlsx, ods, docx or pdf."
    if not available():
        return None, "LibreOffice is not installed here."
    if not (text or "").strip():
        return None, "There is nothing to put in it yet."
    kind, flt = FORMATS[fmt]
    ext = fmt
    tmp = tempfile.mkdtemp(prefix="jarvis-office-")
    try:
        if kind == "sheet":
            src = os.path.join(tmp, "list.csv")
            with open(src, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows([[("'" + c) if c[:1] in "=+-@" and not re.fullmatch(r"[-+]?\d+(?:[.,]\d+)?", c) else c for c in r] for r in rows_from_text(text, title)])      # a cell that starts with = + - @ is text, never a formula
            cmd = ["soffice", "-env:UserInstallation=file://" + tmp + "/profile", "--headless", "--convert-to", flt, "--outdir", tmp, src]
        else:
            src = os.path.join(tmp, "doc.html")
            with open(src, "w", encoding="utf-8") as f:
                f.write(doc_html(text, title or "Notes from Jarvis"))
            cmd = ["soffice", "-env:UserInstallation=file://" + tmp + "/profile", "--headless", "--infilter=HTML (StarWriter)", "--convert-to", flt, "--outdir", tmp, src]
        try:
            subprocess.run(cmd, capture_output=True, timeout=90, cwd=tmp, start_new_session=True)
        except subprocess.TimeoutExpired:
            subprocess.run(["pkill", "-f", "UserInstallation=file://" + tmp], capture_output=True)      # no orphaned soffice.bin left behind
            return None, "LibreOffice took too long."
        made = os.path.join(tmp, ("list." if kind == "sheet" else "doc.") + ext)
        if not os.path.exists(made) or os.path.getsize(made) < 200:
            return None, "The conversion did not produce a file."
        base = re.sub(r"[^A-Za-z0-9 _-]", "", title).strip()[:40] or ("Jarvis list" if kind == "sheet" else "Jarvis notes")
        dest = numbered(base, ext)
        shutil.copyfile(made, dest)
        return str(dest), ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def parse_command(text):
    """"make that a spreadsheet" / "turn the parts list into a document" / "save that as a pdf" -> {"fmt", "about"} or None."""
    low = re.sub(r"\s+", " ", (text or "").lower().strip(" .?!"))
    m = re.fullmatch(r"(?:please )?(?:make|turn|convert|save|export)(?: (?:that|this|it|the (?:parts|buy|shopping)? ?list|my (?:parts|buy|shopping) list|the notes|my notes|the tutorial))?(?: (?:into|to|as))? (?:a |an )?(spreadsheet|excel(?: file| sheet)?|ods|xlsx|document|word(?: doc| document)?|docx|pdf|doc)(?: file)?", low)
    if not m:
        return None
    w = m.group(1)
    fmt = "xlsx" if w.startswith(("spread", "excel", "xlsx")) else "ods" if w == "ods" else "pdf" if w == "pdf" else "docx"
    return {"fmt": fmt, "about": "parts" if re.search(r"parts|buy|shopping", low) else "last"}
