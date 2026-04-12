#!/usr/bin/env bash
set -euo pipefail

if command -v tteditor-uninstall >/dev/null 2>&1; then
    exec tteditor-uninstall "$@"
fi

APPLICATIONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_FILE="$APPLICATIONS_DIR/tteditor.desktop"

if [ -f "$DESKTOP_FILE" ]; then
    rm -f "$DESKTOP_FILE"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPLICATIONS_DIR"
fi

echo "Removed TeleText Editor desktop integration."
