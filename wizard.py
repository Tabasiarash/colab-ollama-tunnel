#!/usr/bin/env python3
"""Install wizard for Tokenless CLI (macOS + Windows).

Walks you through, step by step:
  1. pick your engine: opencode (direct) and/or the Tokenless Gemini CLI
     (via a local LiteLLM bridge); prerequisites auto-install if missing
  2. paste the BASE URL from the Colab notebook (live-checks it)
  3. pick the model pulled on Colab
  4. configures opencode and/or writes + launches the Gemini bridge
  5. smoke-tests the tunnel and opens a browser chat
     (optionally also served over your LAN for other devices)

Run it with:
    python3 wizard.py        # macOS / Linux
    py -3 wizard.py          # Windows

Only uses the Python standard library - no pip installs needed.
"""
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

WIZARD_DIR = Path(__file__).resolve().parent
CHAT_HTML = WIZARD_DIR / "web" / "chat.html"
SETUP_JSON = Path(os.environ.get("WIZARD_STATE") or (WIZARD_DIR / "last_setup.json"))

sys.path.insert(0, str(WIZARD_DIR))
import gemini_bridge as GB  # noqa: E402
from update_config import write_config, default_config_path  # noqa: E402

USE_COLOR = os.environ.get("NO_COLOR") is None and sys.stdout.isatty()


def c(text, code):
    if USE_COLOR:
        return f"\033[{code}m{text}\033[0m"
    return text


BOLD = lambda t: c(t, "1")  # noqa: E731
GREEN = lambda t: c(t, "32")  # noqa: E731
CYAN = lambda t: c(t, "36")  # noqa: E731
YELLOW = lambda t: c(t, "33")  # noqa: E731
RED = lambda t: c(t, "31")  # noqa: E731
DIM = lambda t: c(t, "2")  # noqa: E731

BANNER = r"""
╔══════════════════════════════════════════════════════════════════════╗
║  Tokenless CLI  —  a free Colab GPU for opencode & the Gemini CLI    ║
║             zero API keys · zero token costs · zero hardware         ║
╚══════════════════════════════════════════════════════════════════════╝
   Colab GPU -> Ollama -> {tunnel} -> your CLI / browser chat
"""


