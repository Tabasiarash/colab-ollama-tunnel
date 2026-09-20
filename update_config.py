#!/usr/bin/env python3
"""Point opencode's Ollama provider at the Tokenless CLI tunnel.

Usage (CLI):
    python3 update_config.py BASE_URL [MODEL_ID]

BASE_URL  https://<host>.trycloudflare.com   (a trailing /v1 is auto-added)
MODEL_ID  ollama tag, e.g. qwen2.5-coder:14b (default qwen2.5-coder:14b)

It patches ~/.config/opencode/opencode.jsonc in place: updates baseURL + the
single models entry, and sets a modern option set (tool_call, sampling
params, context/output limits).

The same logic is exposed as write_config() and reused by wizard.py.
"""
import json
import re
from pathlib import Path

CONFIG = Path.home() / ".config/opencode/opencode.jsonc"
EXAMPLE = Path(__file__).with_name("opencode.jsonc.example")
DEFAULT_MODEL = "qwen2.5-coder:14b"


def default_config_path():
    """Cross-platform opencode config path (macOS + Windows)."""
    return Path.home() / ".config" / "opencode" / "opencode.jsonc"


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


def apply_config(cfg, base, model):
    """Mutate a parsed opencode config dict for the given base URL + model."""
    model = model.removeprefix("ollama/").strip()
    base = base.removesuffix("/v1")
    ollama = cfg.setdefault("provider", {}).setdefault("ollama", {})
    if not ollama.get("name"):
        ollama["name"] = "Ollama (Colab)"
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
    entry.setdefault("options", {})
    entry["options"].setdefault("temperature", 0.7)
    entry["options"].setdefault("top_p", 0.8)
    entry["options"].setdefault("top_k", 20)
    entry["options"].setdefault("repetition_penalty", 1.05)
    entry.setdefault("limit", {"context": 32768, "output": 8192})

    cfg["model"] = "ollama/" + model
    return model


def write_config(base, model=None, path=None, backup=True):
    """Patch an opencode config file with the tunnel URL + model.

    Returns (config_path, model, base_without_v1). Raises FileNotFoundError
    when the config file is missing.
    """
    model = (model or DEFAULT_MODEL).removeprefix("ollama/").strip()
    base = base.strip().removesuffix("/v1").rstrip("/")
    path = Path(path or default_config_path())

    if not path.exists():
        raise FileNotFoundError(path)

    before = path.read_text()
    if backup and not path.with_suffix(".jsonc.bak").exists():
        path.with_suffix(".jsonc.bak").write_text(before)

    cfg = load_jsonc(path)
    apply_config(cfg, base, model)
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    return path, model, base


def main():
    import sys

    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    base = sys.argv[1].strip()
    model = (sys.argv[2].strip() if len(sys.argv) > 2 else None) or DEFAULT_MODEL

    try:
        path, model, base = write_config(base, model)
    except FileNotFoundError:
        print("config not found:", CONFIG)
        print("copy opencode.jsonc.example to get started:")
        print("  cp", EXAMPLE, CONFIG)
        return 1

    print("updated", path)
    print("  model   = ollama/" + model)
    print("  baseURL =", base + "/v1")
    print("Restart opencode for the change to take effect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())