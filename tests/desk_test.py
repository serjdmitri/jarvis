"""Desk features 1,2,3,5,6,7,8,10. Run: .venv/bin/python tests/desk_test.py"""
import os, sys, tempfile, pathlib, time, types
T = tempfile.mkdtemp(prefix="dk-"); os.environ.update(XDG_CONFIG_HOME=T + "/x", JARVIS_DATA_DIR=T + "/d")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from jarvis import deskfeatures as D, skills, quiet, settings, unknown, config, machelper, vault, memory
R = []
def rec(n, ok): R.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n, flush=True)
config.DATA.mkdir(exist_ok=True)
D.record_sightings([{"name": "RESISTORS", "x": .1, "y": .7, "w": .1, "h": .1}, {"name": "PERSON", "x": .5, "y": .5, "w": .2, "h": .5}])
r = skills.handle("where did I put the resistors"); rec(f"1 desk memory: {r['speech']}", "resistors" in r["speech"] and "bottom left" in r["speech"] and "just now" in r["speech"])
rec("1 people are never remembered", D.where_is("person") is None)
rec("1 a map question is left alone ('where is the Eiffel Tower')", skills.desk_features_command("where is the Eiffel Tower") is None)
r = skills.handle("start build mode for the LED"); rec(f"2 build mode starts at step 1 ({r['speech'][:50]})", r["speech"].startswith("Step 1 of 5"))
rec("2 next step advances", skills.handle("next step")["speech"].startswith("Step 2 of 5"))
rec("2 check asks the camera (never answered locally)", skills.handle("check my step").get("deskfeature", {}).get("kind") == "build_check" and "CANNOT TELL" in D.build_check_prompt())
rec("2 stop ends it", "ended" in skills.handle("stop build mode")["speech"] and skills.desk_features_command("next step") is None)
rec("3 pinout parsing: good JSON kept, junk refused", D.parse_pins('x {"pins":["1: VCC","2: GND"]}') == ["1: VCC", "2: GND"] and D.parse_pins("I think pin 1") == [] and D.parse_pins('{"pins":[],"note":"not sure"}') == [])
D.sheet_save("LM358", ["1: OUT1"]); r = skills.handle("pinout of the lm358"); rec("3 saved pinouts answer offline with a datasheet warning", "datasheet" in r["speech"] and r["cards"])
rec("3 unknown part goes to the model (deskfeature)", skills.handle("pinout of the xyz123")["deskfeature"]["kind"] == "pinout")
rec("3 'pinout of this chip' reads the chip with the camera", skills.handle("show the pinout of this chip")["deskfeature"]["kind"] == "pinout_look")
vault.available = lambda: False; vault._queue_add = lambda x: None
r = skills.handle("log that I wired the LED to GPIO 17"); rec(f"5 project log line saved ({r['speech']})", "Logged" in r["speech"] or "asleep" in r["speech"])
rec("5 a secret in the log is refused", "don't store" in skills.handle("log that my password is hunter22")["speech"])
rec("6 'add this to my shopping list' uses the camera", skills.handle("add this to my shopping list")["deskfeature"]["kind"] == "shop_look")
rec("7 focus mode silences like quiet hours", (skills.handle("focus mode on"), quiet.active())[1] is True and (skills.handle("focus mode off"), quiet.active())[1] is False)
unknown.log("flibber the zorp"); unknown.log("flibber a gloop"); unknown.log("make coffee")
card, say = D.review(); rec(f"10 review groups the phrases ({say})", "flibber" in say and card["items"][0].startswith("2x"))
rec("10 review by voice", skills.handle("review what you didn't understand")["cards"])
called = []; machelper.ON_BACK = lambda n: called.append(n); machelper._st.update(state="asleep", t=0); settings.update(mac_helper_url="http://127.0.0.1:1")
machelper.url = lambda: "http://127.0.0.1:1"; machelper.request = lambda *a, **k: (200, {"ok": True, "busy": 0, "generators": []}); machelper._qload = lambda: [1, 2]
machelper.status(True); time.sleep(.2); rec("8 asleep Mac answering again triggers 'your Mac is back' with the queue size", called == [2])
print("RESULT", "ALL PASS" if all(R) else "FAILURES"); sys.exit(0 if all(R) else 1)
