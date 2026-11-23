"""The context builder: what the model gets with each question, and nothing more. Small and fast (measured: tests/context_test.py), assembled from local pieces only (no network on this path):
  - date, time, home area (skills.context_line)
  - the few remembered facts that match the question (memory.py) and the few matching snippets from the vault's local cache ("From your notes": the names are shown on screen)
  - the project in progress, and what is on screen: the map place, the camera view, the music
Persona and conversation history live in the model session itself; the available actions are listed in the intent prompt, so neither is repeated here."""
import time

from . import memory, settings

MAX_CHARS = 900
LAST = {"ms": 0.0, "notes": []}


def _screen(a):
    bits = []
    try:
        from . import maps
        if maps.LAST_SPEC["id"] and time.time() - maps.LAST_SPEC["t"] < 900:
            sp = maps.SPECS.get(maps.LAST_SPEC["id"]) or {}
            d = (sp.get("dest") or {}).get("label")
            if d:
                bits.append(f"map open: {d}" + (f" ({sp.get('mode', 'car')})" if sp.get("routes") else ""))
    except Exception:
        pass
    try:
        if a is not None and a.look.get("open"):
            bits.append("camera view open (" + (a.look.get("source") or "camera") + ")" + (f", boxes: {', '.join(b['name'].lower() for b in a.look.get('boxes', [])[:6])}" if a.look.get("boxes") else ""))
    except Exception:
        pass
    try:
        from . import music, spotify_cli
        it = spotify_cli.LAST.get("item")
        if it and time.time() - spotify_cli.LAST.get("t", 0) < 600:
            bits.append(f"music: {it.get('name')} by {it.get('artist')}")
        elif music.STATE.get("active"):
            bits.append("music playing (YouTube)")
    except Exception:
        pass
    return ("[on screen now: " + "; ".join(bits) + "]\n") if bits else ""


def _project():
    try:
        from . import projects
        names = [p.get("name", "") for p in projects.list_projects() if p.get("name")]
        return f"[projects: {', '.join(names[:3])}]\n" if names else ""
    except Exception:
        return ""


def build(question, a=None):
    """-> the text put in front of the question. Records how long it took (LAST['ms']) and which notes were used (LAST['notes'])."""
    t0 = time.perf_counter()
    from . import skills, vault
    parts = [skills.context_line(), memory.context(question)]
    notes_txt, titles = "", []
    try:
        notes_txt, titles = vault.context_fast(question)
    except Exception:
        pass
    parts += [notes_txt, _screen(a), _project()]
    out = "".join(parts)
    if len(out) > MAX_CHARS + 400:                                            # the date line is always kept; the rest is trimmed from the end
        head = parts[0]
        out = head + "".join(parts[1:])[:MAX_CHARS]
    LAST.update(ms=(time.perf_counter() - t0) * 1000, notes=titles)
    return out