def ask(prompt, default=None):
    suffix = f" [{default}]" if default is not None else ""
    try:
        value = input(f"{BOLD(prompt)}{DIM(suffix)} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(130)
    if not value and default is not None:
        return default
    return value


def yn(prompt, default=True):
    value = ask(prompt + (" [Y/n]" if default else " [y/N]"), default="")
    return value.lower() not in ("n", "no", "0") if value else default


def section(title):
    print()
    print(CYAN(f"== {title} =="))


# ---------------------------------------------------------------------------
# 1. Prerequisites
# ---------------------------------------------------------------------------
def cmd_exists(name):
    return shutil.which(name) is not None


def find_opencode():
    for name in ("opencode", "opencode.cmd", "opencode.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def install_opencode():
    print(YELLOW("opencode CLI is not installed. Do you want to install it now?"))
    if not yn("  Confirm auto-install (npm install -g opencode-ai)?", default=True):
        print("  Skipping. You can install it later from https://opencode.ai")
        return False
    if not cmd_exists("npm"):
        print(RED("  npm was not found. Install Node.js first: https://nodejs.org"))
        print("  Then run:  npm install -g opencode-ai")
        return False
    print("  Running: npm install -g opencode-ai  (this can take a minute)...")
    try:
        rc = subprocess.run("npm install -g opencode-ai", shell=True).returncode
    except Exception as exc:  # pragma: no cover
        print(RED(f"  Install failed: {exc}"))
        return False
    if rc != 0 or not find_opencode():
        print(RED("  Install did not succeed."))
        print("  On macOS try:  brew install opencode-ai")
        print("  On Windows try: choco install opencode")
        print("  Manual install: https://opencode.ai/docs/")
        return False
    print(GREEN("  done."))
    return True


def check_prereqs(engine):
    section("Prerequisites")
    print(f"{GREEN('✔')} Python {sys.version.split()[0]}")
    for cli, label in (("opencode", find_opencode()), ("gemini", GB.find_gemini())):
        if engine not in ("opencode", "both") and cli == "opencode":
            continue
        if engine not in ("gemini", "both") and cli == "gemini":
            continue
        if label:
            print(f"{GREEN('✔')} {cli} CLI: {label}")
        elif cli == "opencode":
            print(f"{YELLOW('•')} opencode CLI not found")
            install_opencode()
        else:
            print(f"{YELLOW('•')} gemini CLI not found")
            install_gemini()
    if engine in ("gemini", "both"):
        lit = GB.find_litellm()
        if lit:
            print(f"{GREEN('✔')} litellm: {lit}")
        elif GB.no_launch():
            print(f"{YELLOW('•')} litellm not found (config-only mode - launch skipped)")
        else:
            print(f"{YELLOW('•')} litellm not found")
            install_litellm()


def install_gemini():
    print(YELLOW("Gemini CLI (@google/gemini-cli) is not installed. Install it now?"))
    if not yn("  Confirm auto-install (npm install -g @google/gemini-cli)?", default=True):
        print("  Skipping - the bridge config will still be written; run gemini later.")
        return False
    if not cmd_exists("npm"):
        print(RED("  npm was not found. Install Node.js first: https://nodejs.org"))
        print("  Then run:  npm install -g @google/gemini-cli")
        return False
    print("  Running: npm install -g @google/gemini-cli  (this can take a minute)...")
    try:
        rc = subprocess.run("npm install -g @google/gemini-cli", shell=True).returncode
    except Exception as exc:  # pragma: no cover
        print(RED(f"  Install failed: {exc}"))
        return False
    if rc != 0 or not GB.find_gemini():
        print(RED("  Install did not succeed."))
        print("  Manual install: https://github.com/google-gemini/gemini-cli#installation")
        return False
    print(GREEN("  done."))
    return True


def install_litellm():
    print(YELLOW("LiteLLM proxy (the Gemini <-> OpenAI bridge) is not installed."))
    print(DIM("  The Libre deployment of 'litellm' is a single pip package; it pulls in"))
    print(DIM("  a number of dependencies on first run, so this is the one heavier step."))
    if not yn("  Auto-install it now (python3 -m pip install 'litellm[proxy]')?", default=True):
        print("  Skipping - the bridge config will still be written; start litellm later:")
        print("  " + " ".join(GB.bridge_command()))
        return False
    print("  Running: python3 -m pip install 'litellm[proxy]'  (a few minutes)...")
    try:
        rc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "litellm[proxy]"]).returncode
    except Exception as exc:  # pragma: no cover
        print(RED(f"  Install failed: {exc}"))
        return False
    if rc != 0 or not GB.find_litellm():
        print(RED("  Install did not succeed."))
        print("  Manual install:  python3 -m pip install 'litellm[proxy]'")
        return False
    print(GREEN("  done."))
    return True


def choose_engine(saved=None):
    if saved and saved.get("engine"):
        eng = saved["engine"]
        print(f"{GREEN('✔')} engine: {eng}")
        return eng
    section("Engine")
    print("Which coding CLI should drive this tunnel?")
    print("  [1] opencode   (direct - recommended; config patched, no extra process)")
    print("  [2] gemini CLI (Tokenless Gemini CLI - via local LiteLLM bridge)")
    print("  [3] both")
    choice = ask("Your choice", default="1")
    if choice.strip() in ("2", "gemini", "g"):
        return "gemini"
    if choice.strip() in ("3", "both", "b"):
        return "both"
    return "opencode"


# ---------------------------------------------------------------------------
# 2. BASE URL
# ---------------------------------------------------------------------------
def normalize_base(base):
    base = base.strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    if not base.lower().startswith(("http://", "https://")):
        base = "https://" + base
    return base


def http_base(base):
    return base.removesuffix("/v1")


