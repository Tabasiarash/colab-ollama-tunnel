#!/usr/bin/env bash
# Build the Tokenless CLI Wizard .app and package it into a .dmg (macOS).
#   prerequisites: python3, pip install pyinstaller, Xcode CLT (sips + iconutil)
#
# Env overrides:
#   TARGET_ARCH          build arch (default: host arch, e.g. arm64/x86_64)
#   MIN_MACOS_VERSION    minimum macOS the .app must run on (default: 12.0 = Monterey)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION="$(python3 -c 'import version; print(version.VERSION)')"
ARCH="${TARGET_ARCH:-$(uname -m)}"
if [ "$ARCH" = "aarch64" ]; then ARCH="arm64"; fi
export MACOSX_DEPLOYMENT_TARGET="${MIN_MACOS_VERSION:-12.0}"
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

echo "== bundling with PyInstaller == (arch=$ARCH, min-macos=$MACOSX_DEPLOYMENT_TARGET)"
rm -rf build/dist build/build
pyinstaller --noconfirm --clean --windowed --onedir \
  --name "$APP" \
  --target-arch "$ARCH" \
  --icon build/TokenlessIcon.icns \
  --osx-bundle-identifier com.tokenless.cli.wizard \
  --add-data "web/chat.html:web" \
  --add-data "opencode.jsonc.example:." \
  --paths . \
  wizard_gui.py

DMG="dist/${APP}-${VERSION}-${ARCH}.dmg"
rm -f "$DMG"
echo "== creating $DMG =="
hdiutil create -volname "$VOLNAME" \
  -srcfolder "dist/$APP.app" \
  -ov "$DMG" >/dev/null
echo "Done: $DMG"
ls -lh "$DMG"