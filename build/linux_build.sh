#!/usr/bin/env bash
# Build the Tokenless CLI Wizard as a portable Linux AppImage.
#   prerequisites: python3 with tkinter (python3-tk), pip install pyinstaller
#   output: dist/tokenless-cli-<version>-x86_64.AppImage
#
# PyInstaller bundles the wizard into a onedir payload, which is wrapped in a
# standard AppImage AppDir with an AppRun launcher + .desktop file. The tool is
# fetched from AppImageKit's continuous release and extracted first, so building
# does not require FUSE.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
if ! "$PY" -c 'import tkinter' >/dev/null 2>&1; then
  if [ -x /usr/bin/python3 ] && /usr/bin/python3 -c 'import tkinter' >/dev/null 2>&1; then
    PY=/usr/bin/python3
  else
    echo "tkinter is not available to $PY. Install python3-tk, e.g.:" >&2
    echo "  sudo apt-get install -y python3-tk" >&2
    exit 1
  fi
fi

VERSION="$("$PY" -c 'import version; print(version.VERSION)')"
APP="Tokenless-CLI-Wizard"
APPDIR="build/TokenlessCLI.AppDir"
ARTIFACT="dist/tokenless-cli-${VERSION}-x86_64.AppImage"

echo "== generating icons =="
"$PY" build/gen_icon.py

echo "== bundling with PyInstaller =="
rm -rf build/pyi-work build/pyi-dist "$APPDIR" "$ARTIFACT"
"$PY" -m PyInstaller --noconfirm --clean --onedir \
  --name "$APP" \
  --workpath build/pyi-work \
  --distpath build/pyi-dist \
  --icon build/icon.png \
  --add-data "$ROOT/web/chat.html:web" \
  --add-data "$ROOT/opencode.jsonc.example:." \
  --paths . \
  wizard_gui.py

echo "== assembling AppDir =="
mkdir -p "$APPDIR/usr/bin"
cp -r "build/pyi-dist/$APP"/* "$APPDIR/usr/bin/"
cp build/icon.png "$APPDIR/tokenless-cli.png"
cat > "$APPDIR/AppRun" <<EOF
#!/bin/sh
# AppImage launcher: run the PyInstaller-bundled wizard from the mounted AppDir.
APPDIR="\$(cd "\$(dirname "\$0")" && pwd)"
export PATH="\$APPDIR/usr/bin:\$PATH"
exec "\$APPDIR/usr/bin/$APP" "\$@"
EOF
chmod +x "$APPDIR/AppRun"
cat > "$APPDIR/tokenless-cli-wizard.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Tokenless CLI Wizard
Comment=Tokenless CLI - free Colab GPU for opencode & the Gemini CLI
Exec=$APP
Icon=tokenless-cli
Terminal=false
Categories=Development;
EOF

echo "== calling appimagetool =="
TOOL_DIR="$(mktemp -d)"
trap 'rm -rf "$TOOL_DIR"' EXIT
export ARCH="x86_64"
curl -fsSL -o "$TOOL_DIR/appimagetool.AppImage" \
  "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-${ARCH}.AppImage"
chmod +x "$TOOL_DIR/appimagetool.AppImage"
# Extract instead of mounting: the runtime needs FUSE, which CI runners lack.
( cd "$TOOL_DIR" && ./appimagetool.AppImage --appimage-extract >/dev/null )
mkdir -p "$(dirname "$ARTIFACT")"
"$TOOL_DIR/squashfs-root/AppRun" --no-appstream "$APPDIR" "$ARTIFACT" >/dev/null
rm -rf "$APPDIR" build/pyi-work build/pyi-dist
chmod +x "$ARTIFACT"

echo "Done: $ARTIFACT"
ls -lh "$ARTIFACT"