def list_models(base):
    """Return the list of model ids served at this tunnel URL."""
    url = http_base(base) + "/v1/models"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    last = None
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode())
            return [m["id"] for m in (data.get("data") or [])]
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # Ollama may not have /v1/models old builds; fall back to /api/tags
                try:
                    with urllib.request.urlopen(http_base(base) + "/api/tags", timeout=10) as r2:
                        data = json.loads(r2.read().decode())
                    return [m["name"] for m in (data.get("models") or [])]
                except Exception:
                    raise
            raise
        except Exception as e:
            last = e
            if i < 2:
                print(DIM(f"    … attempt {i+1} failed ({e.__class__.__name__}: {e}), retrying"))
                time.sleep(2)
    raise RuntimeError(f"could not reach the tunnel: {last}")


def get_base_url():
    section("BASE URL")
    print("Paste the BASE URL that the Colab notebook printed")
    print("  (looks like https://xxxx-xxx-xxx-xxx.trycloudflare.com)")
    while True:
        raw = ask("BASE URL", default="")
        base = normalize_base(raw)
        if not base or base == "https://":
            print(RED("  nothing entered - please paste the URL from the notebook."))
            continue
        print(f"  Checking {base}/v1/models ...")
        try:
            models = list_models(base)
            print(GREEN(f"  ✔ reachable, {len(models)} model(s) on this tunnel"))
            return base, models
        except Exception as exc:
            print(RED(f"  ✘ {exc}"))
            print("  The tunnel may be down, still starting, or the URL was mis-typed.")
            if not yn("  Try another URL?", default=True):
                raise SystemExit("aborted.")
        except KeyboardInterrupt:
            raise SystemExit(130)


# ---------------------------------------------------------------------------
# 3. Model
# ---------------------------------------------------------------------------
PRETTY = {
    "qwen2.5-coder:14b": "Qwen2.5 Coder 14B (free-tier default)",
    "qwen2.5-coder:7b": "Qwen2.5 Coder 7B (CPU fallback)",
    "qwen3-coder:30b-a3b-q4_K_M": "Qwen3 Coder 30B-A3B (Pro/PayGo, 24GB+ GPU)",
    "qwen3:8b": "Qwen3 8B",
    "gemma4:12b": "Gemma 4 12B",
    "gemma4:31b": "Gemma 4 31B (needs 24GB+)",
    "devstal": "Devstral Small 2 24B (agentic)",
}


def choose_model(models):
    section("Model")
    models = sorted(set(models))
    if not models:
        print(RED("  No models are pulled on the Colab side yet."))
        print("  The notebook pulls one automatically - re-run it, or type a tag you will pull.")
        tag = ask("MODEL ID", default="qwen2.5-coder:14b")
        return tag
    print("Models on the tunnel:")
    for i, m in enumerate(models, 1):
        note = PRETTY.get(m, "")
        print(f"  [{i}] {m}{DIM(' — ' + note) if note else ''}")
    print("  [0] type a custom tag yourself")
    choice = ask("Your choice (0 to type your own)", default=str(1 if models else 0))
    if choice.isdigit() and 0 < int(choice) <= len(models):
        return models[int(choice) - 1]
    tag = ask("MODEL ID (e.g. qwen2.5-coder:14b)", default="qwen2.5-coder:14b")
    return tag


# ---------------------------------------------------------------------------
# 4. opencode config
# ---------------------------------------------------------------------------
def configure_opencode(base, model):
    section("opencode configuration")
    cfg = default_config_path()
    if not cfg.exists():
        print(YELLOW(f"  config not found at {cfg}"))
        if not yn("  Create it now from opencode.jsonc.example?", default=True):
            print("  Skipping - you can point opencode manually later.")
            return None
        src = WIZARD_DIR / "opencode.jsonc.example"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, cfg)
    try:
        path, model, base = write_config(base, model)
    except Exception as exc:
        print(RED(f"  ✘ failed to write config: {exc}"))
        return None
    print(GREEN(f"  ✔ config updated: {path}"))
    print(f"    model   = ollama/{model}")
    print(f"    baseURL = {base}/v1")
    print(YELLOW("  Remember to restart opencode to pick up the change."))
    return {"path": str(path), "model": model, "base": base}


