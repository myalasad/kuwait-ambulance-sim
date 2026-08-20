#!/bin/bash
# Install a macOS LaunchAgent so the dashboard auto-starts at login and is
# restarted by launchd whenever it dies.  Run once:
#
#   ./scripts/install_autostart.sh
#
# Remove with:
#   launchctl unload ~/Library/LaunchAgents/com.kuwait.ambulance-sim.plist
#   rm ~/Library/LaunchAgents/com.kuwait.ambulance-sim.plist
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.kuwait.ambulance-sim.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.kuwait.ambulance-sim</string>
  <key>ProgramArguments</key>
  <array>
    <string>$ROOT/.venv/bin/python</string>
    <string>$ROOT/run_live.py</string>
    <string>--port</string><string>8642</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$ROOT/data/server.log</string>
  <key>StandardErrorPath</key><string>$ROOT/data/server.log</string>
</dict>
</plist>
EOF
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Installed and started: $PLIST"
echo "The dashboard now auto-starts at login and auto-restarts on crash."
