@echo off
rem Build the Tokenless CLI Wizard as a single-file Windows .exe (PyInstaller).
rem   prerequisites: python, pip install pyinstaller
setlocal
set "ROOT=%~dp0.."
cd /d "%ROOT%"

for /f %%V in ('python -c "import version; print(version.VERSION)"') do set "VER=%%V"

echo == generating icons ==
python build\gen_icon.py

echo == bundling with PyInstaller ==
if exist build\dist rmdir /s /q build\dist
if exist build\build rmdir /s /q build\build
pyinstaller --noconfirm --clean --windowed --onefile ^
  --name "Tokenless-CLI-Setup-%VER%" ^
  --icon build\icon.ico ^
  --add-data "web\chat.html;web" ^
  --add-data "opencode.jsonc.example;." ^
  --paths . ^
  wizard_gui.py

echo Done: dist\Tokenless-CLI-Setup-%VER%.exe
dir dist\Tokenless-CLI-Setup-%VER%.exe
endlocal