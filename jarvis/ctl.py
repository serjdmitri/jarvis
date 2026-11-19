"""Tiny client: send one command to the running jarvis app. Stdlib only."""
import socket
import sys

import os
SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "jarvis.sock")


def send(cmd):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(30 if cmd.startswith(("dictate file", "act ", "camera")) else 2)      # a few commands do real work before they answer
    s.connect(SOCK)
    s.sendall(cmd.encode() + b"\n")
    try:
        return s.recv(4096).decode().strip()
    finally:
        s.close()


if __name__ == "__main__":
    try:
        print(send(" ".join(sys.argv[1:]) or "status"))
    except OSError as e:
        print(f"jarvis not running ({e})", file=sys.stderr)
        sys.exit(1)
