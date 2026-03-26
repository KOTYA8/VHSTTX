#!/usr/bin/env sh
set -eu

if ! command -v ttviewer >/dev/null 2>&1; then
    echo "Warning: ttviewer is not on PATH. Install the package first, for example with pipx install -e .[qt]." >&2
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DATA_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}
APPLICATIONS_DIR="$DATA_HOME/applications"
MIME_PACKAGES_DIR="$DATA_HOME/mime/packages"
ICON_DIR="$DATA_HOME/icons/hicolor/512x512/apps"
ICON_SOURCE="$SCRIPT_DIR/../../teletext/gui/teletext.png"

mkdir -p "$APPLICATIONS_DIR" "$MIME_PACKAGES_DIR" "$ICON_DIR"

cp "$SCRIPT_DIR/ttviewer.desktop" "$APPLICATIONS_DIR/ttviewer.desktop"
cp "$SCRIPT_DIR/x-teletext-t42.xml" "$MIME_PACKAGES_DIR/x-teletext-t42.xml"
cp "$ICON_SOURCE" "$ICON_DIR/teletext.png"

if command -v update-mime-database >/dev/null 2>&1; then
    update-mime-database "$DATA_HOME/mime"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPLICATIONS_DIR"
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$DATA_HOME/icons/hicolor" >/dev/null 2>&1 || true
fi

if command -v xdg-mime >/dev/null 2>&1; then
    xdg-mime default ttviewer.desktop application/x-teletext-t42
fi

echo "Installed Teletext Viewer desktop integration for .t42 files."
