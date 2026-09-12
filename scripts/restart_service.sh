#!/usr/bin/env bash
# Restarts the Portfolio Manager Download Watcher background service
set -e

PLIST_DEST="$HOME/Library/LaunchAgents/com.matthewhope.portfoliomanager.plist"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$PLIST_DEST" ]; then
    echo "🔄 Restarting Portfolio Manager Watcher Service..."
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
    sleep 1
    launchctl load -w "$PLIST_DEST"
    echo "✅ Service restarted with latest code."
    launchctl list | grep portfoliomanager || true
else
    echo "⚠️ Service plist not found at $PLIST_DEST. Running installer..."
    "$SCRIPT_DIR/install_service.sh"
fi
