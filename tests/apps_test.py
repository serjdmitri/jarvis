"""PROMPT28 item 5: LibreOffice conversions (real), disk report (read only), viewers routing. Run: .venv/bin/python tests/apps_test.py"""
import os, sys, types, zipfile, subprocess, glob
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from jarvis import office, diskuse, skills, core, config
R = []
def rec(n, ok): R.append(ok); print(("PASS " if ok else "FAIL ") + n, flush=True)
TXT = "- 2x M3 screws - 6 mm\n- Raspberry Pi 4: 1\n- 330 ohm resistor, 5"
made = []
for fmt, magic in (("xlsx", b"PK"), ("ods", b"PK"), ("docx", b"PK"), ("pdf", b"%PDF")):
    p, why = office.convert(TXT, fmt, "apps test"); made.append(p)
    rec(f"real conversion to {fmt}: {os.path.basename(p) if p else why}", bool(p) and open(p, "rb").read(4).startswith(magic) and os.path.getsize(p) > 500)
x = [m for m in made if m.endswith(".xlsx")][0]
ss = zipfile.ZipFile(x).read("xl/sharedStrings.xml").decode()
rec("spreadsheet really contains the list (screws, Pi, resistor)", all(w in ss for w in ("screws", "Raspberry Pi 4", "resistor")))
p2, _ = office.convert(TXT, "xlsx", "apps test"); rec(f"never overwrites: second file is '{os.path.basename(p2)}'", p2 != x and os.path.exists(x) and os.path.exists(p2))
rec("nothing to convert -> plain reason, no file", office.convert("  ", "xlsx")[0] is None)
for f in glob.glob(str(config.DATA / "office" / "apps test*")): os.remove(f)
# run_office: confirms only through the Mac path
said, sent = [], []
st = types.SimpleNamespace(recent=[("q", TXT)], say=lambda t, **k: said.append(t), run_mac=lambda pl: (sent.append(pl), {"ok": True})[1])
core.Assistant.run_office(st, {"fmt": "xlsx", "about": "last"})
rec("run_office hands the real file to the Mac path", bool(sent) and sent[0]["file"].endswith(".xlsx") and os.path.exists(sent[0]["file"]))
st2 = types.SimpleNamespace(recent=[("q", TXT)], say=lambda t, **k: said.append(t), run_mac=lambda pl: {"ok": False, "message": "no"})
core.Assistant.run_office(st2, {"fmt": "docx", "about": "last"})
rec(f"Mac not reached: says it was only made here ('{said[-1][:60]}')", "could not save it to your Mac" in said[-1])
for f in glob.glob(str(config.DATA / "office" / "Jarvis *")): os.remove(f)
# disks
c, u = diskuse.card(True)
rec(f"disk card: bar + free space + {len(c['steps']) - 2} biggest folders (read only)", len(c["steps"]) >= 4 and "read only" in c["status"] and u["free"] > 0)
src = open(ROOT + "/jarvis/diskuse.py").read()
rec("disk code never deletes or moves (no rm / unlink / remove / rmtree / mv)", not any(w in src for w in ("rmtree", "os.remove", "unlink", '"rm"', '"mv"', "shutil")))
for q, k in (("make that a spreadsheet", "office"), ("make that a document", "office"), ("how much space is left", "disk"), ("what is using it", "disk"), ("open that picture", "viewfile"), ("open that pdf", "viewfile")):
    rec(f"voice '{q}' -> {k}", k in (skills.handle(q, "jarvis") or {}))
rec("Aether is not touched (theme generator; only opens if asked by name)", "aether" not in " ".join(open(ROOT + "/jarvis/office.py").read().split()))
print("RESULT", "ALL PASS" if all(R) else "FAILURES")
