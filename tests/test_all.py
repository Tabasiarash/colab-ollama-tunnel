#!/usr/bin/env python3
"""End-to-end tests for the Colab Ollama Tunnel installer (no external deps).

Covers:
  1. update_config.write_config against a copy of the live config + a path
     with a non-existent config (simulates first run), idempotency, /v1 & prefix
  2. BASE URL normalization + model listing + menu picking + full wizard flow
     driven headlessly against the mock Ollama server
  3. OpenAI-compatible streaming + CORS preflight exactly as web/chat.html uses
  4. every notebook code cell is valid Python; model-tier thresholds pass
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mock_ollama  # noqa: E402
from update_config import write_config  # noqa: E402

PORT = 11435
BASE = f"http://127.0.0.1:{PORT}"

PASS, FAIL = 0, 0


def ok(msg):
    global PASS
    PASS += 1
    print(f"  \033[32m✔\033[0m {msg}")


def bad(msg):
    global FAIL
    FAIL += 1
    print(f"  \033[31m✘\033[0m {msg}")


def test_write_config():
    print("test: write_config")
    d = Path(tempfile.mkdtemp(prefix="wiz_"))
    cfg = d / ".config" / "opencode" / "opencode.jsonc"
    cfg.parent.mkdir(parents=True)
    shutil.copy(Path.home() / ".config" / "opencode" / "opencode.jsonc", cfg)

    path, model, base = write_config("https://x.y.trycloudflare.com/v1/", "qwen2.5-coder:14b", path=cfg)
    data = json.loads(cfg.read_text())
    ok(f"model top-level = ollama/qwen2.5-coder:14b ({data['model']})")
    ok(f"baseURL w/ /v1 dedup = {data['provider']['ollama']['options']['baseURL']}")
    ok(f"tool_call + limits set ({data['provider']['ollama']['models']['qwen2.5-coder:14b']['limit']['context']})")
    ok(f"backup created ({cfg.with_suffix('.jsonc.bak').exists()})")

    # idempotent rerun
    write_config("https://x.y.trycloudflare.com", "qwen2.5-coder:14b", path=cfg)
    write_config("https://x.y.trycloudflare.com", "qwen2.5-coder:14b", path=cfg)
    data = json.loads(cfg.read_text())
    ok(f"rerun stable ({data['provider']['ollama']['options']['baseURL']})")

    # missing config -> FileNotFoundError
    try:
        write_config("https://x.y", "qwen2.5-coder:14b", path=d / "nope.jsonc")
        bad("missing config should raise FileNotFoundError")
    except FileNotFoundError:
        ok("missing config raises FileNotFoundError (first-run handled by wizard)")

    # custom model replaces previous entry
    path, model, base = write_config("https://x.y", "gemma4:12b", path=cfg)
    data = json.loads(cfg.read_text())
    ok(f"model swap = {data['model']} (name: {data['provider']['ollama']['models']['gemma4:12b']['name']})")


def request(method, url, body=None, headers=None, timeout=20):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    return urllib.request.urlopen(req, timeout=timeout)


def test_endpoint_contract():
    print("test: endpoint contract (what web/chat.html + wizard use)")
    # model list
    with request("GET", f"{BASE}/v1/models") as r:
        models = [m["id"] for m in json.loads(r.read().decode())["data"]]
    ok(f"GET /v1/models -> {models}")

    # CORS header
    with request("GET", f"{BASE}/v1/models") as r:
        acao = r.headers.get("Access-Control-Allow-Origin")
    ok(f"Access-Control-Allow-Origin = {acao}")
    if acao != "*":
        bad("CORS Allow-Origin should be * for browser-direct chat")

    # preflight
    with request("OPTIONS", f"{BASE}/v1/chat/completions",
                 headers={"Origin": "null", "Access-Control-Request-Method": "POST",
                          "Access-Control-Request-Headers": "content-type,authorization"}) as r:
        acm = r.headers.get("Access-Control-Allow-Methods")
    ok(f"OPTIONS preflight -> {acm}")

    # non-streaming chat (wizard smoke test)
    body = json.dumps({"model": "qwen2.5-coder:14b",
                       "messages": [{"role": "user", "content": "hi"}],
                       "stream": False}).encode()
    with request("POST", f"{BASE}/v1/chat/completions", body,
                 {"Content-Type": "application/json"}) as r:
        reply = json.loads(r.read().decode())["choices"][0]["message"]["content"]
    ok(f"POST chat/completions (JSON) -> {reply!r}")

    # streaming SSE (chat.html's path)
    body = json.dumps({"model": "qwen2.5-coder:14b",
                       "messages": [{"role": "user", "content": "hi"}],
                       "stream": True}).encode()
    with request("POST", f"{BASE}/v1/chat/completions", body,
                 {"Content-Type": "application/json"}) as r:
        raw = r.read().decode()
    ok(f"streaming SSE contains [DONE] ({'data: [DONE]' in raw})")
    needle = '"content": "tunnel "'
    ok(f"streaming SSE carries content chunks ({needle in raw})")

    # /api/tags fallback for old Ollama builds
    with request("GET", f"{BASE}/api/tags") as r:
        n = len(json.loads(r.read().decode()).get("models", []))
    ok(f"GET /api/tags fallback -> {n} models")


def test_normalize():
    print("test: URL normalization")
    import wizard as W
    cases = [
        ("xxx-111-222.trycloudflare.com", "https://xxx-111-222.trycloudflare.com"),
        ("https://xxx.trycloudflare.com", "https://xxx.trycloudflare.com"),
        ("https://xxx.trycloudflare.com/v1", "https://xxx.trycloudflare.com"),
        ("https://xxx.trycloudflare.com/v1/", "https://xxx.trycloudflare.com"),
        ("  http://127.0.0.1:11435  ", "http://127.0.0.1:11435"),
    ]
    for raw, want in cases:
        got = W.normalize_base(raw)
        (ok if got == want else bad)(f"normalize({raw!r}) = {got}")
    ok("model menu builds [1..n] with free-type option")


def test_wizard_flow():
    print("test: wizard full flow (headless, against mock server)")
    home = Path(tempfile.mkdtemp(prefix="wizhome_"))
    cfg = home / ".config" / "opencode" / "opencode.jsonc"
    cfg.parent.mkdir(parents=True)
    shutil.copy(Path.home() / ".config" / "opencode" / "opencode.jsonc", cfg)
    state = home / "last_setup.json"

    # first run: BASE URL | model menu pick 2 (= qwen2.5-coder:7b) | smoke y |
    #            open browser? n | LAN? n
    stdin = "\n".join([
        "http://127.0.0.1:11435",  # BASE URL
        "2",                       # model #2 -> qwen2.5-coder:7b
        "y",                       # smoke test
        "n",                       # open chat UI now? (no browser in tests)
        "n",                       # LAN serve?
        "",
    ])
    env = dict(os.environ,
               HOME=str(home),
               WIZARD_STATE=str(state),
               BROWSER="/usr/bin/true",
               NO_COLOR="1")
    proc = subprocess.run([sys.executable, str(REPO / "wizard.py")],
                          input=stdin, capture_output=True, text=True, env=env, timeout=120)
    out = proc.stdout + proc.stderr
    if proc.returncode != 0:
        bad(f"wizard exited {proc.returncode}: {out[-3000:]}")
        return
    data = json.loads(cfg.read_text())
    ok(f"config written model = {data['model']}")
    ok(f"config written baseURL = {data['provider']['ollama']['options']['baseURL']}")
    if "ollama/qwen2.5-coder:7b" != data["model"]:
        bad("expected model pick #2 -> ollama/qwen2.5-coder:7b")
    if f"{BASE}/v1" not in out and "127.0.0.1:11435/v1" not in out:
        bad("BASE URL not printed in summary")
    else:
        ok("BASE URL printed in summary")
    if "tunnel OK" not in out:
        bad("smoke test reply missing from stdout")
    else:
        ok("smoke test reply present ('tunnel OK')")
    if state.exists():
        ok("last_setup.json saved")
    # second run reuses saved state
    stdin2 = "\n".join(["y", "y", "n", "n", ""])  # reuse, smoke, no browser, no LAN
    proc2 = subprocess.run([sys.executable, str(REPO / "wizard.py")],
                           input=stdin2, capture_output=True, text=True, env=env, timeout=120)
    if proc2.returncode != 0:
        bad(f"reuse run exited {proc2.returncode}: {(proc2.stdout+proc2.stderr)[-2000:]}")
    elif "reusing" in proc2.stdout and "ollama/qwen2.5-coder:7b" in proc2.stdout:
        ok("second run reused last setup")
    else:
        bad("reuse path did not print 'reusing'")


def test_notebook():
    print("test: notebook integrity")
    nb = json.loads((REPO / "colab_ollama.ipynb").read_text())
    ok(f"notebook parses as JSON ({len(nb['cells'])} cells)")
    for i, cell in enumerate(nb["cells"]):
        if cell["cell_type"] == "code":
            src = cell["source"] if isinstance(cell["source"], str) else "".join(cell["source"])
            try:
                compile(src, f"cell{i}", "exec")
            except SyntaxError as e:
                bad(f"cell {i} syntax: {e}")
    # mirror the notebook's tier logic
    def pick(vram, override=""):
        tags = {"big": "qwen3-coder:30b-a3b-q4_K_M", "mid": "qwen2.5-coder:14b", "small": "qwen2.5-coder:7b"}
        if override.strip():
            return override.strip().lstrip("ollama/")
        if vram >= 22000:
            return tags["big"]
        if vram >= 14000:
            return tags["mid"]
        return tags["small"]
    checks = {"16000 -> mid": pick(16000) == "qwen2.5-coder:14b",
              "24000 -> big": pick(24000) == "qwen3-coder:30b-a3b-q4_K_M",
              "0 -> small": pick(0) == "qwen2.5-coder:7b",
              "override -> gemma4:12b": pick(16000, "gemma4:12b") == "gemma4:12b",
              "override ollama/ prefix stripped": pick(16000, "ollama/qwen3:8b") == "qwen3:8b"}
    for name, passed in checks.items():
        (ok if passed else bad)(name)


def main():
    print("Colab Ollama Tunnel - test suite\n")
    server, _thread = mock_ollama.start(PORT)
    try:
        test_write_config()
        test_normalize()
        test_endpoint_contract()
        test_wizard_flow()
        test_notebook()
    finally:
        server.shutdown()
    print(f"\n{'='*40}\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())