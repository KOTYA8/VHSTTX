#!/usr/bin/env sh
set -eu

if command -v vhsttx-uninstall >/dev/null 2>&1; then
    exec vhsttx-uninstall "$@"
fi

DATA_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}
APPLICATIONS_DIR="$DATA_HOME/applications"
ICON_DIR="$DATA_HOME/icons/hicolor/512x512/apps"

rm -f "$APPLICATIONS_DIR/vhsttx.desktop"
rm -f "$ICON_DIR/vhsttxgui.png"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPLICATIONS_DIR"
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$DATA_HOME/icons/hicolor" >/dev/null 2>&1 || true
fi

echo "Removed VHSTTX desktop integration."
