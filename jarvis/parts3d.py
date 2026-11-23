"""Real-sized parts for your projects, built from scene-spec JSON (the PROMPT20 hologram), no model files. Sizes are in cm in the scene (the real size in mm is in each part's label).
ONLY parts whose real dimensions and pinouts are well known are here (standard board outlines, the 40-pin header, the SG90 servo, the 5 mm LED, the resistor colour code, the half-size breadboard,
HC-SR04, DHT22). NOT included, listed in NEEDS_CHECKING: boards and modules whose size varies by maker. Wiring is checked against the pinout data; an unknown pin is never guessed: it says
"check the datasheet". Every wiring card carries "verify before powering"; a circuit with mains power is never shown as ready."""
import re

from . import holo

MM = 0.1                                     # scene units are cm
NEEDS_CHECKING = ["ESP32 dev boards (the size and pin layout differ between makers)", "DC motors and gear motors (many sizes)", "battery packs and holders (sizes differ)", "Arduino Uno header pin positions (shape known, exact offsets not verified)", "Raspberry Pi 5 extra connectors (board outline and GPIO header are the same as the Pi 4)"]

# physical pin -> name for the 40-pin Raspberry Pi header (same on Pi 2/3/4/5/Zero). BCM = the GPIO number.
PI_PINS = {1: "3V3", 2: "5V", 3: "GPIO2", 4: "5V", 5: "GPIO3", 6: "GND", 7: "GPIO4", 8: "GPIO14", 9: "GND", 10: "GPIO15", 11: "GPIO17", 12: "GPIO18", 13: "GPIO27", 14: "GND", 15: "GPIO22", 16: "GPIO23",
           17: "3V3", 18: "GPIO24", 19: "GPIO10", 20: "GND", 21: "GPIO9", 22: "GPIO25", 23: "GPIO11", 24: "GPIO8", 25: "GND", 26: "GPIO7", 27: "ID_SD", 28: "ID_SC", 29: "GPIO5", 30: "GND", 31: "GPIO6",
           32: "GPIO12", 33: "GPIO13", 34: "GND", 35: "GPIO19", 36: "GPIO16", 37: "GPIO26", 38: "GPIO20", 39: "GND", 40: "GPIO21"}
GPIO_PIN = {int(v[4:]): k for k, v in PI_PINS.items() if v.startswith("GPIO")}
RESERVED = {"ID_SD", "ID_SC"}
COLOR_DIGIT = ["black", "brown", "red", "orange", "yellow", "green", "blue", "violet", "grey", "white"]
SCENE_COLOR = {"black": "grey", "brown": "orange", "red": "red", "orange": "orange", "yellow": "yellow", "green": "green", "blue": "blue", "violet": "purple", "grey": "grey", "white": "white", "gold": "yellow"}


