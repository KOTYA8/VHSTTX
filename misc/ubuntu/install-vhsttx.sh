#!/usr/bin/env sh
set -eu

if command -v vhsttx-install >/dev/null 2>&1; then
    exec vhsttx-install "$@"
fi

if ! command -v vhsttx >/dev/null 2>&1; then
    echo "Warning: vhsttx is not on PATH. Install the package first, for example with pipx install -e .[qt]." >&2
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DATA_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}
APPLICATIONS_DIR="$DATA_HOME/applications"
ICON_DIR="$DATA_HOME/icons/hicolor/512x512/apps"
ICON_SOURCE="$SCRIPT_DIR/../../teletext/gui/vhsttxgui.png"

mkdir -p "$APPLICATIONS_DIR" "$ICON_DIR"

cp "$SCRIPT_DIR/vhsttx.desktop" "$APPLICATIONS_DIR/vhsttx.desktop"
cp "$ICON_SOURCE" "$ICON_DIR/vhsttxgui.png"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPLICATIONS_DIR"
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$DATA_HOME/icons/hicolor" >/dev/null 2>&1 || true
fi

echo "Installed VHSTTX desktop integration."
