#!/usr/bin/env bash
# Installs and starts the Portfolio Manager Download Watcher as a background macOS service

PLIST_SRC="/Users/matthewhope/github_projects/portfolio-manager/scripts/com.matthewhope.portfoliomanager.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.matthewhope.portfoliomanager.plist"
LOG_DIR="/Users/matthewhope/github_projects/portfolio-manager/logs"

echo "📦 Installing Portfolio Manager 24/7 Watcher Service..."

# 1. Ensure directories exist
mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$LOG_DIR"

# 2. Unload existing service if currently running
if launchctl list | grep -q "com.matthewhope.portfoliomanager"; then
    echo "🔄 Unloading existing service instance..."
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
fi

# 3. Copy plist into LaunchAgents
cp "$PLIST_SRC" "$PLIST_DEST"

# 4. Load and start the service
launchctl load -w "$PLIST_DEST"

echo ""
echo "✅ Watcher service installed and running in the background!"
echo "   It will automatically launch whenever your Mac starts."
echo "   Logs: $LOG_DIR/watcher.log"
echo ""
echo "To check live status, run:"
echo "   tail -f $LOG_DIR/watcher.log"
