"""Step 1: speaker + mic self-test.  Usage: .venv/bin/python -m jarvis.selftest"""
import subprocess
import tempfile
import time

from . import audio


def main():
    tone = tempfile.mktemp(suffix=".wav")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=660:duration=0.8", "-af", "volume=0.3", "-ar", "48000", tone], check=True)
    print("speaker: playing tone ...")
    audio.play(tone)
    print("mic: SPEAK NOW for 3 seconds ...")
    r = audio.Recorder()
    r.start()
    time.sleep(3)
    path = r.stop()
    rms, peak, clip, secs = audio.level(path)
    print(f"mic: {secs:.1f}s  rms={rms:.1f} dBFS  peak={peak:.1f} dBFS  clipped={clip*100:.2f}%")
    if clip > 0.01 or peak > -1:
        print("  -> TOO HOT: lower gain (wpctl set-volume @DEFAULT_AUDIO_SOURCE@ 0.5)")
    elif rms < -55:
        print("  -> very quiet / silent: raise gain or check mic mute")
    else:
        print("  -> level OK")
    print("speaker: playing back your recording ...")
    audio.play(path)


if __name__ == "__main__":
    main()
