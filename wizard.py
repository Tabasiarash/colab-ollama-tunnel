#!/usr/bin/env python3
"""Install wizard for Colab Ollama Tunnel (macOS + Windows).

Walks you through, step by step:
  1. prerequisites (opencode CLI, auto-installs if missing)
  2. paste the BASE URL from the Colab notebook (live-checks it)
  3. pick the model pulled on Colab
  4. patches your opencode config
  5. opens a browser chat so you can test the model right away
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
   ____      _       _   ____             _
  / __ \    | |     | | |  _ \           | |
 | |  | |___| | ___ | |_| |_) | __ _ __ _| |  _ __ ___  _ __
 | |  | / __| |/ _ \| __|  _ < / _` / _` | | | '_ ` _ \| '_ \
 | |__| \__ \ | (_) | |_| |_) | (_| (_| | |_| | | | | | | |_) |
  \____/|___/_\___/ \__|____/ \__,_\__, |_|_|_| |_| |_| .__/
                                    __/ |              | |
                                   |___/               |_|
   Colab GPU -> Ollama -> {tunnel} -> this machine
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


def check_prereqs():
    section("Prerequisites")
    print(f"{GREEN('✔')} Python {sys.version.split()[0]}")
    oc = find_opencode()
    if oc:
        print(f"{GREEN('✔')} opencode CLI: {oc}")
        return True
    print(f"{YELLOW('•')} opencode CLI not found")
    return install_opencode()


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
def smoke_test(base, model):
    section("Quick test")
    if not yn("  Send a one-shot test message through the tunnel?", default=True):
        return
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
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read().decode())
        reply = (data["choices"][0]["message"]["content"] or "").strip()
        print(GREEN("  ✔ model replied:") + ("  " + reply[:90] if reply else DIM("  (empty)")))
    except Exception as exc:
        print(YELLOW(f"  • smoke test did not complete: {exc}"))
        print("    (First load can be slow while the model warms up - retry in the chat.)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print(BANNER.replace("{tunnel}", "cloudflared"))
    print(DIM(__doc__.splitlines()[0]))
    print(DIM(f"works on macOS (python3) and Windows (py -3)"))

    check_prereqs()

    base_ok = False
    base = ""
    models = []
    saved = None
    if SETUP_JSON.exists():
        try:
            saved = json.loads(SETUP_JSON.read_text())
        except Exception:
            saved = None
    if saved and saved.get("base") and yn(
        f"Reuse last setup ({saved['base']}, model {saved.get('model','')})?",
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

    result = configure_opencode(base, model)

    smoke_test(base, model)

    chat_url = start_local_chat(base, model)

    try:
        SETUP_JSON.write_text(json.dumps({
            "base": http_base(base),
            "model": model,
            "config": (result or {}).get("path"),
            "chat": chat_url,
        }, indent=2))
    except Exception:
        pass

    section("Done")
    print(GREEN("  Your setup is ready."))
    print(f"  BASE URL : {http_base(base)}/v1")
    print(f"  MODEL ID : {model}")
    if result:
        print(f"  config   : {result['path']}")
        print(YELLOW("  → Restart opencode, then chat with the model in the browser."))
    print()
    print(DIM("Tip: keep the Colab notebook running - the tunnel dies with the session."))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\naborted.")
        raise SystemExit(130)