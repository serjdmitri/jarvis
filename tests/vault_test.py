"""A8: the memory vault. The real Mac-helper code runs on 127.0.0.1 with a throwaway vault (200 fake notes) and the real Go client over the signed link.
Run: .venv/bin/python tests/vault_test.py"""
import json, os, sys, tempfile, threading, time, pathlib, shutil
T = tempfile.mkdtemp(prefix="vt-")
os.environ.update(JARVIS_HELPER_CFG=T + "/cfg.json", JARVIS_HELPER_DEST=T + "/dl", JARVIS_HELPER_CACHE=T + "/cache", JARVIS_HELPER_PORT="8792", JARVIS_VAULT=T + "/Jarvis Brain", JARVIS_VAULT_CFG=T + "/vault.json",
                  XDG_CONFIG_HOME=T + "/xdg", JARVIS_DATA_DIR=T + "/data")
KEY = bytes.fromhex("22" * 32); json.dump({"key": KEY.hex(), "topic": "t", "ack_topic": "a"}, open(T + "/cfg.json", "w"))
root = pathlib.Path(__file__).resolve().parent.parent; sys.path.insert(0, str(root)); sys.path.insert(0, str(root / "mac" / "helper"))
import jarvis_helper as H, vault as V
from http.server import ThreadingHTTPServer
def start(): s = ThreadingHTTPServer(("127.0.0.1", 8792), H.H); threading.Thread(target=s.serve_forever, daemon=True).start(); return s
srv = start()
from jarvis import machelper, mac, settings, vault as GV, memory, skills, config
mac._load = lambda: ("topic", KEY); settings.update(mac_helper_url="http://127.0.0.1:8792"); machelper.ok_url = lambda u: True
R = []
def rec(n, ok, extra=""): R.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n, extra, flush=True)
VR = T + "/Jarvis Brain"; V.ensure()
# a fake vault of 200 notes with links
for i in range(200):
    nm = f"Topic {i}"; links = " ".join(f"[[Topic {(i * 7 + k) % 200}]]" for k in range(1, 3))
    d = "Memory" if i % 3 == 0 else "Projects" if i % 3 == 1 else "Inbox"
    open(f"{VR}/{d}/{nm}.md", "w").write(f"---\ncreated: 2026-01-01\n---\n# {nm}\n\nthing number {i} about widgets and gadgets. {links}\n")
open(f"{VR}/Projects/Pi project.md", "w").write("---\ncreated: 2026-01-01\n---\n# Pi project\n\n- 2026-11-01: bought a Raspberry Pi 5\n- 2026-11-10: wired an LED to GPIO 17 [[Raspberry Pi 5]]\n")
os.makedirs(T + "/ext/Private", exist_ok=True); os.makedirs(T + "/ext/Notes", exist_ok=True)
open(T + "/ext/Notes/travel.md", "w").write("# travel\nplanning a trip to Lisbon with widgets"); open(T + "/ext/Private/diary.md", "w").write("# diary\nwidgets secret diary Lisbon")
json.dump({"read_roots": [T + "/ext"]}, open(T + "/vault.json", "w"))
GV.CACHE = pathlib.Path(T + "/vc.json"); GV.QUEUE = pathlib.Path(T + "/vq.jsonl"); config.DATA = pathlib.Path(T + "/data"); memory.PATH = pathlib.Path(T + "/mem.json")
machelper._st.update(state="unset", t=0)
rec("helper up: vault actions answer over the signed link", machelper.status(True) in ("ready", "busy"))
# --- write
r = GV.remember("my favourite editor is Neovim and I use it for the Pi project", title="Editor", tags=["preference"])
note = open(f"{VR}/Memory/Editor.md").read()
rec("remember: a Markdown note with frontmatter (created, source, confidence, tags)", r["ok"] and all(k in note for k in ("created:", "source:", "confidence:", "tags: [preference]")), r["speech"])
rec("it links to an existing note named in the text: [[Pi project]]", "[[Pi project]]" in note)
r = GV.remember("I run the Pi project on Tuesdays", kind="daily"); rec("daily log line appended", r["ok"] and os.path.exists(f"{VR}/Daily/{time.strftime('%Y-%m-%d')}.md"))
# --- search / read
s = GV.search("Neovim editor"); rec(f"search finds it ({s['from']}, {len(s['hits'])} hit)", s["hits"] and s["hits"][0]["title"] == "Editor")
rd = GV.read(s["hits"][0]["path"]); rec("read returns the note text", rd["ok"] and "Neovim" in rd["text"])
sp = skills.vault_command("where did I leave off on the Pi project"); rec(f"'where did I leave off on the Pi project': {sp['speech'][:80]}", "GPIO 17" in sp["speech"])
sp = skills.vault_command("show the note about neovim"); rec("'show the note about neovim' gives a card and says From your notes", sp and sp["speech"].startswith("From your notes") and sp["cards"])
# --- secrets filter, both layers
for bad in ("remember that my password is hunter22", "remember that my card number is 4111 1111 1111 1111", "remember that the api key is sk-abcdefghijklmnop1234", "remember that my wifi code is 482913", "remember my serial number is C02XG1234ABC"):
    r = skills.vault_command(bad); rec(f"secret refused: '{bad[:42]}...'", r and "don't store" in r["speech"].lower() and not os.path.exists(f"{VR}/Memory/{bad[:10]}.md"))
