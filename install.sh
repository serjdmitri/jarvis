#!/bin/sh
# Installs Jarvis into ./.venv and downloads the models. Needs no secrets and sends nothing about you anywhere.
# Run it from the project folder, which must be ~/jarvis (the Hyprland file refers to $HOME/jarvis):   git clone <this repo> ~/jarvis && cd ~/jarvis && ./install.sh
set -e
cd "$(dirname "$(readlink -f "$0")")"
echo "== python environment"
if command -v uv >/dev/null 2>&1; then uv venv --system-site-packages .venv && uv pip install --python .venv/bin/python -r requirements.txt
else python3 -m venv --system-site-packages .venv && .venv/bin/pip install -r requirements.txt; fi
mkdir -p models/kws data notes logs shots run
V=https://huggingface.co/rhasspy/piper-voices/resolve/main/en
fetch() { [ -s "$2" ] || { echo "   downloading $2"; curl -fL --retry 3 -o "$2" "$1"; }; }
echo "== voices (Piper, about 63 MB each)"
fetch $V/en_GB/alan/medium/en_GB-alan-medium.onnx models/en_GB-alan-medium.onnx
fetch $V/en_GB/alan/medium/en_GB-alan-medium.onnx.json models/en_GB-alan-medium.onnx.json
fetch $V/en_US/lessac/medium/en_US-lessac-medium.onnx models/en_US-lessac-medium.onnx
fetch $V/en_US/lessac/medium/en_US-lessac-medium.onnx.json models/en_US-lessac-medium.onnx.json
echo "== wake-word model (sherpa-onnx keyword spotter, about 5 MB, Apache-2.0)"
if [ ! -s models/kws/tokens.txt ]; then
  curl -fL --retry 3 -o /tmp/kws.tar.bz2 https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2
  tar -xjf /tmp/kws.tar.bz2 -C models/kws --strip-components=1 && rm -f /tmp/kws.tar.bz2
fi
echo "== speech recognition model: faster-whisper downloads 'tiny.en' (about 75 MB) the first time Jarvis starts"
echo
echo "Next:"
echo "  1. add this line to ~/.config/hypr/bindings.lua:   dofile(os.getenv(\"HOME\") .. \"/jarvis/hypr/jarvis.lua\")   then run: hyprctl reload"
echo "  2. install and log in to Claude Code once (run: claude)"
echo "  3. bin/jarvis-doctor        (checks everything)"
echo "  4. bin/jarvis-watch &       (starts Jarvis; it also starts at login after step 1)"