# ---------------------------------------------------------------------------
# 4b. Tokenless Gemini CLI (via LiteLLM bridge)
# ---------------------------------------------------------------------------
def configure_gemini(base, model):
    section("Tokenless Gemini CLI (LiteLLM bridge)")
    try:
        cfg = GB.write_bridge_config(model, http_base(base))
    except Exception as exc:
        print(RED(f"  ✘ failed to write bridge config: {exc}"))
        return None
    print(GREEN(f"  ✔ bridge config: {cfg}"))
    print(f"    model      = ollama_chat/{model}")
    print(f"    api_base   = {http_base(base)}")
    print(f"    port       = {GB.PORT}  (master key: {GB.MASTER_KEY})")
    print(DIM("    {bridge launch}  " + " ".join(GB.bridge_command(cfg))))
    return {"config": str(cfg)}


def run_gemini_cli(bridge):
    section("Gemini CLI")
    if GB.no_launch():
        print(DIM("  (launch skipped - TOKENLESS_NO_LAUNCH is set for automation)"))
        return
    cfg = Path(bridge["config"])
    if not GB.find_litellm() or not GB.find_gemini():
        print(YELLOW("  prerequisite CLI(s) missing - run the bridge manually:"))
        print("    " + " ".join(GB.bridge_command(cfg)))
        print(f"    GOOGLE_GEMINI_BASE_URL=http://127.0.0.1:{GB.PORT} "
              f"GEMINI_API_KEY={GB.MASTER_KEY} gemini --sandbox=false")
        return
    proc = GB.launch_bridge(cfg, port=GB.PORT, log_path=GB.bridge_dir() / "litellm.log")
    if proc is None:
        print(RED("  ✘ could not start LiteLLM."))
        return
    print(f"  starting LiteLLM on port {GB.PORT} ...")
    if not GB.wait_ready(timeout=120):
        print(YELLOW("  bridge did not answer in time - check the tunnel and try again."))
    else:
        print(GREEN(f"  ✔ bridge ready at http://127.0.0.1:{GB.PORT}"))
        reply = GB.smoke_bridge()
        if reply:
            print(GREEN("  ✔ model replied (Gemini protocol):") + ("  " + reply[:90] if reply else ""))
        else:
            print(YELLOW("  • bridge smoke test did not complete - first load can be slow."))
        ok_v, reply_v = GB.verify_gemini_cli()
        if ok_v:
            print(GREEN("  ✔ gemini CLI replied (headless, real run):"))
            for line in reply_v.splitlines()[:6]:
                print("    " + line)
        else:
            print(YELLOW("  • gemini CLI headless verify did not complete yet:"))
            if reply_v:
                print("    " + " ".join(reply_v.split())[:160])
            print(DIM("    retry manually: GOOGLE_GEMINI_BASE_URL=http://127.0.0.1:4000 "
                      f"GEMINI_API_KEY={GB.MASTER_KEY} gemini --sandbox=false -m {GB.FIRST_MODEL_ID}"))
    print(GREEN("  launching gemini (interactive - exit with /quit when done)..."))
    print(DIM(f"    env  GOOGLE_GEMINI_BASE_URL=http://127.0.0.1:{GB.PORT}  "
              f"GEMINI_API_KEY={GB.MASTER_KEY}"))
    GB.launch_gemini()
    print(DIM("  gemini exited. Stop the bridge anytime with:"))
    print(YELLOW("    pkill -f litellm --config          # or: taskkill /F /IM litellm.exe"))


