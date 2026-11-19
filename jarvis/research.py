"""Research mode: a background `claude -p` run (separate from the chat brain, so Jarvis stays usable) with WebSearch + WebFetch only.
It must finish in about 90 s. Afterwards THIS code checks every cited link against the URLs the search and fetch tools really returned or fetched, and the
report says "All sources verified" or lists the ones that were not seen, so an invented source is called out. The report is saved as a markdown note."""
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime

from . import brain, config

NOTES = config.ROOT / "notes"
HARD_CAP_S = 45
LAST = {"path": None, "title": None}

PROMPT = """Research this topic for the user: {topic}

Do real research: run several different WebSearch queries, open the best pages with WebFetch, and cross-check claims between at least two sources where you can.
Work fast: you have about 30 seconds in total. Run your WebSearch calls and your WebFetch calls IN PARALLEL (several tool calls in one step), not one after another, and stop as soon as two sources agree. Then reply with ONLY one JSON object, no other text:
{{"summary": "two short sentences to be read aloud", "findings": [{{"text": "one finding, one or two sentences", "refs": [1, 2], "confidence": "high|medium|low"}}], "sources": [{{"n": 1, "title": "page title", "url": "https://..."}}]}}
Rules: 4 to 7 findings; every finding cites at least one source number, and where you can, two different sources that agree; mark a finding "low" confidence if sources disagree or you only saw it once; ONLY cite pages you actually searched or fetched in this session; put the exact URL; never invent a source."""


def norm(u):
    p = urllib.parse.urlsplit((u or "").strip())
    host = (p.hostname or "").lower().removeprefix("www.")
    return host + p.path.rstrip("/") + (("?" + p.query) if p.query else "")


def seen_match(url, seen):
    n = norm(url)
    return any(n == s or (n and s and (n.startswith(s) or s.startswith(n)) and abs(len(n) - len(s)) < 40) for s in seen)


