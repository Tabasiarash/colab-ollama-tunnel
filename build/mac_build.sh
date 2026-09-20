#!/usr/bin/env bash
# Build the Tokenless CLI Wizard .app and package it into a .dmg (macOS).
#   prerequisites: python3, pip install pyinstaller, Xcode CLT (sips + iconutil)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION="$(python3 -c 'import version; print(version.VERSION)')"
APP="Tokenless CLI Wizard"
VOLNAME="Tokenless CLI"

echo "== generating icons =="
python3 build/gen_icon.py
rm -rf build/TokenlessIcon.iconset
mkdir -p build/TokenlessIcon.iconset
for s in 16 32 128 256 512; do
  sips -z "$s" "$s" build/icon.png --out "build/TokenlessIcon.iconset/icon_${s}x${s}.png" >/dev/null
  d=$((s * 2))
  sips -z "$d" "$d" build/icon.png --out "build/TokenlessIcon.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns build/TokenlessIcon.iconset -o build/TokenlessIcon.icns

echo "== bundling with PyInstaller =="
rm -rf build/dist build/build
pyinstaller --noconfirm --clean --windowed --onedir \
  --name "$APP" \
  --icon build/TokenlessIcon.icns \
  --osx-bundle-identifier com.tokenless.cli.wizard \
  --add-data "web/chat.html:web" \
  --add-data "opencode.jsonc.example:." \
  --paths . \
  wizard_gui.py

DMG="dist/${APP}-${VERSION}.dmg"
rm -f "$DMG"
echo "== creating $DMG =="
hdiutil create -volname "$VOLNAME" \
  -srcfolder "dist/$APP.app" \
  -ov "$DMG" >/dev/null
echo "Done: $DMG"
ls -lh "$DMG"