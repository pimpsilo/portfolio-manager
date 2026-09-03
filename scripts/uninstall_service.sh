#!/usr/bin/env bash
# Uninstalls the Portfolio Manager Download Watcher service

PLIST_DEST="$HOME/Library/LaunchAgents/com.matthewhope.portfoliomanager.plist"

echo "🛑 Stopping and uninstalling Portfolio Manager Watcher Service..."

if [ -f "$PLIST_DEST" ]; then
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
    rm -f "$PLIST_DEST"
    echo "✓ Service stopped and removed from ~/Library/LaunchAgents."
else
    echo "Service is not currently installed."
fi
