"""A9: the context builder: small, fast, right pieces. Run: .venv/bin/python tests/context_test.py"""
import json, os, sys, tempfile, pathlib, statistics, time
T = tempfile.mkdtemp(prefix="ct-"); os.environ.update(XDG_CONFIG_HOME=T + "/x", JARVIS_DATA_DIR=T + "/d")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from jarvis import context, vault, memory, config, maps, spotify_cli
R = []
def rec(n, ok): R.append(ok); print(("PASS " if ok else "FAIL ") + n, flush=True)
vault.CACHE = pathlib.Path(T + "/vc.json"); memory.PATH = pathlib.Path(T + "/mem.json")
vault._cache_save({"items": [{"path": "Projects/Pi project.md", "title": "Pi project", "text": "wired an LED to GPIO 17 on the Raspberry Pi 5", "mtime": 1}, {"path": "Memory/Coffee.md", "title": "Coffee", "text": "I prefer dark roast", "mtime": 1}] + [{"path": f"Memory/T{i}.md", "title": f"T{i}", "text": "filler note about widgets " * 5, "mtime": 1} for i in range(120)], "t": time.time()})
memory.add("my favourite editor is Neovim")
class A: look = {"open": True, "source": "phone camera", "boxes": [{"name": "CHARGER"}]}
out = context.build("what did I wire to the Raspberry Pi GPIO", A())
rec("date/time/home line is always there", out.startswith("[context:"))
rec("the matching note snippet is included and named", "Pi project" in out and context.LAST["notes"] == ["Pi project"])
rec("an unrelated note is NOT included", "Coffee" not in out and "dark roast" not in out)
rec("what is on screen: camera view and boxes", "camera view open (phone camera)" in out and "charger" in out)
out2 = context.build("which editor do I like", None); rec("a matching remembered fact is included", "Neovim" in out2)
maps.LAST_SPEC.update(id="m1", t=time.time()); maps.SPECS["m1"] = {"dest": {"label": "Starbucks"}, "routes": {"car": {}}, "mode": "car"}
rec("map place on screen", "map open: Starbucks (car)" in context.build("how long", None))
rec(f"small: {len(out)} characters for a full context (cap {context.MAX_CHARS + 400})", len(out) < context.MAX_CHARS + 600)
qs = ["what time is it", "play some jazz", "how long to Starbucks", "what did I wire to the Pi", "tell me a joke", "which editor do I like"] * 20
ms = []
for q in qs:
    context.build(q, A()); ms.append(context.LAST["ms"])
rec(f"added time: median {statistics.median(ms):.2f} ms, worst {max(ms):.2f} ms over {len(ms)} builds (budget: a simple local command < 300 ms, first spoken word < 1.5 s)", statistics.median(ms) < 20 and max(ms) < 100)
print("RESULT", "ALL PASS" if all(R) else "FAILURES"); sys.exit(0 if all(R) else 1)
