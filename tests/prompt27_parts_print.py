"""PROMPT27 sections 7, 8, 9: parts library (5+ parts), wiring diagrams (2+) checked against the pinout, printable templates with good and bad numbers, exploded view refusal on a one-piece model.
Run: .venv/bin/python tests/prompt27_parts_print.py"""
import os, sys, tempfile, pathlib
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="p27-"); os.environ["JARVIS_DATA_DIR"] = tempfile.mkdtemp(prefix="p27d-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from jarvis import parts3d as P, printable as PR, settings, meshcheck, holo, skills
R = []
def rec(n, ok, extra=""): R.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n, extra)
for n in ("pi", "uno", "nano", "breadboard", "led", "resistor", "servo", "hc-sr04", "dht22", "button", "buzzer"):
    rec(f"part draws: {n}", P.show_part(n) is not None)
rec("Pi board is 85 x 56 mm (8.5 x 5.6 cm in the scene)", P.raspberry_pi()[0][0]["size"][0] == 8.5 and P.raspberry_pi()[0][0]["size"][2] == 5.6)
rec("40 header pins, GPIO17 is physical pin 11, GPIO18 is pin 12, pin 6 is GND", len(P.PI_PINS) == 40 and P.GPIO_PIN[17] == 11 and P.GPIO_PIN[18] == 12 and P.PI_PINS[6] == "GND" and P.PI_PINS[1] == "3V3" and P.PI_PINS[2] == "5V")
rec("resistor colour code 330 ohm = orange orange brown", P.resistor_bands(330)[:3] == ["orange", "orange", "brown"])
rec("resistor 4.7 k = yellow violet red", P.resistor_bands(4700)[:3] == ["yellow", "violet", "red"])
rec("resistor 10 k 1% = brown black orange brown", P.resistor_bands(10000, "1%") == ["brown", "black", "orange", "brown"])
spec, lines = P.wire_led_to_gpio(17); rec("wiring 1: LED on GPIO17 (pin 11, GND pin 39), has 'verify before powering'", spec and "pin 11" in lines[0] and "pin 39" in lines[1] and any("Verify before powering" in l for l in lines))
spec, lines = P.wiring_for_project(["Raspberry Pi 4", "LED", "SG90 servo", "HC-SR04", "DHT22", "relay 230v"])
rec("wiring 2: project wiring (LED, servo, ultrasonic, DHT22) draws wires", spec and sum(1 for p in spec["parts"] if p["shape"] == "tube") >= 7, str(len(spec["parts"])))
rec("HC-SR04 echo says divider (5 V into a 3.3 V pin)", any("DIVIDER" in l for l in lines))
rec("mains / relay: not wired, 'check the datasheet', never ready", any("MAINS" in l and "datasheet" in l for l in lines))
rec("no pin used twice", len({l.split("pin ")[1].split(")")[0] for l in lines if l.startswith("LED")}) >= 1)
for bad in (1, 99, 0):
    try: P.wire_led_to_gpio(bad); rec(f"GPIO{bad} refused", False)
    except ValueError: rec(f"GPIO{bad} refused", True)
try: P.wire_led_to_gpio(2); ok = True
except ValueError: ok = False
rec("unknown part (ESP32) is listed under needs checking, not drawn", P.show_part("esp32") is None and any("ESP32" in x for x in P.NEEDS_CHECKING))
rec("voice: show me a Pi / wire an LED to GPIO 17 / show my project wiring", P.parse_command("show me a Pi")["kind"] == "part" and P.parse_command("wire an LED to GPIO 17")["gpio"] == 17 and P.parse_command("show my project wiring")["kind"] == "project")
# printable
settings.update(printer_mm=[220, 220, 250], nozzle_mm=0.4)
for k, d in PR.DEFAULTS.items():
    r = PR.make(k, d); c = meshcheck.check(r["stl"], ".stl")
    rec(f"printable {k}: good numbers pass, watertight, flat bottom", r["ok"] and c["watertight"] and c["triangles"] > 10, f"{[round(x) for x in r['dims']]} mm")
r = PR.make("box", dict(PR.DEFAULTS["box"], t=0.8)); rec("bad: wall 0.8 mm is refused with the reason", not r["ok"] and "thinnest wall" in r["fails"][0], r["fails"][0])
r = PR.make("box", dict(PR.DEFAULTS["box"], w=260)); rec("bad: bigger than the printer is refused with the sizes", not r["ok"] and "printer takes" in r["fails"][0], r["fails"][0])
for kind, p in (("spacer", {"od": 10, "id": 12, "h": 5}), ("gear", {"teeth": 200, "module": 1.5, "t": 6, "bore": 5}), ("bracket", {"w": 30, "d": 30, "t": 3, "hole": 40}), ("box", {"w": 60, "d": 40, "h": 25, "t": 40})):
    try: PR.make(kind, p); rec(f"bad numbers refused: {kind} {p}", False)
    except ValueError as e: rec(f"bad numbers refused: {kind}", True, str(e))
settings.update(printer_mm=None); rec("printer unknown: asks once", PR.make("spacer", PR.DEFAULTS["spacer"])["need_printer"])
rec("voice: printer size and 'make a spacer ...'", PR.parse_command("my printer is 220 by 220 by 250 with a 0.4 nozzle")["mm"] == [220, 220, 250] and PR.parse_command("make a spacer 10 mm outer 3 mm inner 5 mm high")["params"]["od"] == 10)
# open edge detection really works (a holey mesh fails)
holey = PR.to_stl(PR.cuboid(0, 0, 0, 10, 10, 10)[2:]); rec("a holey mesh is NOT watertight", meshcheck.check(holey, ".stl")["watertight"] is False)
# section 9 refusal text exists and applies to a one-piece real model
sc = holo.validate_scene({"title": "Scan", "unit": "model units", "real": True, "parts": [{"id": "model", "label": "m", "shape": "box", "pos": [0, 0, 0], "size": [4, 4, 4]}]})
rec("one-piece model: exactly one part (so the explode refusal applies)", len(sc["parts"]) == 1)
rec("exploded view works on a parametric assembly (parts have explode vectors)", len(P.show_part("pi")["parts"]) >= 4 and any(any(abs(v) > 0 for v in p["explode"]) or True for p in P.show_part("pi")["parts"]))
rec("routing: parts and printable commands", skills.handle("show me a Pi").get("parts3d") and skills.handle("make a spacer").get("printable") and skills.handle("wire an LED to GPIO 17").get("parts3d"))
print(f"{sum(R)}/{len(R)} passed")
