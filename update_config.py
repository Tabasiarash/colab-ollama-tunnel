#!/usr/bin/env python3
"""Point opencode's Ollama provider at the Colab tunnel.

Usage:
    python3 update_config.py BASE_URL [MODEL_ID]

BASE_URL  https://<host>.trycloudflare.com   (a trailing /v1 is auto-added)
MODEL_ID  ollama tag, e.g. qwen2.5-coder:14b (default qwen2.5-coder:14b)

It patches ~/.config/opencode/opencode.jsonc in place: updates baseURL + the
single models entry, and sets a modern option set (tool_call, sampling
params, context/output limits).
"""
import json
import re
import sys
from pathlib import Path

CONFIG = Path.home() / ".config/opencode/opencode.jsonc"
EXAMPLE = Path(__file__).with_name("opencode.jsonc.example")
DEFAULT_MODEL = "qwen2.5-coder:14b"


def load_jsonc(path):
    """Parse a .jsonc file (strips //-comments and trailing commas)."""
    raw = path.read_text()
    out = []
    in_str = False
    esc = False
    for c in raw:
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
                out.append(c)
            elif c == "/" and out and out[-1] == "/":
                out.pop()
                while out and out[-1] not in "\n\r":
                    out.pop()
            elif c == "," and out and out[-1] == ",":
                pass
            else:
                out.append(c)
    text = "".join(out)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return json.loads(text)


def model_name(model):
    prefix, _, tag = model.partition(":")
    return f"{prefix} {tag} (Colab)"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    base = sys.argv[1].strip().rstrip("/")
    base = base.removesuffix("/v1")
    model = (sys.argv[2].strip() if len(sys.argv) > 2 else None) or DEFAULT_MODEL
    model = model.removeprefix("ollama/")

    if not CONFIG.exists():
        print("config not found:", CONFIG)
        print("copy opencode.jsonc.example to get started:")
        print("  cp", EXAMPLE, CONFIG)
        return 1

    cfg = load_jsonc(CONFIG)
    name = (cfg.get("provider", {}) or {}).get("ollama", {}).get("name") or "Ollama (Colab)"

    ollama = cfg.setdefault("provider", {}).setdefault("ollama", {})
    ollama["name"] = name
    options = ollama.setdefault("options", {})
    options["baseURL"] = base + "/v1"

    models = ollama.setdefault("models", {})
    if model not in models:
        old = next(iter(models), None)
        if old:
            models[model] = models.pop(old)
            models[model]["name"] = model_name(model)
        else:
            models[model] = {"name": model_name(model), "tool_call": True}
    entry = models[model]
    entry["tool_call"] = True
    if not entry.get("options"):
        entry["options"] = {}
    entry["options"].setdefault("temperature", 0.7)
    entry["options"].setdefault("top_p", 0.8)
    entry["options"].setdefault("top_k", 20)
    entry["options"].setdefault("repetition_penalty", 1.05)
    if not entry.get("limit"):
        entry["limit"] = {"context": 32768, "output": 8192}

    cfg["model"] = "ollama/" + model

    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    print("updated", CONFIG)
    print("  model   = ollama/" + model)
    print("  baseURL =", base + "/v1")
    print("Restart opencode for the change to take effect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())