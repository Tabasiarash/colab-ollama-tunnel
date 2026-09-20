import json
import uuid

CELLS = []


def md(source):
    CELLS.append({"cell_type": "markdown", "metadata": {"id": uuid.uuid4().hex[:14]}, "source": source})


def code(source):
    CELLS.append(
        {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {"id": uuid.uuid4().hex[:14]},
            "outputs": [],
            "source": source,
        }
    )


md(
    """# Tokenless CLI - free Colab GPU for opencode & the Gemini CLI

Runs [Ollama](https://ollama.com) on this Colab VM, pulls a coding model sized to the resources that
were granted, and exposes it through a temporary public HTTPS tunnel ([cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/))
so [opencode](https://opencode.ai) or any OpenAI-compatible client can reach it.

## What you get

| Runtime | Detected hardware | Model pulled |
| --- | --- | --- |
| Colab free | T4 (14GB+ VRAM) | `qwen2.5-coder:14b` (default) |
| CPU only / small GPU | no GPU, <14GB VRAM | `qwen2.5-coder:7b` |
| Colab Pro / Pro+ / PayGo | L4, A100, V100, L40S (22GB+ VRAM) | `qwen3-coder:30b-a3b-q4_K_M` |

Any model can be forced with the `MODEL_OVERRIDE` variable (e.g. `gemma4:12b`, `qwen3:8b`,
`gemma4:31b`, `devstal`).

> **Security note:** the tunnel is public and unauthenticated - anyone with the URL can call the API.
> Treat it like a password, don't paste it into code, and note the session (and URL) dies
> after ~12 h or when you close the runtime.
"""
)

md(
    """## Setup

1. **Runtime > Change runtime type** -> Hardware accelerator: **T4 GPU** (free).
2. **Runtime > Run all** (~10-20 min the first time).
3. Leave this tab open. The endpoint dies with the session.

## After it finishes

The notebook prints a **BASE URL** (`https://xxxx.trycloudflare.com`) and the **model id** it pulled.

Fastest wiring: run the printed `update_config.py` command on your machine, or copy the exact
`opencode.jsonc` block it prints.
"""
)

code(
    """import platform, shutil, subprocess, sys

print("python:", sys.version.split()[0])
print("host:", platform.platform())

def detect_gpu():
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        if out:
            name, mem = out.split(",", 1)
            return {"name": name.strip(), "vram_mb": int(mem.replace("MiB", "").strip())}
    except Exception:
        pass
    return None

GPU = detect_gpu()
if GPU:
    print(f"GPU: {GPU['name']} ({GPU['vram_mb']} MiB VRAM)")
else:
    print("GPU: none (CPU-only)")

import psutil
print("ram_gb: %.1f" % (psutil.virtual_memory().total / 1e9))
d = shutil.disk_usage("/")
print(f"disk_free_gb: {d.free / 1e9:.0f}")
print("root:", subprocess.check_output(["whoami"], text=True).strip())
"""
)

md(
    """## Model selection

Picks the best model that fits the granted GPU. Override by setting `MODEL_OVERRIDE` to any real
[Ollama tag](https://ollama.com/search?c=tools) (e.g. `gemma4:12b`). Choosing something too big for
the free tier risks OOM kills - keep 20GB+ models on Colab Pro / PayGo hardware.
"""
)

code(
    """import json, os, subprocess

# --- Set this to force a model, e.g. "gemma4:12b" or "qwen3-coder:30b-a3b-q4_K_M". "" auto-picks. ---
MODEL_OVERRIDE = ""

TAGS = {
    "big":   "qwen3-coder:30b-a3b-q4_K_M",   # ~19GB, needs 24GB+ VRAM (L4/A100/Pro)
    "mid":   "qwen2.5-coder:14b",            # ~9GB, best free-tier fit (T4)
    "small": "qwen2.5-coder:7b",             # ~5GB, CPU / small VRAM
}

def vram_mb():
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader"],
            text=True, stderr=subprocess.DEVNULL,
        )
        return int(out.replace("MiB", "").strip())
    except Exception:
        return 0

vram = vram_mb()
if MODEL_OVERRIDE.strip():
    MODEL = MODEL_OVERRIDE.strip().lstrip("ollama/")
    print(f"MODEL overridden -> {MODEL}")
elif vram >= 22000:
    MODEL = TAGS["big"]
    print(f"GPU tier: big (22GB+ VRAM) -> {MODEL}")
elif vram >= 14000:
    MODEL = TAGS["mid"]
    print(f"GPU tier: mid (14GB+ VRAM) -> {MODEL}")
else:
    MODEL = TAGS["small"]
    print(f"GPU tier: small/CPU -> {MODEL}")

open("model_id.txt", "w").write(MODEL)
"""
)

