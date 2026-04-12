#!/usr/bin/env bash
set -euo pipefail

if command -v tteditor-install >/dev/null 2>&1; then
    exec tteditor-install "$@"
fi

if ! command -v tteditor >/dev/null 2>&1; then
    echo "Warning: tteditor is not on PATH. Install the package first, for example with pipx install -e .[qt]." >&2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPLICATIONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

mkdir -p "$APPLICATIONS_DIR"
cp "$SCRIPT_DIR/tteditor.desktop" "$APPLICATIONS_DIR/tteditor.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPLICATIONS_DIR"
fi

echo "Installed TeleText Editor desktop integration."
