"""Brain: the `claude` CLI on your existing login (no API key).

StreamBrain  - ONE long-lived `claude -p --input-format stream-json` process: no startup per turn,
               replies stream token by token, screenshots go in as inline base64 images.
OneShotBrain - the original `claude -p` per turn with --resume (kept as a fallback / for benchmarks).
Both keep conversation context across turns."""
import base64
import json
import os
import queue
import subprocess
import threading
import time
import uuid

from . import config

ENV = {**os.environ, "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
# Only web search + fetch. No file edits, no shell. dontAsk = anything not allowed is refused immediately, never a prompt that blocks.
TOOL_FLAGS = ["--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch,WebFetch", "--permission-mode", "dontAsk"]
TOOL_CAP_S = float(os.environ.get("JARVIS_TOOL_CAP_S", "12"))     # after this long in tool use: stop researching, answer with what was found
STALL_S = float(os.environ.get("JARVIS_STALL_S", "30"))           # no event at all for this long: the process is stuck
WRAPUP = "Time is up. Stop researching and answer now, in one or two short spoken sentences, using only what you have found so far."


def _base_flags(system):
    f = ["--system-prompt", system, "--strict-mcp-config", "--disable-slash-commands",
         "--setting-sources", "project"]
    if config.CLAUDE_MODEL:
        f += ["--model", config.CLAUDE_MODEL]
    return f


class StreamBrain:
    def __init__(self, system=None):
        self.system = system or config.SYSTEM_PROMPT
        config.RUN_DIR.mkdir(exist_ok=True)
        self.proc = self.q = None
        self.session = None
        self.lock = threading.Lock()

    def _spawn(self):
        cmd = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json",
               "--verbose", "--include-partial-messages"] + TOOL_FLAGS + _base_flags(self.system)
        if self.session:
            cmd += ["--resume", self.session]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1,
                                     cwd=config.RUN_DIR, env=ENV)
        self.q = queue.Queue()
        threading.Thread(target=self._read, args=(self.proc, self.q), daemon=True).start()

    @staticmethod
    def _read(proc, q):
        for line in proc.stdout:
            q.put(line)
        q.put(None)

    def _kill(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        self.proc = None

    def reset(self):
        with self.lock:
            self._kill()
            self.session = None

    def set_system(self, system):
        """New persona: a resumed session keeps answering as the old one, so start a fresh session."""
        with self.lock:
            self._kill()
            self.session = None
            self.system = system

    def close(self):
        with self.lock:
            self._kill()

    def warm(self):
        """Boot the process and run one tiny turn so the first real turn is already warm."""
        try:
            self.ask("Reply with only the word: ready")
        except Exception as e:
            print("brain warm-up failed:", e)

    def _write(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def ask(self, text, image=None, on_delta=None, on_event=None, timeout=90):
        fake = os.environ.get("JARVIS_FAKE_BRAIN_DOWN")
        if fake and os.path.exists(fake):                           # tests only: a flag file makes the brain "stop responding"
            raise RuntimeError("the claude process exited. (test)")
        """on_event(kind, detail): 'tool' (starting a WebSearch/WebFetch), 'tool_done', 'cap' (tool-time cap hit), 'text'."""
        content = text
        if image:
            with open(image, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                       {"type": "text", "text": text}]
        ev = on_event or (lambda *a: None)
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._spawn()
            t0 = time.time()
            last_event = t0
            t_tool = None                  # when the first tool call of this question started
            capped = wrapped = False
            text_out = ""
            self.activity = []             # what the brain was doing (kept for error messages / logs)

            def note(msg):
                self.activity.append(f"{time.time() - t0:5.1f}s {msg}")
                print("[brain]", self.activity[-1], flush=True)
            try:
                self._write({"type": "user", "message": {"role": "user", "content": content}})
                while True:
                    try:
                        line = self.q.get(timeout=1.0)
                    except queue.Empty:
                        now = time.time()
                        if t_tool and not capped and now - t_tool > TOOL_CAP_S:
                            capped = True
                            note(f"tool-time cap ({TOOL_CAP_S:.0f}s) reached: interrupting and asking for an answer")
                            ev("cap", "out of time, answering with what I have")
                            self._write({"type": "control_request", "request_id": f"int_{int(now)}", "request": {"subtype": "interrupt"}})
                        elif now - last_event > STALL_S:
                            note(f"NO EVENTS for {STALL_S:.0f}s: process is stuck")
                            raise RuntimeError("the brain stopped responding (no activity for %d seconds). Last activity: %s"
                                               % (STALL_S, "; ".join(self.activity[-3:]) or "none"))
                        elif now - t0 > timeout + TOOL_CAP_S + 20:
                            raise RuntimeError("the brain took too long. Activity: " + "; ".join(self.activity[-4:]))
                        continue
                    if line is None:
                        raise RuntimeError("the claude process exited. Activity: " + "; ".join(self.activity[-3:]))
                    e = json.loads(line)
                    t = e.get("type")
                    if t in ("stream_event", "assistant", "user", "result"):
                        last_event = time.time()                   # real progress only: the CLI's silent network retries must not reset the stall timer
                    if t == "stream_event":
                        d = e["event"]
                        if d.get("type") == "content_block_delta" and d["delta"].get("type") == "text_delta":
                            text_out += d["delta"]["text"]
                            if on_delta:
                                on_delta(d["delta"]["text"])
                    elif t == "assistant":
                        for blk in e["message"].get("content", []):
                            if blk.get("type") == "tool_use":
                                if t_tool is None:
                                    t_tool = time.time()
                                inp = blk.get("input", {})
                                detail = inp.get("query") or inp.get("url") or ""
                                note(f"TOOL {blk['name']}: {detail[:120]}")
                                ev("tool", f"{blk['name']}|{detail}")
                    elif t == "user":
                        c = e.get("message", {}).get("content")
                        if isinstance(c, list) and any(b.get("type") == "tool_result" for b in c):
                            note("tool result received")
                            ev("tool_done", "")
                    elif t == "result":
                        self.session = e.get("session_id") or self.session
                        if capped and not wrapped and e.get("subtype") == "error_during_execution":
                            # the interrupted turn ended; now ask for the answer from what it has
                            text_out, wrapped = "", True
                            self._write({"type": "user", "message": {"role": "user", "content": WRAPUP}})
                            note("interrupted; wrap-up requested")
                            continue
                        if e.get("is_error"):
                            raise RuntimeError(str(e.get("result") or e.get("subtype"))[:300])
                        note("answer complete")
                        return (e.get("result") or text_out or "").strip()
            except Exception:
                self._kill()   # next turn respawns and resumes the session
                raise


class OneShotBrain:
    def __init__(self, system=None):
        self.system = system or config.SYSTEM_PROMPT
        config.RUN_DIR.mkdir(exist_ok=True)
        self.session = None

    def reset(self):
        self.session = None

    def set_system(self, system):
        self.session, self.system = None, system

    def close(self):
        pass

    def warm(self):
        pass

    def ask(self, text, image=None, on_delta=None, on_event=None, timeout=120):
        prompt = text
        if image:
            prompt = f"{text}\n\n[Screenshot of my screen saved at {image}; read it with the Read tool.]"
        cmd = ["claude", "-p", "--output-format", "json", "--tools", "Read" if image else ""] + _base_flags(self.system)
        if image:
            cmd += ["--allowedTools", "Read", "--add-dir", str(config.SHOTS_DIR)]
        if self.session:
            cmd += ["--resume", self.session]
        else:
            new = str(uuid.uuid4())
            cmd += ["--session-id", new]
        r = subprocess.run(cmd + [prompt], capture_output=True, text=True, timeout=timeout,
                           cwd=config.RUN_DIR, env=ENV)
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            raise RuntimeError((r.stderr or r.stdout or "claude produced no output").strip()[:500])
        if data.get("is_error"):
            raise RuntimeError(str(data.get("result"))[:500])
        self.session = data.get("session_id") or self.session or new
        out = data["result"].strip()
        if on_delta:
            on_delta(out)   # no streaming in this mode: everything arrives at once
        return out


def make_brain(system=None):
    return OneShotBrain(system) if config.BRAIN == "oneshot" else StreamBrain(system)


Brain = make_brain
