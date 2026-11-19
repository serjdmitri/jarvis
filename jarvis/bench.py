"""Latency benchmark: 'speaks' a question into the mic with piper, holds/releases like a person, and
reports per-stage timings.  Usage: [JARVIS_* env] .venv/bin/python -m jarvis.bench [runs] [tail_s]"""
import statistics
import subprocess
import sys
import threading
import time

from . import config, core, tts

QUESTIONS = ["What is the capital of Australia?", "Give me a quick fun fact about octopuses.",
             "How many days are there in a leap year?"]


def main():
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    tail = float(sys.argv[2]) if len(sys.argv) > 2 else 0.6   # how long you hold after finishing a sentence
    done, results = threading.Event(), []
    a = core.Assistant(lambda s, d="": done.set() if s == "idle" and a.turn_busy else None,
                       lambda r, t: print(f"   {r}: {t}") if r != "sys" else None,
                       on_timing=lambda m: results.append(m))
    a.turn_busy = False
    print(f"config: brain={config.BRAIN} model={config.CLAUDE_MODEL or 'default'} whisper={config.WHISPER_MODEL} "
          f"speculate={config.SPECULATE} ack={config.ACK} tail={tail}s")
    a.preload()
    while a.state != "idle":
        time.sleep(0.2)
    a.brain_ready.wait(60)   # let the brain's warm-up turn finish
    for i in range(runs):
        q = QUESTIONS[i % len(QUESTIONS)]
        tts.synth(q, "/tmp/bench-q.wav")
        done.clear(); a.turn_busy = False
        a.press(); time.sleep(0.4)
        subprocess.run(["pw-play", "--volume", "3", "/tmp/bench-q.wav"])   # louder: stands in for a close talker; time.sleep(tail)
        a.turn_busy = True
        a.release()
        done.wait(90); a.speaker.wait(); time.sleep(1)
    keys = ["transcript", "first_token", "first_sentence", "first_audio", "done"]
    print("\nmedian seconds after release (n=%d): " % len(results) +
          "  ".join(f"{k}={statistics.median(r[k] for r in results if k in r):.2f}" for k in keys))
    a.brain.close()


if __name__ == "__main__":
    main()
