#!/bin/sh
# Jarvis Mac helper: ONE install command. Silent: a user LaunchAgent (no window, no Dock icon, no menu bar item); it starts at login and restarts if it dies.
# Run on the Mac from this folder:   sh install.sh
set -e
D="$HOME/.jarvis-helper"; mkdir -p "$D" && chmod 700 "$D"
cp "$(dirname "$0")/jarvis_helper.py" "$(dirname "$0")/meshcheck.py" "$(dirname "$0")/vault.py" "$D/"
cp "$(dirname "$0")/../jarvis_pair.py" "$D/" 2>/dev/null || true
PL="$HOME/Library/LaunchAgents/local.jarvis.helper.plist"; mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PL" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>local.jarvis.helper</string>
<key>ProgramArguments</key><array><string>/usr/bin/python3</string><string>$D/jarvis_helper.py</string><string>run</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ProcessType</key><string>Background</string>
<key>StandardOutPath</key><string>/dev/null</string><key>StandardErrorPath</key><string>/dev/null</string>
</dict></plist>
EOF
launchctl unload "$PL" 2>/dev/null || true
launchctl load "$PL"
echo "Jarvis helper installed (silent). Pair once first if you have not:  python3 jarvis_inbox.py pair"
