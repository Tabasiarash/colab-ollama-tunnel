@echo off
REM Colab Ollama Tunnel - install wizard. Double-click or run from a terminal.
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 wizard.py %*
  goto :eof
)
where python >nul 2>nul
if %errorlevel%==0 (
  python wizard.py %*
  goto :eof
)
echo Python was not found. Install it from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH" during install) and run this again.
pause