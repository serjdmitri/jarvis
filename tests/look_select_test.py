"""A2: singular and selective highlights. Run: .venv/bin/python tests/look_select_test.py"""
import os, sys, types
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from jarvis import look, opener, core, track
R = []
def rec(n, ok): R.append(ok); print(("PASS " if ok else "FAIL ") + n, flush=True)
B = [{"name": "CHARGER", "x": .1, "y": .1, "w": .2, "h": .2}, {"name": "PHONE 1", "x": .5, "y": .1, "w": .2, "h": .3}, {"name": "PHONE 2", "x": .5, "y": .6, "w": .2, "h": .3}]
st = {"open": True, "boxes": B}
def op(t, s=st): return look.parse_command(t, s)
for t, want in [("highlight the charger", ("keep", "charger")), ("highlight just the phone", ("keep", "phone")), ("highlight only the charger", ("keep", "charger")),
                ("stop highlighting the phone", ("drop", "phone")), ("remove the highlight on the charger", ("drop", "charger")), ("don't highlight the charger", ("drop", "charger")),
                ("clear highlights", ("clear", None)), ("clear the highlights", ("clear", None)), ("stop highlighting", ("clear", None)), ("remove all highlights", ("clear", None))]:
    c = op(t); rec(f"'{t}' -> {want[0]}", bool(c) and c["op"] == want[0] and (want[1] is None or c.get("what") == want[1]))
c = op("highlight the second one"); rec("'highlight the second one' -> keep box 2", c == {"op": "keepn", "n": 2})
c = op("highlight the last one"); rec("'highlight the last one' -> keep the last", c == {"op": "keepn", "n": -1})
c = op("highlight just the second one"); rec("'highlight just the second one'", c == {"op": "keepn", "n": 2})
c = op("highlight the screws", {"open": True, "boxes": B}); rec("'highlight the screws' (not boxed yet) -> a new look for ONLY screws", c and c["op"] == "box" and c.get("only") is True and c["what"] == "screws")
c = op("highlight just the pen", {"open": True, "boxes": B}); rec("'highlight just the pen' (not boxed) -> new look, only", c and c["op"] == "box" and c.get("only"))
c = op("highlight everything", {"open": True, "boxes": []}); rec("'highlight everything' keeps the all-items look", c and c["op"] in ("clear", "box") and not c.get("only"))
c = look.parse_command("highlight the charger", {"open": False, "boxes": []}); rec("view closed, no camera cue: not taken", c is None)
c = look.parse_command("highlight the charger on my desk", {"open": False, "boxes": []}); rec("view closed + 'on my desk': one look, only the charger", c and c["op"] == "box" and c.get("only"))
rec("the singular prompt asks for ONLY that object and numbers several", "ONLY this" in look.ONLY and "numbers" in look.ONLY)
# subset on a stub core: boxes stay where the tracker has them
sent = []; opener.NOTIFY = lambda m: sent.append(m)
tk = track.Tracker(lambda m: None); tk.size = (160, 120)
class T:  # stand-in tracks
    def __init__(s, b): s.b = b
    def out(s, W, H): return dict(s.b, lost=False)
tk.tracks = [T(dict(b, x=b["x"] + .05)) for b in B]
me = types.SimpleNamespace(look={"boxes": [dict(b) for b in B], "sel": None}, tracker=tk)
r = core.Assistant._look_subset(me, {"op": "drop", "what": "phone"})
rec(f"'stop highlighting the phone' removes both phones, keeps the charger where it is NOW ({r})", [b["name"] for b in me.look["boxes"]] == ["CHARGER"] and abs(me.look["boxes"][0]["x"] - .15) < 1e-6 and len(tk.tracks) == 1)
me = types.SimpleNamespace(look={"boxes": [dict(b) for b in B], "sel": None}, tracker=tk); tk.tracks = [T(dict(b)) for b in B]
r = core.Assistant._look_subset(me, {"op": "keepn", "n": 3}); rec(f"'the third one' keeps only PHONE 2 ({r})", [b["name"] for b in me.look["boxes"]] == ["PHONE 2"])
r = core.Assistant._look_subset(me, {"op": "drop", "what": "tractor"}); rec(f"dropping something not highlighted says so ({r})", "not highlighting" in r)
print("RESULT", "ALL PASS" if all(R) else "FAILURES"); sys.exit(0 if all(R) else 1)
