"""Settings that stick: values survive a (simulated) restart, a persona switch, an update; backup before migration; export has no secrets; import validates; reset one.
Run: .venv/bin/python tests/settings_test.py"""
import json, os, sys, tempfile, pathlib, importlib
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="settest-"); os.environ["JARVIS_DATA_DIR"] = tempfile.mkdtemp(prefix="setdata-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from jarvis import settings, settingsio, prefs
R = []
def rec(n, ok, extra=""): R.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n, extra)
settingsio.EXPORT_DIR = pathlib.Path(tempfile.mkdtemp(prefix="setexp-"))
prefs_set = lambda k, v: settings.update(**{k: prefs.clean(k, v)})
settings.update(speed_jarvis=1.6, speed_rose=1.6, speed_sage=1.45, volume=35, whisper=False, listen_patience="fast", ntfy_topic="SECRETTOPIC", finnhub_key="KEY123", spotify_client_id="abc", home_area="Test Town", sec_contact="me@example.com")
rec("speed 1.6x per persona accepted by the setting rules", prefs.clean("speed", 1.6) == 1.6 and prefs.clean("speed", 2.0) == 2.0)
note = settingsio.ensure_schema(); rec("first run versions the file and keeps a backup", "backup" in note and list(settings.PATH.parent.glob("settings.json.bak-*")), note)
rec("schema stored", settings.load().get("schema") == settingsio.SCHEMA)
settings._cache = None; importlib.reload(settings)               # a "restart": everything is read back from disk
rec("values survive a restart", settings.get("speed_jarvis") == 1.6 and settings.get("speed_rose") == 1.6 and settings.get("volume") == 35 and settings.get("listen_patience") == "fast")
settings.update(persona="sage"); settings.update(persona="jarvis")
rec("values survive persona switches (speed per persona stays)", settings.get("speed_jarvis") == 1.6 and settings.get("speed_sage") == 1.45)
rec("a second ensure_schema does not make another backup", settingsio.ensure_schema() == "")
settings.update(volume=500, speed_page=9.0)
pr = settingsio.problems(); rec("invalid values are REPORTED, not dropped", any(p.startswith("volume") for p in pr) and any(p.startswith("speed_page") for p in pr) and settings.get("volume") == 500, str(pr))
settings.update(volume=35); settings.update(**{"speed_page": 1.0})
p = settingsio.export(); d = json.load(open(p))
rec("export has no secrets", not any(k for k in d if "key" in k or "topic" in k or "contact" in k) and "SECRETTOPIC" not in open(p).read() and "KEY123" not in open(p).read(), str(sorted(d))[:120])
rec("export file mode 600", oct(os.stat(p).st_mode & 0o777) == "0o600")
bad = tempfile.mktemp(suffix=".json"); json.dump({"speed_jarvis": 1.3, "volume": 999, "finnhub_key": "evil", "ntfy_topic": "x", "rm -rf": 1, "listen_patience": "slow", "whisper": True}, open(bad, "w"))
ok, rej = settingsio.import_file(bad)
rec("import applies only valid known settings", sorted(ok) == ["speed_jarvis", "whisper"] and settings.get("speed_jarvis") == 1.3 and settings.get("whisper") is True, str(ok))
rec("import rejects bad values, secrets and unknown keys one by one", len(rej) == 5 and settings.get("finnhub_key") == "KEY123" and settings.get("volume") == 35, str(rej))
open(bad, "w").write("not json"); rec("import of a garbage file is refused", settingsio.import_file(bad)[0] == [])
gone = settingsio.reset_one("speed"); rec("reset one setting (speed) removes the speed values only", "speed_jarvis" in gone and settings.get("speed_jarvis") is None and settings.get("volume") == 35 and settings.get("home_area") == "Test Town", str(gone))
rec("parse: export / import / reset", settingsio.parse_command("export my settings")["kind"] == "export" and settingsio.parse_command("reset the volume setting")["name"] == "volume")
print(f"{sum(R)}/{len(R)} passed")
