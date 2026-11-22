"""Ask the brain for STRUCTURED data (a JSON object or list) and validate it in code before anything uses it. The brain never runs anything: this only parses text.
web=True lets it use WebSearch + WebFetch (read-only). A bad or missing answer is None, never a guess."""
import json
import re
import subprocess

from . import brain, config


def _extract(text):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I | re.M).strip()
    for opener_, closer in (("{", "}"), ("[", "]")):
        a, b = t.find(opener_), t.rfind(closer)
        if a >= 0 and b > a:
            try:
                return json.loads(t[a:b + 1])
            except ValueError:
                continue
    return None


def ask_json(prompt, system="You answer with ONE valid JSON value and nothing else.", web=False, timeout=70):
    cmd = ["claude", "-p", "--output-format", "json"] + (brain.TOOL_FLAGS if web else ["--tools", ""]) + brain._base_flags(system)
    try:
        r = subprocess.run(cmd + [prompt], capture_output=True, text=True, timeout=timeout, cwd=config.RUN_DIR, env=brain.ENV)
        data = json.loads(r.stdout)
        if data.get("is_error"):
            return None
        return _extract(data.get("result"))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
