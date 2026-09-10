#!/usr/bin/env bash
# ==============================================================================
# Portfolio Manager — Research Report Refresh Helper
#
# Examples:
#   ./scripts/refresh_reports.sh --triage                # Dry-run audit (free)
#   ./scripts/refresh_reports.sh --older-than 3          # Reports older than 3 days
#   ./scripts/refresh_reports.sh --older-than 7 --limit 10 # Weekly tranche
#   ./scripts/refresh_reports.sh --all                   # Complete sweep (all 60)
#   ./scripts/refresh_reports.sh --all --category etfs_index # Refresh index ETFs
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
VENV_PYTHON="$BASE_DIR/.venv/bin/python"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "Error: Virtual environment python not found at $VENV_PYTHON"
    exit 1
fi

"$VENV_PYTHON" "$BASE_DIR/main.py" --run-agents "$@"