code(
    """import os, subprocess

URL = "https://ollama.com/download/ollama-linux-amd64.tar.zst"
print("downloading ollama ...", flush=True)
subprocess.run(["curl", "-fsSL", "-o", "ollama.tgz.zst", URL], check=True)

print("extracting to /usr ...", flush=True)
try:
    subprocess.run(["tar", "-x", "-C", "/usr", "-f", "ollama.tgz.zst"], check=True)
except subprocess.CalledProcessError:
    print("zstd support missing - installing zstd ...", flush=True)
    subprocess.run(["apt-get", "update", "-qq"], check=True)
    subprocess.run(["apt-get", "install", "-y", "-qq", "zstd"], check=True)
    subprocess.run(["tar", "--zstd", "-x", "-C", "/usr", "-f", "ollama.tgz.zst"], check=True)

BIN = "/usr/local/bin/ollama"
if not os.path.exists(BIN):
    BIN = "/usr/bin/ollama"
os.environ["OLLAMA_BIN"] = BIN
print("ollama binary:", BIN)
print("version:", subprocess.check_output([BIN, "--version"], text=True).strip())
"""
)

code(
    """import os, subprocess, time

BIN = os.environ.get("OLLAMA_BIN", "/usr/local/bin/ollama")
env = dict(os.environ, OLLAMA_HOST="0.0.0.0", OLLAMA_KEEP_ALIVE="10m")
logf = open("ollama.log", "a", buffering=1)

proc = subprocess.Popen(
    [BIN, "serve"], env=env, stdout=logf, stderr=subprocess.STDOUT,
    start_new_session=True,
)
print("ollama serve pid:", proc.pid)

def api_ok(path="/api/tags", timeout=3):
    try:
        with __import__("urllib.request").request.urlopen("http://localhost:11434" + path, timeout=timeout) as r:
            return r.status
    except Exception:
        return None

for i in range(120):
    if api_ok() == 200:
        print(f"ollama ready after {i + 1}s")
        break
    time.sleep(1)
else:
    print("!! ollama did not come up in 120s - log tail:")
    print(open("ollama.log").read()[-4000:])
    raise SystemExit(1)
"""
)

code(
    """import json, os, subprocess, time

BIN = os.environ.get("OLLAMA_BIN", "/usr/local/bin/ollama")
MODEL = open("model_id.txt").read().strip()

print(f"pulling {MODEL} ...", flush=True)
rc = subprocess.run([BIN, "pull", MODEL]).returncode
if rc != 0:
    raise SystemExit(f"pull failed for {MODEL} (rc={rc})")

print("warm-up request ...", flush=True)
smoke = None
for _ in range(6):
    try:
        body = json.dumps({
            "model": MODEL,
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "stream": False,
        }).encode()
        req = __import__("urllib.request").request.Request(
            "http://localhost:11434/api/chat", data=body,
            headers={"Content-Type": "application/json"},
        )
        with __import__("urllib.request").request.urlopen(req, timeout=600) as r:
            smoke = json.load(r)["message"]["content"]
        break
    except Exception as e:
        print("  warm-up attempt failed:", e, flush=True)
        time.sleep(5)

print("sample reply:", (smoke or "").strip()[:120])
print()
print("=" * 60)
print("MODEL ID ->", MODEL)
print("=" * 60)
"""
)

md(
    """## Connect opencode (on your machine)

Once the next cell prints a **BASE URL**, point opencode (or any OpenAI-compatible client) at `BASE/v1`.

On your machine run the update script (it patches `~/.config/opencode/opencode.jsonc` in place):

```bash
python3 update_config.py https://<BASE>.trycloudflare.com qwen2.5-coder:14b
```

Or paste the exact `opencode.jsonc` block printed in the cell after the tunnel starts. Restart
opencode after changing it.
"""
)

