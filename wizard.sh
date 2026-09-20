#!/usr/bin/env bash
# Tokenless CLI - install wizard (macOS / Linux)
cd "$(dirname "$0")" || exit 1
exec python3 wizard.py "$@"