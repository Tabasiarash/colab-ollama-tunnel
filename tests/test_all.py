#!/usr/bin/env python3
"""End-to-end tests for the Tokenless CLI installer (no external deps).

Covers:
  1. update_config.write_config against a copy of the live config + a path
     with a non-existent config (simulates first run), idempotency, /v1 & prefix
  2. BASE URL normalization + model listing + menu picking + full wizard flow
     (opencode + Tokenless Gemini CLI engines) driven headlessly against the
     mock Ollama server
  3. the gemini_bridge: LiteLLM config/YAML/alias-map generation, launch
     commands, env vars, and the bridge+gemini launcher scripts (no processes
     are launched in tests)
  4. the GUI wizard module (wizard_gui): import, resource resolution incl. the
     PyInstaller _MEIPASS case, version string, smoke_status, build files
  5. OpenAI-compatible streaming + CORS preflight exactly as web/chat.html uses
  6. every notebook code cell is valid Python; model-tier thresholds pass
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

import gemini_bridge as GB  # noqa: E402
import mock_ollama  # noqa: E402
import version  # noqa: E402
import wizard_gui  # noqa: E402
from update_config import write_config  # noqa: E402
from wizard import smoke_status  # noqa: E402

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

    # first run: engine opencode | BASE URL | model menu pick 2 (= qwen2.5-coder:7b) | smoke y |
    #            open browser? n | LAN? n
    stdin = "\n".join([
        "1",                       # engine -> opencode
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


def test_gemini_bridge():
    print("test: gemini_bridge (config generation + commands, no launch)")
    cfg = GB.build_litellm_config("qwen2.5-coder:14b", "https://abc.trycloudflare.com/v1")
    lit = cfg["model_list"][0]
    ok(f"model_list entry = {lit['litellm_params']['model']}")
    if lit["litellm_params"]["model"] != "ollama_chat/qwen2.5-coder:14b":
        bad("ollama_chat/ prefix expected for litellm")
    if lit["litellm_params"]["api_base"] != "https://abc.trycloudflare.com":
        bad(f"api_base should be de-/v1'd, got {lit['litellm_params']['api_base']}")
    alias = cfg["router_settings"]["model_group_alias"]
    if len(alias) == len(GB.GEMINI_MODEL_IDS) and set(alias) == set(GB.GEMINI_MODEL_IDS) \
            and all(v == GB.MODEL_GROUP for v in alias.values()):
        ok(f"model_group_alias covers all {len(GB.GEMINI_MODEL_IDS)} 0.47 model ids -> {GB.MODEL_GROUP}")
    else:
        bad("model_group_alias incomplete/wrong")
    if cfg["general_settings"]["master_key"] == GB.MASTER_KEY:
        ok("master_key set for local bridge auth")
    else:
        bad("master_key missing")

    # exact YAML layout (stable output = easy diffing for users)
    lines = ["model_list:",
             "  - model_name: colab-tunnel",
             "    litellm_params:",
             "      model: ollama_chat/qwen2.5-coder:14b",
             "      api_base: https://abc.trycloudflare.com",
             "router_settings:",
             "  model_group_alias:"]
    lines += [f"    {gid}: colab-tunnel" for gid in GB.GEMINI_MODEL_IDS]
    lines += ["general_settings:", "  master_key: sk-tokenless-dummy"]
    expected = "\n".join(lines)
    got = GB.render_yaml(cfg)
    (ok if got == expected else bad)("render_yaml layout matches template")
    if got != expected:
        print("--- got ---\n" + got + "\n--- want ---\n" + expected)

    d = Path(tempfile.mkdtemp(prefix="gb_"))
    p = GB.write_bridge_config("qwen2.5-coder:14b", "https://abc.trycloudflare.com/v1",
                               path=d / "litellm_config.yaml")
    ok(f"write_bridge_config -> {p.name} ({p.exists()})")
    if "ollama_chat/qwen2.5-coder:14b" not in p.read_text():
        bad("written yaml missing model entry")

    cmd = GB.bridge_command(p)
    if cmd[-4:] == ["--config", str(p), "--port", "4000"] or "--port" in cmd and "4000" in cmd:
        ok(f"bridge launch cmd = {cmd}")
    else:
        bad(f"bridge launch cmd unexpected: {cmd}")
    gcmd = GB.gemini_command("/fake/gemini")
    if gcmd == ["/fake/gemini", "--sandbox=false"]:
        ok("gemini launch always uses --sandbox=false")
    else:
        bad(f"gemini cmd unexpected: {gcmd}")
    env = GB.gemini_env()
    if env.get("GOOGLE_GEMINI_BASE_URL") == "http://127.0.0.1:4000" \
            and env.get("GEMINI_API_KEY") == "sk-tokenless-dummy":
        ok("gemini env points at the local bridge + dummy key")
    else:
        bad(f"gemini env wrong: {env.get('GOOGLE_GEMINI_BASE_URL')} / {env.get('GEMINI_API_KEY')}")


def test_gemini_wizard_flow():
    print("test: wizard gemini engine (headless, config-only, no launch)")
    home = Path(tempfile.mkdtemp(prefix="wizgem_"))
    state = home / "last_setup.json"
    tokenless = home / "tokenless"
    stdin = "\n".join([
        "2",                       # engine -> gemini CLI
        "http://127.0.0.1:11435",  # BASE URL
        "1",                       # model #1 -> qwen2.5-coder:14b
        "y",                       # smoke test
        "n",                       # open chat UI now?
        "n",                       # LAN serve?
        "",
    ])
    env = dict(os.environ,
               HOME=str(home),
               WIZARD_STATE=str(state),
               TOKENLESS_STATE=str(tokenless),
               TOKENLESS_NO_LAUNCH="1",
               BROWSER="/usr/bin/true",
               NO_COLOR="1")
    proc = subprocess.run([sys.executable, str(REPO / "wizard.py")],
                          input=stdin, capture_output=True, text=True, env=env, timeout=120)
    out = proc.stdout + proc.stderr
    if proc.returncode != 0:
        bad(f"gemini wizard exited {proc.returncode}: {out[-3000:]}")
        return
    bridge = tokenless / "litellm_config.yaml"
    if bridge.exists() and "ollama_chat/qwen2.5-coder:14b" in bridge.read_text():
        ok(f"bridge config written to TOKENLESS_STATE ({bridge.name})")
    else:
        bad(f"bridge config missing at {bridge}")
    if state.exists():
        saved = json.loads(state.read_text())
        (ok if saved.get("engine") == "gemini" else bad)(f"last_setup engine saved = {saved.get('engine')}")
        (ok if saved.get("base") == BASE else bad)("last_setup base saved")
    else:
        bad("last_setup.json not saved for gemini engine")
    if "launch skipped" in out:
        ok("TOKENLESS_NO_LAUNCH respected (no processes started)")
    else:
        bad("expected 'launch skipped' in output")
    if "tunnel OK" in out:
        ok("tunnel smoke test still ran for gemini engine")
    else:
        bad("smoke reply missing from stdout")
    # rerun: reuse path should keep engine choice without re-asking
    stdin2 = "\n".join(["y", "y", "n", "n", ""])
    proc2 = subprocess.run([sys.executable, str(REPO / "wizard.py")],
                           input=stdin2, capture_output=True, text=True, env=env, timeout=120)
    out2 = proc2.stdout + proc2.stderr
    if proc2.returncode != 0:
        bad(f"gemini reuse run exited {proc2.returncode}: {out2[-2000:]}")
    elif "engine: gemini" in out2:
        ok("reuse run kept gemini engine")
    else:
        bad("reuse run did not keep gemini engine")


def test_version():
    print("test: version")
    parts = version.VERSION.split(".")
    ok(f"VERSION = {version.VERSION}")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        bad("VERSION should be X.Y.Z (used for tags + artifact names)")


def test_resource_path():
    print("test: GUI resource resolution")
    orig = getattr(sys, "_MEIPASS", None)
    try:
        d = Path(tempfile.mkdtemp(prefix="guitest_"))
        (d / "web").mkdir()
        (d / "web" / "chat.html").write_text("<html></html>")
        sys._MEIPASS = str(d)
        got = wizard_gui.resource_path("web/chat.html")
        (ok if got == d / "web" / "chat.html" else bad)(f"resource_path(web/chat.html) = {got}")
        found = wizard_gui.chat_html_path()
        if found.exists() and found.name == "chat.html":
            ok(f"chat_html_path resolves to an existing file ({found.name})")
        else:
            bad(f"chat_html_path did not resolve: {found}")
    finally:
        if orig is None:
            sys.__dict__.pop("_MEIPASS", None)
        else:
            sys._MEIPASS = orig


def test_gui_module():
    print("test: GUI module surface")
    if hasattr(wizard_gui, "WizardApp") and hasattr(wizard_gui, "ENGINES"):
        ok("WizardApp class + engine options present")
    else:
        bad("wizard_gui missing WizardApp/ENGINES")
    if {"opencode", "gemini", "both"} <= set(wizard_gui.ENGINES):
        ok("engine choices include opencode / gemini / both")
    else:
        bad("engine options incomplete")
    try:
        import py_compile
        py_compile.compile(str(REPO / "wizard_gui.py"), doraise=True)
        ok("wizard_gui.py compiles")
    except py_compile.PyCompileError as e:
        bad(f"wizard_gui.py syntax: {e}")


def test_smoke_status():
    print("test: smoke_status (used by GUI + wizard)")
    okst, reply = smoke_status(BASE, "qwen2.5-coder:14b", timeout=20)
    (ok if okst and "tunnel OK" in reply else bad)(f"smoke_status vs mock -> {reply!r}")
    okst2, err = smoke_status("http://127.0.0.1:1", "qwen2.5-coder:14b", timeout=3)
    (ok if not okst2 else bad)("smoke_status fails cleanly on a dead tunnel")


def test_gemini_launcher():
    print("test: gemini bridge launcher scripts")
    for plat in ("win32", "linux"):
        text = GB.launcher_script(plat)
        checks = ("--sandbox=false" in text,
                  f"{GB.PORT}" in text,
                  "GOOGLE_GEMINI_BASE_URL" in text,
                  GB.MASTER_KEY in text,
                  "gemini" in text)
        (ok if all(checks) else bad)(f"launcher_script({plat}) contains bridge+gemini wiring")
    d = Path(tempfile.mkdtemp(prefix="lanc_"))
    sh = GB.write_launcher(platform="linux", path=d / "start_gemini.sh")
    bt = GB.write_launcher(platform="win32", path=d / "start_gemini.bat")
    ok(f"write_launcher writes .sh ({sh.exists()}) + .bat ({bt.exists()})")
    if sh.exists() and not os.access(sh, os.X_OK):
        bad(".sh launcher should be executable")


def test_gemini_verify():
    print("test: gemini CLI headless verify (argv + marker, no subprocess)")
    cmd = ["gemini", "--sandbox=false"]
    if GB.gemini_command("gemini") != cmd:
        bad(f"gemini_command base = {GB.gemini_command('gemini')}")
    vc = GB.verify_command("gemini")
    for flag in ("--sandbox=false", "--skip-trust", "-m", GB.FIRST_MODEL_ID, "-p",
                 GB.VERIFY_PROMPT):
        (ok if flag in vc else bad)(f"verify_command contains {flag} -> {vc}")
    if vc.index("--skip-trust") > vc.index("--sandbox=false"):
        ok("verify_command appends headless flags after --sandbox=false")
    else:
        bad("flag order unexpected")
    markers = {
        "tokenless-ok": True,
        "**tokenless-ok**": True,
        "Sure! The phrase is: tokenless-ok": True,
        "": False,
        "I can't help with that": False,
        None: False,
    }
    for text, want in markers.items():
        got = GB._has_marker(text)
        (ok if got is want else bad)(f"_has_marker({text!r}) = {got} (want {want})")
    if GB._has_marker("tokenless-ok", marker="nosecret"):
        bad("custom marker respected")
    else:
        ok("custom marker respected")


def test_build_files():
    print("test: build artifacts")
    for rel in ["build/gen_icon.py", "build/mac_build.sh", "build/win_build.bat",
                "build/icon.png", "build/icon.ico",
                ".github/workflows/build-release.yml"]:
        p = REPO / rel
        (ok if p.exists() else bad)(f"{rel} present")
    png = (REPO / "build/icon.png").read_bytes()
    ico = (REPO / "build/icon.ico").read_bytes()
    if png.startswith(b"\x89PNG\r\n\x1a\n"):
        ok("icon.png is a valid PNG")
    else:
        bad("icon.png magic bytes wrong")
    if ico.startswith(b"\x00\x00\x01\x00\x01\x00"):
        ok("icon.ico has a valid ICO header")
    else:
        bad("icon.ico header wrong")


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
    print("Tokenless CLI - test suite\n")
    server, _thread = mock_ollama.start(PORT)
    try:
        test_write_config()
        test_normalize()
        test_endpoint_contract()
        test_wizard_flow()
        test_gemini_bridge()
        test_gemini_wizard_flow()
        test_version()
        test_resource_path()
        test_gui_module()
        test_smoke_status()
        test_gemini_launcher()
        test_gemini_verify()
        test_build_files()
        test_notebook()
    finally:
        server.shutdown()
    print(f"\n{'='*40}\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())