code(
    """import os, re, subprocess, time

cli = "./cloudflared"
if not os.path.exists(cli):
    print("downloading cloudflared ...", flush=True)
    subprocess.run(
        ["curl", "-fsSL", "-o", cli,
         "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"],
        check=True,
    )
    os.chmod(cli, 0o755)

try:
    with __import__("urllib.request").request.urlopen("http://localhost:11434/api/tags", timeout=3) as r:
        print("ollama responding:", r.status)
except Exception as e:
    print("WARN: ollama not reachable:", e)

logf = open("tunnel.log", "ab", buffering=0)
proc = subprocess.Popen(
    [cli, "tunnel", "--url", "http://localhost:11434", "--no-autoupdate"],
    stdout=logf, stderr=subprocess.STDOUT, start_new_session=True,
)
print("cloudflared pid:", proc.pid)

pat = re.compile(r"https://[a-z0-9\\-]+\\.trycloudflare\\.com")
url = None
for _ in range(120):
    time.sleep(1)
    if proc.poll() is not None:
        break
    snip = open("tunnel.log", "r", encoding="utf-8", errors="ignore").read()
    m = pat.search(snip)
    if m:
        url = m.group(0)
        break

if not url:
    print("!! cloudflared produced no tunnel URL - log tail:")
    print(open("tunnel.log", "r", errors="ignore").read()[-3000:])
    raise SystemExit(1)

open("tunnel_url.txt", "w").write(url)
print()
print("=" * 72)
print("BASE URL  ->  " + url + "/v1")
print("=" * 72)
print()
print("Health check:  curl " + url + "/api/tags")
print("Tunnel is unauthenticated - keep this URL private and expect it to die with the session.")
"""
)

code(
    """import json, os

base = open("tunnel_url.txt").read().strip()
model = open("model_id.txt").read().strip()
name = model.split(":")[0].upper() + " " + model.split(":")[1] + " (Colab)"

CFG = {
    "$schema": "https://opencode.ai/config.json",
    "model": "ollama/" + model,
    "provider": {
        "ollama": {
            "npm": "@ai-sdk/openai-compatible",
            "name": "Ollama (Colab)",
            "options": {"baseURL": base + "/v1"},
            "models": {
                model: {
                    "name": name,
                    "tool_call": True,
                    "options": {
                        "temperature": 0.7,
                        "top_p": 0.8,
                        "top_k": 20,
                        "repetition_penalty": 1.05,
                    },
                    "limit": {"context": 32768, "output": 8192},
                }
            },
        }
    },
}
print("Paste into ~/.config/opencode/opencode.jsonc")
print("=" * 72)
print(json.dumps(CFG, indent=2))
print("=" * 72)
print()
print("Or run on your machine:")
print(f"  python3 update_config.py {base} {model}")
"""
)

code(
    """import datetime, os, time, urllib.request

BASE = "http://localhost:11434/api/tags"
tun = None
if os.path.exists("tunnel_url.txt"):
    tun = open("tunnel_url.txt").read().strip()

print("Keep-alive running. Leave this cell/notebook running.")
print("tunnel:", tun)
print("Stop later with Runtime > Stop runtime. Each restart = new tunnel URL.")
while True:
    try:
        with urllib.request.urlopen(BASE, timeout=8) as r:
            status = r.status
    except Exception as e:
        status = "DOWN (" + e.__class__.__name__ + ")"
    print(datetime.datetime.now().strftime("%H:%M:%S"), "ollama:", status, flush=True)
    time.sleep(300)
"""
)

nb = {
    "metadata": {
        "colab": {"name": "colab_ollama.ipynb", "provenance": [], "toc_visible": True},
        "kernelspec": {"name": "python3", "display_name": "Python 3"},
        "language_info": {"name": "python"},
        "accelerator": "GPU",
    },
    "nbformat": 4,
    "nbformat_minor": 0,
    "cells": CELLS,
}

out = "/Users/arash/kaggle_ollama/colab_ollama.ipynb"
with open(out, "w") as f:
    json.dump(nb, f, indent=1)
print("wrote", out, "cells:", len(CELLS))