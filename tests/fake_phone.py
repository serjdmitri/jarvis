"""A fake DroidCam for tests: python tests/fake_phone.py PORT [single]   MJPEG at /video; the first frames are dark (like a real phone), and in "single" mode a second viewer is refused."""
import subprocess, sys, tempfile, threading, time, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
d = tempfile.mkdtemp()
def mk(name, color, text):
    subprocess.run(["magick", "-size", "640x480", f"gradient:{color}-white", "-fill", "#c33", "-draw", "rectangle 100,120 300,360", "-fill", "#fc3", "-draw", "circle 460,240 460,340", "-pointsize", "30", "-fill", "black", "-annotate", "+20+50", text, "-quality", "85", f"{d}/{name}.jpg"], check=True)
mk("dark", "#050505", "dark"); mk("good", "#335577", "FAKE PHONE")
DARK, GOOD = open(f"{d}/dark.jpg", "rb").read(), open(f"{d}/good.jpg", "rb").read()
STATE = {"viewers": 0, "max": 0, "refused": 0, "single": len(sys.argv) > 2}
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/stats":
            b = f'{{"max": {STATE["max"]}, "refused": {STATE["refused"]}, "viewers": {STATE["viewers"]}}}'.encode(); self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
        if not self.path.startswith("/video"):
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
        if STATE["single"] and STATE["viewers"] >= 1:
            STATE["refused"] += 1; self.send_response(503); self.send_header("Content-Length", "0"); self.end_headers(); return
        STATE["viewers"] += 1; STATE["max"] = max(STATE["max"], STATE["viewers"])
        try:
            self.send_response(200); self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame"); self.end_headers()
            n = 0
            while True:
                f = DARK if n < 5 else GOOD; n += 1
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(f)).encode() + b"\r\n\r\n" + f + b"\r\n"); self.wfile.flush(); time.sleep(0.1)
        except Exception:
            pass
        finally:
            STATE["viewers"] -= 1
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
