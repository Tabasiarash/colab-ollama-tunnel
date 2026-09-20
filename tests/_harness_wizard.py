#!/usr/bin/env python3
"""Run wizard.py inside a process that also hosts the mock server.

Some CI runners can't reach a parent process's 127.0.0.1 listener from a child
process (macOS Actions runners), so wizard integration tests run the fake
tunnel *and* the wizard in the same process via this harness.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mock_ollama  # noqa: E402
import wizard  # noqa: E402

mock_ollama.start(int(os.environ.get("MOCK_PORT", "11435")))
try:
    raise SystemExit(wizard.main())
except KeyboardInterrupt:
    print("\naborted.")
    raise SystemExit(130)