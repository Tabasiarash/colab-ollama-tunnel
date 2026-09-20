#!/usr/bin/env bash
# Colab Ollama Tunnel - install wizard (macOS / Linux)
cd "$(dirname "$0")" || exit 1
exec python3 wizard.py "$@"