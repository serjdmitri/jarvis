#!/bin/sh
# Remove the Jarvis Mac helper completely (the files in Downloads/Jarvis stay).
PL="$HOME/Library/LaunchAgents/local.jarvis.helper.plist"
launchctl unload "$PL" 2>/dev/null; rm -f "$PL"; rm -rf "$HOME/.jarvis-helper"
echo "Jarvis helper removed."