def resistor_bands(ohms, tolerance="5%"):
    """4-band colour code -> [colour names] (digits, multiplier, tolerance). Values from 10 ohm to 99 Mohm in the E-series digits."""
    if not 10 <= ohms <= 99_000_000_000:
        raise ValueError("value out of range")
    mult = 0
    v = float(ohms)
    while v >= 100:
        v /= 10
        mult += 1
    d = round(v)
    if d >= 100:
        d //= 10
        mult += 1
    tol = {"1%": "brown", "2%": "red", "5%": "gold", "10%": "silver"}.get(tolerance, "gold")
    return [COLOR_DIGIT[d // 10], COLOR_DIGIT[d % 10], COLOR_DIGIT[mult], tol]


def pi_pin_xy(pin):
    """Centre of a header pin on the Pi board outline (mm from the lower-left corner; the long edge is 85 mm). Pin 1 is at the end nearest the SD card, on the row away from the board edge."""
    col, row = (pin - 1) // 2, (pin - 1) % 2
    return 32.5 + (col - 9.5) * 2.54, 51.23 + row * 2.54


def _p(pid, label, shape, pos, size, color, desc="", parent=None, rot=(0, 0, 0)):
    return {"id": pid, "label": label, "desc": desc, "shape": shape, "pos": [round(x, 3) for x in pos], "size": [max(0.01, round(x, 3)) for x in size], "color": color, "rot": list(rot), "parent": parent}


def raspberry_pi(ox=0.0, oz=0.0, model="Pi 4 Model B"):
    """The board 85 x 56 mm, the 4 mounting holes' pattern 58 x 49 mm, the 40-pin header (2 x 20 pins at 2.54 mm), USB/Ethernet block, SD slot. -> parts (cm), the pin map {pin: (x, y, z) in scene cm}."""
    L, W, T = 8.5, 5.6, 0.16
    parts = [_p("pi_board", f"Raspberry {model} board 85 x 56 mm", "box", (ox, T / 2, oz), (L, T, W), "green", "The printed circuit board.")]
    parts.append(_p("pi_header", "GPIO header 2 x 20, 2.54 mm", "box", (ox + (32.5 - 42.5) * MM, T + 0.25, oz + (51.23 + 1.27 - 28) * MM), (5.08, 0.5, 0.5), "grey", "Pin 1 is at the SD-card end, on the inner row."))
    parts.append(_p("pi_usb", "USB and Ethernet", "box", (ox + 3.5, T + 0.8, oz + 0.0), (2.0, 1.6, 5.2), "white", "Right-hand end of the board."))
    parts.append(_p("pi_sd", "microSD slot", "box", (ox - 4.0, 0.05, oz), (1.2, 0.1, 1.2), "grey", "Underside, left-hand end."))
    pins = {}
    for n in range(1, 41):
        x, y = pi_pin_xy(n)
        pins[n] = (ox + (x - 42.5) * MM, T + 0.5, oz + (y - 28) * MM)
    return parts, pins


def breadboard(ox=0.0, oz=0.0):
    """Half-size breadboard 82.6 x 54.6 mm (400 tie points): 30 columns, rows a-e and f-j, power rails. -> parts and hole(col, row) -> position."""
    parts = [_p("bb_body", "Half-size breadboard 82.6 x 54.6 mm", "box", (ox, 0.25, oz), (8.26, 0.5, 5.46), "white", "Rows a-e are joined, rows f-j are joined, the centre gap splits them.")]
    def hole(col, row):
        if not (1 <= col <= 30 and row in "abcdefghij"):
            raise ValueError("no such hole")
        i = "abcdefghij".index(row)
        z = (-1.27 * (5 - i) if i < 5 else 1.27 * (i - 4)) - (0.38 if i < 5 else -0.38)
        return (ox + (col - 15.5) * 0.254, 0.5, oz + z + 0)
    return parts, hole


def led(pid, pos, color_name="red", parent=None):
    """5 mm LED: dome 5 mm, flange 5.8 mm, 8.6 mm tall, leads 2.54 mm apart; the LONG lead is the anode (+)."""
    x, y, z = pos
    return [_p(pid, f"{color_name.title()} LED 5 mm (long lead = +)", "cylinder", (x, y + 0.43, z), (0.5, 0.86, 0.5), color_name if color_name in holo.COLORS else "red", "Anode (long lead) to the resistor, cathode (short lead, flat side) to ground.", parent)]


def resistor(pid, ohms, pos, parent=None):
    """Axial resistor body 6.3 x 2.4 mm with the 4-band colour code."""
    bands = resistor_bands(ohms)
    x, y, z = pos
    out = [_p(pid, f"{ohms} ohm resistor 6.3 x 2.4 mm: " + ", ".join(bands), "cylinder", (x, y + 0.12, z), (0.24, 0.63, 0.24), "orange", "Bands: " + ", ".join(bands), parent, rot=(0, 0, 90))]
    for i, b in enumerate(bands):
        out.append(_p(f"{pid}_b{i}", f"band {i + 1}: {b}", "cylinder", (x + (i - 1.5) * 0.12, y + 0.12, z), (0.26, 0.04, 0.26), SCENE_COLOR.get(b, "yellow"), "", pid, rot=(0, 0, 90)))
    return out


def servo_sg90(pid, pos):
    """SG90 micro servo: body 22.8 x 12.2 x 22.7 mm. Wires: brown = ground, red = 5 V, orange = signal."""
    x, y, z = pos
    return [_p(pid, "SG90 servo 22.8 x 12.2 x 22.7 mm", "box", (x, y + 1.14, z), (2.28, 2.27, 1.22), "blue", "Wires: brown = GND, red = 5 V, orange = signal."),
            _p(pid + "_horn", "servo horn", "cylinder", (x - 0.55, y + 2.4, z), (0.5, 0.2, 0.5), "white", "", pid)]


def hcsr04(pid, pos):
    """HC-SR04 ultrasonic sensor 45 x 20 x 15 mm; pins VCC, TRIG, ECHO, GND. ECHO is 5 V: it needs a divider before a Pi pin."""
    x, y, z = pos
    return [_p(pid, "HC-SR04 ultrasonic sensor 45 x 20 x 15 mm", "box", (x, y + 0.75, z), (4.5, 1.5, 2.0), "blue", "Pins VCC, TRIG, ECHO, GND. ECHO is 5 V: use a voltage divider before a Pi pin.")]


def dht22(pid, pos):
    """DHT22 temperature and humidity sensor 15.1 x 25 x 7.7 mm; pins 1 VCC, 2 DATA, 3 not connected, 4 GND (a 10 k pull-up from DATA to VCC)."""
    x, y, z = pos
    return [_p(pid, "DHT22 sensor 15.1 x 25 x 7.7 mm", "box", (x, y + 0.4, z), (1.51, 0.77, 2.5), "white", "Pins: 1 VCC, 2 DATA, 3 not connected, 4 GND.")]


def button(pid, pos):
    x, y, z = pos
    return [_p(pid, "Tactile button 6 x 6 x 5 mm", "box", (x, y + 0.25, z), (0.6, 0.5, 0.6), "grey", "")]


def buzzer(pid, pos):
    x, y, z = pos
    return [_p(pid, "Active buzzer 12 mm", "cylinder", (x, y + 0.48, z), (1.2, 0.95, 1.2), "grey", "Long lead is +.")]


def arduino_nano(pid, pos):
    x, y, z = pos
    return [_p(pid, "Arduino Nano 45 x 18 mm", "box", (x, y + 0.08, z), (4.5, 0.16, 1.8), "blue", "Pin positions not drawn: check the datasheet.")]


def arduino_uno(pid, pos):
    x, y, z = pos
    return [_p(pid, "Arduino Uno R3 68.6 x 53.4 mm", "box", (x, y + 0.08, z), (6.86, 0.16, 5.34), "blue", "Pin positions not drawn: check the datasheet.")]


def wire(pid, a, b, color_name, label, lift=1.2):
    """A jumper wire as a tube between two points, arched up so it is visible."""
    mid = ((a[0] + b[0]) / 2, max(a[1], b[1]) + lift, (a[2] + b[2]) / 2)
    return _p(pid, label, "tube", (0, 0, 0), (0.06, 0.06, 0.06), SCENE_COLOR.get(color_name, color_name if color_name in holo.COLORS else "white"), label)


def _tube(pid, pts, color_name, label):
    p = _p(pid, label, "tube", (0, 0, 0), (0.07, 0.07, 0.07), SCENE_COLOR.get(color_name, color_name if color_name in holo.COLORS else "white"), label)
    p["points"] = [[round(c, 3) for c in q] for q in pts]
    return p


def show_part(name):
    """"show me a Pi" -> scene spec. name in pi, uno, nano, breadboard, led, resistor, servo, hcsr04, dht22, button, buzzer."""
    n = name.lower()
    if "pi" in n.split() or "raspberry" in n:
        parts, _ = raspberry_pi()
        title = "Raspberry Pi 4 Model B (85 x 56 mm)"
    elif "uno" in n:
        parts, title = arduino_uno("uno", (0, 0, 0)), "Arduino Uno R3"
    elif "nano" in n:
        parts, title = arduino_nano("nano", (0, 0, 0)), "Arduino Nano"
    elif "breadboard" in n:
        parts, _ = breadboard()
        title = "Half-size breadboard"
    elif "led" in n:
        parts, title = led("led", (0, 0, 0)), "5 mm LED"
    elif "resistor" in n:
        parts, title = resistor("res", 330, (0, 0, 0)), "330 ohm resistor"
    elif "servo" in n:
        parts, title = servo_sg90("servo", (0, 0, 0)), "SG90 servo"
    elif "ultrasonic" in n or "hc-sr04" in n or "hcsr04" in n:
        parts, title = hcsr04("us", (0, 0, 0)), "HC-SR04"
    elif "dht" in n or "temperature sensor" in n:
        parts, title = dht22("dht", (0, 0, 0)), "DHT22"
    elif "button" in n:
        parts, title = button("btn", (0, 0, 0)), "Tactile button"
    elif "buzzer" in n:
        parts, title = buzzer("bz", (0, 0, 0)), "Buzzer"
    else:
        return None
    return holo.validate_scene({"title": title, "unit": "cm", "parts": parts})


def wire_led_to_gpio(gpio, ohms=330, led_color="red"):
    """A real LED circuit on a Pi: GPIO -> resistor -> LED anode, LED cathode -> a ground pin. -> (scene spec, list of plain lines) or raises ValueError with the reason."""
    if gpio not in GPIO_PIN:
        raise ValueError(f"GPIO{gpio} is not on the 40-pin header.")
    name = PI_PINS[GPIO_PIN[gpio]]
    if name in RESERVED:
        raise ValueError(f"{name} is reserved for the Pi's own board ID.")
    parts, pins = raspberry_pi(-6.0, 0)
    bb, hole = breadboard(4.0, 0)
    gp, gnd = GPIO_PIN[gpio], 39 if gpio != 21 else 34
    r_a, r_b, l_a, l_b = hole(10, "e"), hole(14, "e"), hole(14, "d"), hole(16, "d")
    wires = [_tube("w_sig", [pins[gp], (pins[gp][0], 2.5, pins[gp][2]), (r_a[0], 2.5, r_a[2]), r_a], "yellow", f"{name} (pin {gp}) to the resistor"),
             _tube("w_gnd", [l_b, (l_b[0], 2.2, l_b[2]), (pins[gnd][0], 2.2, pins[gnd][2]), pins[gnd]], "grey", f"LED cathode to GND (pin {gnd})")]
    comps = resistor("r1", ohms, ((r_a[0] + r_b[0]) / 2, 0.5, r_a[2]), "bb_body") + led("led1", (l_a[0] + 0.254, 0.5, l_a[2]), led_color, "bb_body")
    spec = holo.validate_scene({"title": f"LED on {name}", "unit": "cm", "parts": parts + bb + comps + wires})
    lines = [f"{name} (physical pin {gp}) -> {ohms} ohm resistor ({', '.join(resistor_bands(ohms))}) -> LED long lead (+)", f"LED short lead (-) -> GND (physical pin {gnd})",
             f"Breadboard: resistor in column 10 and 14, row e; LED in column 14 and 16, row d.", "Verify before powering: check each wire against the pin numbers, and keep the resistor in the circuit."]
    return spec, lines


def wiring_for_project(parts_text):
    """From a project's parts (names) -> (scene spec or None, lines). Only parts with a known pinout are wired; any other part gets "check the datasheet" and no wire."""
    low = " ".join(parts_text).lower() if isinstance(parts_text, (list, tuple)) else str(parts_text).lower()
    lines, used, sp = [], set(), []
    pi, pins = raspberry_pi(-6.0, 0)
    bbp, hole = breadboard(4.0, 0)
    scene = pi + bbp
    def take(pin):
        if pin in used:
            raise ValueError(f"pin {pin} would be used twice")
        used.add(pin)
        return pin
    x = 8
    if re.search(r"\bleds?\b", low):
        g = take(GPIO_PIN[17])
        a, b = hole(x, "e"), hole(x + 4, "e")
        scene += resistor("r_led", 330, ((a[0] + b[0]) / 2, 0.5, a[2]), "bb_body") + led("led", (b[0], 0.5, hole(x + 4, "d")[2]), "red", "bb_body")
        sp += [_tube("w_led", [pins[g], (pins[g][0], 2.5, pins[g][2]), (a[0], 2.5, a[2]), a], "yellow", "GPIO17 to the LED resistor")]
        lines.append(f"LED: GPIO17 (pin {g}) -> 330 ohm -> LED +, LED - -> GND (pin 39)")
        used.add(39); x += 6
    if "servo" in low:
        g = take(GPIO_PIN[18])
        scene += servo_sg90("servo", (8.5, 0.5, -2.5))
        sp += [_tube("w_srv_sig", [pins[g], (pins[g][0], 3, pins[g][2]), (8.0, 3, -2.0), (8.0, 1.8, -2.0)], "orange", "servo signal to GPIO18"), _tube("w_srv_5v", [pins[2], (pins[2][0], 3.4, pins[2][2]), (8.3, 3.4, -2.0), (8.3, 1.8, -2.0)], "red", "servo 5 V"),
               _tube("w_srv_gnd", [pins[6], (pins[6][0], 2.8, pins[6][2]), (8.6, 2.8, -2.0), (8.6, 1.8, -2.0)], "grey", "servo GND")]
        lines.append(f"SG90 servo: signal (orange) -> GPIO18 (pin {g}), 5 V (red) -> pin 2, GND (brown) -> pin 6. A servo that moves a load needs its own 5 V supply: check the current.")
    if "dht22" in low or "dht 22" in low:
        g = take(GPIO_PIN[4])
        scene += dht22("dht", (6.0, 0.5, 3.0))
        sp += [_tube("w_dht", [pins[g], (pins[g][0], 3.2, pins[g][2]), (5.6, 3.2, 3.0), (5.6, 1.0, 3.0)], "green", "DHT22 data to GPIO4")]
        lines.append(f"DHT22: pin 1 VCC -> 3V3 (pin 1), pin 2 DATA -> GPIO4 (pin {g}) with a 10 k resistor from DATA to VCC, pin 4 GND -> pin 6.")
    if "ultrasonic" in low or "hc-sr04" in low or "hcsr04" in low:
        t, e = take(GPIO_PIN[23]), take(GPIO_PIN[24])
        scene += hcsr04("us", (6.5, 0.5, -3.0))
        sp += [_tube("w_trig", [pins[t], (pins[t][0], 3.6, pins[t][2]), (5.0, 3.6, -3.0), (5.0, 1.5, -3.0)], "yellow", "TRIG to GPIO23"), _tube("w_echo", [pins[e], (pins[e][0], 3.9, pins[e][2]), (5.6, 3.9, -3.0), (5.6, 1.5, -3.0)], "pink", "ECHO to GPIO24 through a divider")]
        lines.append(f"HC-SR04: VCC -> 5 V (pin 2), TRIG -> GPIO23 (pin {t}), ECHO -> GPIO24 (pin {e}) THROUGH A DIVIDER (1 k and 2 k resistors: ECHO is 5 V, a Pi pin is 3.3 V and can be damaged), GND -> pin 6.")
    unknown = [w for w in ("motor", "stepper", "relay", "esp32", "lcd", "oled", "camera", "battery", "mains", "230v", "120v") if w in low]
    for u in unknown:
        lines.append(f"{u}: pins and sizes not verified here, so no wire is drawn: check the datasheet." + (" MAINS POWER: never shown as ready; use a proper, isolated, certified module and ask someone qualified." if u in ("mains", "230v", "120v", "relay") else ""))
    if not sp and not lines:
        return None, ["I don't know how to wire those parts yet: check the datasheet."]
    lines.append("Verify before powering: check every wire against the pin numbers.")
    return holo.validate_scene({"title": "Project wiring", "unit": "cm", "parts": scene + sp}), lines


def parse_command(text):
    import re
    low = re.sub(r"\s+", " ", text.lower().strip(" .?!"))
    m = re.fullmatch(r"(?:please )?wire (?:up )?(?:an? |the )?(?:(\w+) )?led to (?:gpio ?|g p i o )?(\d{1,2})", low)
    if m:
        return {"kind": "led", "gpio": int(m.group(2)), "color": m.group(1) if m.group(1) in holo.COLORS else "red"}
    if re.fullmatch(r"(?:please )?(?:show|open) (?:me )?(?:my |the )?(?:project )?wiring(?: in 3d)?|(?:please )?show the wiring(?: in 3d)?", low):
        return {"kind": "project"}
    m = re.fullmatch(r"(?:please )?show me (?:a |an |the )?(pi|raspberry pi(?: 4)?|arduino uno|uno|arduino nano|nano|breadboard|led|resistor|servo|sg90|ultrasonic sensor|hc-sr04|dht22|temperature sensor|button|buzzer)(?: in 3d)?", low)
    if m:
        return {"kind": "part", "name": m.group(1)}
    return None
