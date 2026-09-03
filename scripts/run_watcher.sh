#!/usr/bin/env bash
# Runs the Portfolio Manager Download Watcher in the background or terminal

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$DIR/.venv/bin/python"

echo "👀 Starting Portfolio Manager Download Watcher..."
echo "Monitoring ~/Downloads for new Fidelity CSV exports..."
exec "$PYTHON" "$DIR/main.py" --watch