code, rr = V.handle("append", {"kind": "memory", "title": "x", "text": "my token is ghp_abcdefghij1234567890"}), None; rec("the Mac side refuses it too (second layer)", code["ok"] is False)
# --- sensitive needs a yes
r = skills.vault_command("remember that my doctor changed my medication"); rec("sensitive (health): asks first, nothing saved", r and r.get("expect") == "vault_ok" and not os.path.exists(f"{VR}/Memory/my doctor changed my medication.md"))
r2 = GV.remember("my doctor changed my medication", confirmed=True); rec("after a yes it is saved", r2["ok"])
# --- Private folder + traversal
s = GV.search("widgets Lisbon"); paths = [h["path"] for h in s["hits"]]
rec("enabled folder is searchable (read only), Private is never returned", any(p.startswith("ext0/Notes") for p in paths) and not any("Private" in p for p in paths), str(paths[:4]))
rec("reading a Private note is refused", not GV.read("ext0/Private/diary.md")["ok"] and not GV.read("../../etc/passwd")["ok"] and not GV.read("ext0/../../../etc/hosts")["ok"])
rec("Jarvis never writes into the read-only folder", not os.path.exists(T + "/ext/Notes/Editor.md") and V.handle("forget", {"path": "ext0/Notes/travel.md"})["ok"] is False)
# --- forget with undo
sp = skills.vault_command("forget the note about neovim"); rec(f"forget: moved to Trash, not deleted ({sp['speech'][:60]})", "Trash" in sp["speech"] and not os.path.exists(f"{VR}/Memory/Editor.md") and any(f.endswith("Editor.md") for f in os.listdir(f"{VR}/Trash")))
sp = skills.vault_command("bring it back"); rec("undo brings it back", "Brought back" in sp["speech"] and os.path.exists(f"{VR}/Memory/Editor.md"))
rec("forget cannot touch Daily or anything outside Jarvis's notes", V.handle("forget", {"path": f"Daily/{time.strftime('%Y-%m-%d')}.md"})["ok"] is False and V.handle("forget", {"path": "../cfg.json"})["ok"] is False)
# --- graph
g, err = GV.graph(); rec(f"graph: {len(g['nodes'])} of {g['total']} nodes, {len(g['edges'])} edges, never above 300", g and len(g["nodes"]) <= 300 and len(g["edges"]) > 150)
g2, _ = GV.graph("Pi project"); rec(f"'how does the Pi project connect': focus graph of {len(g2['nodes'])} nodes with the ghost note 'Raspberry Pi 5' shown", any(n["id"] == "Pi project" for n in g2["nodes"]) and any(n["folder"] == "ghost" for n in g2["nodes"]))
rec("new notes are flagged to glow", any(n["new"] for n in g["nodes"]))
sp = skills.vault_command("show my memory map"); rec("voice 'show my memory map' -> a memmap card", sp and sp["cards"][0]["type"] == "memmap")
# --- Mac asleep
GV.refresh_cache(); srv.shutdown(); srv.server_close(); machelper._st.update(state="asleep", t=time.time())
r = GV.remember("I prefer dark roast coffee"); rec(f"Mac asleep: saved locally and queued, said plainly ({r['speech'][-60:]})", r["ok"] and r.get("queued") and len(GV.queued()) == 1 and "asleep" in r["speech"])
s = GV.search("Pi project"); rec("Mac asleep: search answers from the small local cache", s["from"] == "cache")
srv = start(); machelper._st.update(state="unset", t=0); machelper.status(True); n = GV.flush()
rec("Mac back: the queued note reaches the vault", n == 1 and not GV.queued() and os.path.exists(f"{VR}/Memory/I prefer dark roast coffee.md"))
# --- nothing outside the Jarvis folders
top = sorted(x for x in os.listdir(VR) if not x.startswith("."))
rec(f"the vault only holds Jarvis's folders {top}", set(top) <= {"Memory", "Daily", "Projects", "Inbox", "Trash"})
print("RESULT", "ALL PASS" if all(R) else "FAILURES"); shutil.rmtree(T, ignore_errors=True); sys.exit(0 if all(R) else 1)
