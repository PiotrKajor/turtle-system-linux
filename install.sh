#!/bin/sh
# Installs a desktop entry, icons and the .tgy/.tgp/.tgb file types for the current user.
set -e
APPDIR=$(cd "$(dirname "$0")" && pwd)
DATA=${XDG_DATA_HOME:-$HOME/.local/share}
mkdir -p "$DATA/applications" "$DATA/mime/packages"
sed "s|@APPDIR@|$APPDIR|" "$APPDIR/packaging/turtle-system.desktop.in" > "$DATA/applications/turtle-system.desktop"
for f in "$APPDIR"/icons/turtle-system-*.png; do
    size=$(basename "$f" .png | sed 's/turtle-system-//')
    mkdir -p "$DATA/icons/hicolor/${size}x${size}/apps"
    cp "$f" "$DATA/icons/hicolor/${size}x${size}/apps/turtle-system.png"
done
cp "$APPDIR/packaging/turtle-system-mime.xml" "$DATA/mime/packages/turtle-system.xml"
update-mime-database "$DATA/mime" 2>/dev/null || true
update-desktop-database "$DATA/applications" 2>/dev/null || true
gtk-update-icon-cache -q "$DATA/icons/hicolor" 2>/dev/null || true
echo "Installed: Turtle System is now in your applications menu."