def run(topic, on_status=None, cap=HARD_CAP_S):
    """-> {"summary", "markdown", "path", "unverified": [...], "sources": [...], "error": str|None}. Blocking: call it from a thread."""
    on = on_status or (lambda lines: None)
    lines = [f"Researching: {topic[:60]}"]
    on(list(lines))
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose"] + brain.TOOL_FLAGS + [
        "--system-prompt", "You are a careful research assistant. Output only what is asked.", "--strict-mcp-config", "--disable-slash-commands", "--setting-sources", "project"]
    if config.CLAUDE_MODEL:
        cmd += ["--model", config.CLAUDE_MODEL]
    proc = subprocess.Popen(cmd + [PROMPT.format(topic=topic)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
                            cwd=config.RUN_DIR, env=brain.ENV)
    seen, result, t0 = set(), "", time.time()
    killer = threading.Timer(cap, lambda: proc.poll() is None and proc.kill())
    killer.start()
    try:
        for line in proc.stdout:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            t = e.get("type")
            if t == "assistant":
                for b in e["message"].get("content", []):
                    if b.get("type") == "tool_use":
                        inp = b.get("input", {})
                        if b["name"] == "WebFetch" and inp.get("url"):
                            seen.add(norm(inp["url"]))
                            lines.append("Reading: " + (urllib.parse.urlsplit(inp["url"]).hostname or "").removeprefix("www."))
                        elif inp.get("query"):
                            lines.append("Searching: " + inp["query"][:50])
                        on(lines[-5:])
            elif t == "user":                                              # what the tools actually returned: these are the only URLs that count as seen
                c = e.get("message", {}).get("content")
                if isinstance(c, list):
                    for b in c:
                        if b.get("type") == "tool_result":
                            body = b.get("content")
                            text = body if isinstance(body, str) else " ".join(x.get("text", "") for x in (body or []) if isinstance(x, dict))
                            for m in re.finditer(r"https?://[^\s\"'<>\]\\)]+", text):
                                seen.add(norm(m.group(0).rstrip(".,")))
            elif t == "result":
                result = (e.get("result") or "").strip()
    finally:
        killer.cancel()
        if proc.poll() is None:
            proc.kill()
    if not result:
        return {"error": "The research ran out of time before it had an answer.", "elapsed": time.time() - t0}
    m = re.search(r"\{.*\}", result, re.S)
    try:
        data = json.loads(m.group(0))
        summary = str(data["summary"]).strip()
        findings = [(str(f.get("text", "")).strip(), [int(r) for r in f.get("refs", []) if str(r).isdigit()], str(f.get("confidence", "medium")).lower()) for f in data.get("findings", []) if f.get("text")]
        sources = [{"n": int(s["n"]), "title": str(s.get("title", ""))[:120], "url": str(s.get("url", ""))} for s in data.get("sources", []) if s.get("url")]
    except Exception:
        return {"error": "The research came back in a form I could not read.", "elapsed": time.time() - t0}
    for s in sources:
        s["verified"] = s["url"].startswith("https://") and seen_match(s["url"], seen)
    unverified = [s for s in sources if not s["verified"]]
    on(lines[-3:] + ["Checking the claims against the sources"])
    findings = [(t, refs, ("low" if (conf == "low" or len(set(refs)) < 2) and True else conf), len(set(refs)) < 2) for t, refs, conf in findings]   # (text, refs, confidence, single_source)
    cited = {r for _, refs, _, _ in findings for r in refs}
    bad_refs = sorted(cited - {s["n"] for s in sources})          # a finding cites a number that is not in the sources list: also invented
    md = [f"# {topic}", f"*Researched {datetime.now().strftime('%Y-%m-%d %H:%M')}*", "", "## Summary", summary, "", "## Findings"]
    for i, (text, refs, conf, single) in enumerate(findings, 1):
        md.append(f"{i}. {text} " + " ".join(f"[{r}]" for r in refs) + ("  (UNCERTAIN: " + ("only one source" if single else "sources disagree or unclear") + ")" if conf == "low" else ""))
    md += ["", "## Sources"]
    for s in sources:
        md.append(f"[{s['n']}] {s['title']} - {s['url']}" + ("" if s["verified"] else "  (NOT SEEN by the search or fetch)"))
    md.append("")
    if not unverified and not bad_refs:
        md.append("All sources verified: every link above was returned by the search or fetched during this research.")
    else:
        md.append("WARNING, not verified: " + ", ".join(f"[{s['n']}] {s['url']}" for s in unverified) + (f"; findings cite missing source numbers {bad_refs}" if bad_refs else ""))
    markdown = "\n".join(md)
    NOTES.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:40] or "research"
    path = NOTES / f"research-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{slug}.md"
    path.write_text(markdown, encoding="utf-8")
    LAST.update(path=str(path), title=topic)
    report_card = {"type": "report", "title": "Research: " + topic[:50], "summary": summary, "full": False,
                   "findings": [{"n": i, "text": t, "refs": refs, "uncertain": conf == "low", "why": ("only one source" if single else "sources disagree or unclear") if conf == "low" else ""} for i, (t, refs, conf, single) in enumerate(findings, 1)],
                   "sources": [{"n": s["n"], "title": s["title"], "url": s["url"], "verified": s["verified"]} for s in sources],
                   "verified": ("All sources verified" if not unverified and not bad_refs else "Not verified: " + ", ".join(f"[{s['n']}]" for s in unverified))}
    LAST.update(summary=summary, link=(sources[0]["url"] if sources else None), links=[s["url"] for s in sources[:3] if s["verified"]])
    return {"summary": summary, "markdown": markdown, "card": report_card, "uncertain": sum(1 for f in findings if f[2] == "low"), "path": str(path), "sources": sources, "unverified": unverified, "bad_refs": bad_refs, "elapsed": time.time() - t0,
            "tools_seen": len(seen)}


def parse_command(text):
    m = re.search(r"\b(?:deep research|research)\s+(?:on |into |about |for )?(.+)$", text.strip(" .?!"), re.I)
    if m and len(m.group(1)) > 2:
        return {"topic": m.group(1).strip()}
    return None
