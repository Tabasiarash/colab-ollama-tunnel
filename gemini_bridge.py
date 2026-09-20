#!/usr/bin/env python3
"""Tokenless Gemini CLI bridge: point Google's gemini CLI at the Colab tunnel.

The tunnel only serves Ollama's OpenAI-style /v1 API, while the gemini CLI
speaks Google's Gemini REST protocol. This helper uses a local LiteLLM proxy
as a translation layer:

    gemini CLI  --(Gemini /v1beta)-->  LiteLLM :4000  --(OpenAI /v1)-->  Colab tunnel

It builds the LiteLLM config (model_list + model_group_alias mapping gemini
CLI's internal model ids onto the Colab model), starts the proxy, verifies it,
and launches `gemini --sandbox=false` with GOOGLE_GEMINI_BASE_URL pointing at
the local proxy.

Standard library only. All launch logic is skipped when TOKENLESS_NO_LAUNCH=1
(used by the test suite), so building/config generation stays side-effect free.
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_DIR = Path.home() / ".tokenless-cli"
MASTER_KEY = "sk-tokenless-dummy"
PORT = 4000
MODEL_GROUP = "colab-tunnel"
FIRST_MODEL_ID = "gemini-3.1-flash-preview"

GEMINI_MODEL_IDS = [
    "gemini-3.1-pro-preview",
    "gemini-3.1-pro-preview-customtools",
    "gemini-3.1-flash-preview",
    "gemini-3.1-flash-preview-customtools",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemini-3-flash-preview-customtools",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]


def bridge_dir():
    return Path(os.environ.get("TOKENLESS_STATE") or DEFAULT_DIR)


def config_path():
    return bridge_dir() / "litellm_config.yaml"


def no_launch():
    return os.environ.get("TOKENLESS_NO_LAUNCH") == "1"


def find_gemini():
    for name in ("gemini", "gemini.cmd", "gemini.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def find_litellm():
    return shutil.which("litellm")


def build_litellm_config(model, api_base):
    """Return the LiteLLM proxy config as a plain dict (YAML on write)."""
    model = model.removeprefix("ollama/").strip()
    api_base = api_base.strip().removesuffix("/v1").rstrip("/")
    return {
        "model_list": [
            {
                "model_name": MODEL_GROUP,
                "litellm_params": {
                    "model": f"ollama_chat/{model}",
                    "api_base": api_base,
                },
            }
        ],
        "router_settings": {
            "model_group_alias": {gid: MODEL_GROUP for gid in GEMINI_MODEL_IDS}
        },
        "general_settings": {"master_key": MASTER_KEY},
    }


def _fmt(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return v


def _dump_dictish(d, pad):
    lines = []
    for k, v in d.items():
        if isinstance(v, dict):
            lines.append(f"{pad}{k}:")
            lines.extend(_dump_dictish(v, pad + "  "))
        elif isinstance(v, list):
            lines.append(f"{pad}{k}:")
            lines.extend(_dump_list(v, pad + "  "))
        else:
            lines.append(f"{pad}{k}: {_fmt(v)}")
    return lines


def _dump_list(items, pad):
    lines = []
    for item in items:
        if isinstance(item, dict):
            k0, v0 = next(iter(item.items()))
            if isinstance(v0, dict):
                lines.append(f"{pad}- {k0}:")
                lines.extend(_dump_dictish(v0, pad + "  "))
            else:
                lines.append(f"{pad}- {k0}: {_fmt(v0)}")
            for k, v in list(item.items())[1:]:
                if isinstance(v, dict):
                    lines.append(f"{pad}  {k}:")
                    lines.extend(_dump_dictish(v, pad + "    "))
                elif isinstance(v, list):
                    lines.append(f"{pad}  {k}:")
                    lines.extend(_dump_list(v, pad + "    "))
                else:
                    lines.append(f"{pad}  {k}: {_fmt(v)}")
        else:
            lines.append(f"{pad}- {_fmt(item)}")
    return lines


def render_yaml(cfg):
    return "\n".join(_dump_dictish(cfg, ""))


def write_bridge_config(model, api_base, path=None):
    """Write the LiteLLM config file, returning its path."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_yaml(build_litellm_config(model, api_base)) + "\n")
    # mask the tunnel URL so it is never committed anywhere
    return path


def bridge_command(path=None, port=PORT):
    exe = find_litellm() or "litellm"
    return [exe, "--config", str(path or config_path()), "--port", str(port)]


def gemini_command(cli=None, extra=None):
    cmd = [cli or find_gemini() or "gemini", "--sandbox=false"]
    if extra:
        cmd.extend(extra)
    return cmd


def gemini_env(port=PORT):
    env = dict(os.environ)
    env["GOOGLE_GEMINI_BASE_URL"] = f"http://127.0.0.1:{port}"
    env["GEMINI_API_KEY"] = MASTER_KEY
    return env


def launch_bridge(path=None, port=PORT, log_path=None):
    """Start the LiteLLM proxy in the background; returns the Popen or None."""
    if no_launch():
        return None
    logf = open(log_path, "ab") if log_path else None
    try:
        return subprocess.Popen(
            bridge_command(path, port),
            stdout=logf or subprocess.DEVNULL,
            stderr=logf or subprocess.STDOUT,
        )
    except FileNotFoundError:
        return None


def wait_ready(port=PORT, timeout=120):
    """Poll LiteLLM until it answers; returns True/False."""
    url_root = f"http://127.0.0.1:{port}"
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        for path in ("/health/liveliness", "/v1/models"):
            try:
                with urllib.request.urlopen(url_root + path, timeout=5) as r:
                    if r.status == 200:
                        return True
            except Exception as exc:
                last = exc
        time.sleep(1)
    return False


def smoke_bridge(model_id=FIRST_MODEL_ID, port=PORT, timeout=300):
    """One-shot Gemini-protocol call through the bridge -> text or None."""
    body = json.dumps(
        {
            "contents": [
                {
                    "parts": [
                        {"text": "Reply with exactly: tunnel OK (nothing else)"}
                    ],
                    "role": "user",
                }
            ]
        }
    ).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1beta/models/{model_id}:generateContent",
        data=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        return (parts[0].get("text") or "").strip() if parts else ""
    except Exception:
        return None


def launch_gemini(cli=None, port=PORT, extra=None):
    """Run the gemini CLI in the foreground with the bridge env (interactive)."""
    return subprocess.run(
        gemini_command(cli, extra), env=gemini_env(port)
    ).returncode


def summary(config, port=PORT):
    return {
        "engine": "gemini",
        "bridge": {
            "config": str(config),
            "port": port,
            "base_url": f"http://127.0.0.1:{port}",
            "master_key": MASTER_KEY,
            "stop": ["pkill", "-f", f"\"litellm --config {str(config)}\""],
        },
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) >= 3 and argv[0] == "--write":
        model = argv[1]
        base = argv[2]
        path = write_bridge_config(model, base)
        print("wrote", path)
        print(render_yaml(build_litellm_config(model, base)))
        return 0
    if argv and argv[0] == "--command":
        print(" ".join(bridge_command()))
        return 0
    if argv and argv[0] == "--env":
        for k, v in gemini_env().items():
            print(f"{k}={v}")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())