# ---------------------------------------------------------------------------
# 5. Chat
# ---------------------------------------------------------------------------
def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def start_local_chat(base, model):
    section("Browser chat")
    if not CHAT_HTML.exists():
        print(RED(f"  ✘ chat page missing: {CHAT_HTML}"))
        return
    import urllib.parse
    q = urllib.parse.urlencode({"base": http_base(base), "model": model})
    file_url = CHAT_HTML.as_uri() + "?" + q
    proxy = None
    if yn("  Open the chat in your browser now?", default=True):
        webbrowser.open(file_url)
        print(GREEN(f"  ✔ opened {file_url}"))
    print(DIM("  Chat URL (use it later):"))
    print(DIM(f"    {file_url}"))
    if yn("  Also serve it for your LAN / phone? (optional)", default=False):
        port = 8080
        proxy = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(port), "--bind", "0.0.0.0"],
            cwd=str(WIZARD_DIR / "web"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(GREEN(f"  ✔ serving on http://{lan_ip()}:{port}"))
        print(DIM(f"    phone/other devices:  http://{lan_ip()}:{port}/chat.html?{q}"))
        print(DIM("    stop it later with Ctrl+C or by killing the http.server process."))
    return file_url


# ---------------------------------------------------------------------------
# 6. Smoke test
# ---------------------------------------------------------------------------
def smoke_status(base, model, timeout=180):
    """One-shot tunnel test. Returns (ok, reply_or_error) - reuses urllib only."""
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "user", "content": "Reply with exactly: tunnel OK (nothing else)"}
            ],
            "stream": False,
            "temperature": 0,
        }
    ).encode()
    req = urllib.request.Request(
        http_base(base) + "/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        return True, (data["choices"][0]["message"]["content"] or "").strip()
    except Exception as exc:
        return False, str(exc)


def smoke_test(base, model):
    section("Quick test")
    if not yn("  Send a one-shot test message through the tunnel?", default=True):
        return
    ok, reply = smoke_status(base, model)
    if ok:
        print(GREEN("  ✔ model replied:") + ("  " + reply[:90] if reply else DIM("  (empty)")))
    else:
        print(YELLOW(f"  • smoke test did not complete: {reply}"))
        print("    (First load can be slow while the model warms up - retry in the chat.)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print(BANNER.replace("{tunnel}", "cloudflared"))
    print(DIM(__doc__.splitlines()[0]))
    print(DIM(f"works on macOS (python3) and Windows (py -3)"))

    saved = None
    if SETUP_JSON.exists():
        try:
            saved = json.loads(SETUP_JSON.read_text())
        except Exception:
            saved = None

    engine = choose_engine(saved)
    check_prereqs(engine)

    base_ok = False
    base = ""
    models = []
    if saved and saved.get("base") and yn(
        f"Reuse last setup ({saved['base']}, model {saved.get('model','')}, "
        f"engine {saved.get('engine','opencode')})?",
        default=False,
    ):
        base, models = saved["base"], []
        base_ok = True
        print(f"{GREEN('✔')} reusing {base}")

    if not base_ok:
        base, models = get_base_url()

    if base_ok and saved and saved.get("model"):
        model = saved["model"]
        print(f"{GREEN('✔')} model: {model}")
    else:
        model = choose_model(models)

    result = None
    bridge = None
    if engine in ("opencode", "both"):
        result = configure_opencode(base, model)
    if engine in ("gemini", "both"):
        bridge = configure_gemini(base, model)

    smoke_test(base, model)
    if bridge:
        run_gemini_cli(bridge)

    chat_url = start_local_chat(base, model)

    try:
        SETUP_JSON.write_text(json.dumps({
            "engine": engine,
            "base": http_base(base),
            "model": model,
            "config": (result or {}).get("path"),
            "bridge": (bridge or {}).get("config"),
            "chat": chat_url,
        }, indent=2))
    except Exception:
        pass

    section("Done")
    print(GREEN("  Your setup is ready."))
    print(f"  ENGINE   : {engine}")
    print(f"  BASE URL : {http_base(base)}/v1")
    print(f"  MODEL ID : {model}")
    if result:
        print(f"  config   : {result['path']}")
        print(YELLOW("  → Restart opencode, then chat with the model in the browser."))
    if bridge:
        print(f"  bridge   : {bridge['config']}")
        print(YELLOW("  → Gemini bridge written; launch with  litellm --config <that file> --port 4000"))
        if engine == "gemini":
            print(YELLOW("    or run gemini manually:  GOOGLE_GEMINI_BASE_URL=http://127.0.0.1:4000 "
                         "GEMINI_API_KEY=sk-tokenless-dummy gemini --sandbox=false"))
    print()
    print(DIM("Tip: keep the Colab notebook running - the tunnel dies with the session."))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\naborted.")
        raise SystemExit(130)