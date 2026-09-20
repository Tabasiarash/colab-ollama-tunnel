#!/usr/bin/env python3
"""Tokenless CLI - GUI install wizard (Tkinter; standard library only).

A windowed version of wizard.py: paste the tunnel BASE URL, pick a model,
choose your engine (opencode and/or the Tokenless Gemini CLI via the LiteLLM
bridge), then write config, smoke-test, open the browser chat, and launch the
Gemini bridge - all from the same UI.

Packaged with PyInstaller (build/mac_build.sh, build/win_build.bat); also runs
directly with:  python3 wizard_gui.py
"""
import os
import shutil
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gemini_bridge as GB  # noqa: E402
from update_config import default_config_path, write_config  # noqa: E402
from version import VERSION  # noqa: E402
from wizard import CHAT_HTML, http_base, list_models, normalize_base, smoke_status  # noqa: E402

APP_NAME = "Tokenless CLI Wizard"
ENGINES = {
    "opencode": "opencode (direct, recommended)",
    "gemini": "gemini CLI (via local LiteLLM bridge)",
    "both": "both",
}


def resource_path(rel):
    """Resolve a bundled resource (sys._MEIPASS when frozen, repo dir otherwise)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / rel


def chat_html_path():
    for cand in (resource_path("web/chat.html"), CHAT_HTML):
        if Path(cand).exists():
            return Path(cand)
    return Path(cand)


class WizardApp:
    def __init__(self, root):
        self.root = root
        self.base = None
        self.models = []
        self.engine_var = tk.StringVar(value="opencode")
        self.model_var = tk.StringVar()

        root.title(f"{APP_NAME} v{VERSION}")
        root.geometry("700x600")
        root.minsize(640, 560)

        self._build_header()
        self._build_step1()
        self._build_step2()
        self._build_actions()
        self._build_log()
        self._build_status()

    # -- UI construction ------------------------------------------------------
    def _build_header(self):
        head = tk.Frame(self.root, padx=14, pady=10)
        head.pack(fill="x")
        tk.Label(head, text=APP_NAME, font=("Helvetica", 15, "bold")).pack(anchor="w")
        tk.Label(
            head,
            text="Zero API keys · zero token costs · free Colab GPU for opencode & the Gemini CLI",
            fg="#555",
        ).pack(anchor="w")

    def _build_step1(self):
        f = ttk.LabelFrame(self.root, text=" 1 · Tunnel BASE URL ", padding=8)
        f.pack(fill="x", padx=14, pady=(6, 0))
        row = ttk.Frame(f)
        row.pack(fill="x")
        self.url_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.url_var).pack(side="left", fill="x", expand=True)
        self.check_btn = ttk.Button(row, text="Check tunnel", command=self.on_check)
        self.check_btn.pack(side="right", padx=(8, 0))
        self.url_hint = tk.Label(f, text="paste https://xxxx.trycloudflare.com from the notebook",
                                 fg="#888", font=("", 10))
        self.url_hint.pack(anchor="w", pady=(4, 0))

    def _build_step2(self):
        f = ttk.LabelFrame(self.root, text=" 2 · Model & engine ", padding=8)
        f.pack(fill="x", padx=14, pady=6)
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Model").pack(side="left")
        self.model_cb = ttk.Combobox(row, textvariable=self.model_var, state="readonly", width=36)
        self.model_cb.pack(side="left", padx=(8, 20))
        ttk.Label(row, text="Engine").pack(side="left")
        for i, (key, label) in enumerate(ENGINES.items()):
            ttk.Radiobutton(row, text=label, value=key, variable=self.engine_var,
                            command=self._on_engine).pack(side="left", padx=(8 if i else 8, 0))
        self.engine_hint = tk.Label(f, text="opencode: patches ~/.config/opencode/opencode.jsonc",
                                    fg="#888", font=("", 10))
        self.engine_hint.pack(anchor="w", pady=(4, 0))

    def _build_actions(self):
        f = ttk.Frame(self.root)
        f.pack(fill="x", padx=14, pady=6)
        self.write_btn = ttk.Button(f, text="Write config", command=self.on_write_config)
        self.test_btn = ttk.Button(f, text="Smoke test", command=self.on_smoke, state="disabled")
        self.chat_btn = ttk.Button(f, text="Open browser chat", command=self.on_chat, state="disabled")
        self.gemini_btn = ttk.Button(f, text="Launch Gemini CLI", command=self.on_launch_gemini,
                                     state="disabled")
        for b in (self.write_btn, self.test_btn, self.chat_btn, self.gemini_btn):
            b.pack(side="left", padx=(0, 8))

    def _build_log(self):
        self.log = scrolledtext.ScrolledText(self.root, height=14, state="disabled",
                                             font=("Menlo", 10))
        self.log.pack(fill="both", expand=True, padx=14, pady=6)
        for tag, color in (("ok", "#1a7f37"), ("err", "#cf222e"), ("warn", "#9a6700"),
                           ("info", "#333"), ("dim", "#888")):
            self.log.tag_config(tag, foreground=color)

    def _build_status(self):
        self.status = tk.Label(self.root, text="", anchor="w", fg="#555")
        self.status.pack(fill="x", padx=14, pady=(0, 8))

    # -- helpers --------------------------------------------------------------
    def _log(self, text, tag="info"):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.configure(state="disabled")
        self.log.see("end")

    def _busy(self, flag):
        state = "disabled" if flag else "normal"
        for b in (self.check_btn, self.write_btn, self.test_btn):
            b.configure(state=state)
        self.root.update_idletasks()

    def _run_async(self, work, done):
        def worker():
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001
                result = ("error", str(exc))
            self.root.after(0, lambda: done(result))
        threading.Thread(target=worker, daemon=True).start()

    def _on_engine(self):
        hints = {
            "opencode": "opencode: patches ~/.config/opencode/opencode.jsonc (a .bak is kept)",
            "gemini": "gemini CLI: writes the LiteLLM bridge config + launcher "
                      f"(Tokenless Gemini CLI, port {GB.PORT})",
            "both": "both: opencode config + the Tokenless Gemini CLI bridge launcher",
        }
        self.engine_hint.configure(text=hints[self.engine_var.get()])

    def _require(self, thing, name):
        if not thing:
            self._log(f"{name} first.", "err")
            return False
        return True

    # -- actions --------------------------------------------------------------
    def on_check(self):
        raw = self.url_var.get().strip()
        if not raw:
            self._log("Please paste the BASE URL from the notebook.", "err")
            return
        base = normalize_base(raw)
        self._log(f"checking {base}/v1/models ...", "info")
        self._busy(True)

        def work():
            return ("ok", list_models(base))

        def done(res):
            self._busy(False)
            tag, payload = res
            if tag != "ok":
                self._log(f"  ✘ {payload}", "err")
                self.status.configure(text="tunnel unreachable")
                return
            self.base, self.models = base, payload
            self.model_cb.configure(values=sorted(self.models) or ["qwen2.5-coder:14b"])
            if self.models:
                self.model_var.set(sorted(self.models)[0])
            else:
                self.model_var.set("qwen2.5-coder:14b")
            self._log(f"  ✔ reachable - {len(self.models)} model(s) on this tunnel", "ok")
            self.status.configure(text="tunnel OK")
            self.test_btn.configure(state="normal")
            self.chat_btn.configure(state="normal")
            self.gemini_btn.configure(state="normal" if GB.find_gemini() or GB.find_litellm() else "disabled")

        self._run_async(work, done)

    def on_write_config(self):
        if not self._require(self.base, "Check the tunnel"):
            return
        model = self.model_var.get().strip() or "qwen2.5-coder:14b"
        engine = self.engine_var.get()
        self._log(f"-- writing config for engine '{engine}', model {model} --", "dim")

        if engine in ("opencode", "both"):
            cfg = default_config_path()
            if not cfg.exists():
                src = resource_path("opencode.jsonc.example")
                if not src.exists():
                    self._log("  ✘ opencode.jsonc.example not found next to the wizard.", "err")
                else:
                    cfg.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(src, cfg)
                    self._log(f"  created {cfg} from the example template", "info")
            try:
                path, m, b = write_config(self.base, model)
                self._log(f"  ✔ opencode config updated: {path}", "ok")
                self._log(f"    model = ollama/{m}   baseURL = {b}/v1", "dim")
                self._log("    restart opencode to pick it up", "warn")
            except Exception as exc:  # noqa: BLE001
                self._log(f"  ✘ {exc}", "err")

        if engine in ("gemini", "both"):
            try:
                p = GB.write_bridge_config(model, http_base(self.base))
                self._log(f"  ✔ gemini bridge config: {p}", "ok")
                self._log(f"    Tokenless Gemini CLI -> LiteLLM:{GB.PORT} -> {http_base(self.base)}", "dim")
                sh = GB.write_launcher(path=(Path(p).parent / "start_gemini.sh"))
                self._log(f"    launcher: {sh}", "dim")
            except Exception as exc:  # noqa: BLE001
                self._log(f"  ✘ {exc}", "err")

        self._log("config written.", "info")

    def on_smoke(self):
        if not self._require(self.base, "Check the tunnel"):
            return
        model = self.model_var.get().strip() or "qwen2.5-coder:14b"
        self._log("sending one-shot test message through the tunnel ...", "info")
        self._busy(True)

        def work():
            return smoke_status(self.base, model)

        def done(res):
            self._busy(False)
            ok, reply = res
            if ok:
                self._log("  ✔ model replied: " + (reply[:90] if reply else "(empty)"), "ok")
                self.status.configure(text="smoke test OK")
            else:
                self._log(f"  • smoke test did not complete: {reply}", "warn")

        self._run_async(work, done)

    def on_chat(self):
        if not self._require(self.base, "Check the tunnel"):
            return
        html = chat_html_path()
        if not html.exists():
            self._log(f"  ✘ chat page missing: {html}", "err")
            return
        q = urllib.parse.urlencode({"base": http_base(self.base),
                                    "model": self.model_var.get().strip() or "qwen2.5-coder:14b"})
        url = html.as_uri() + "?" + q
        webbrowser.open(url)
        self._log(f"opened browser chat:\n  {url}", "info")

    def on_launch_gemini(self):
        if not self._require(self.base, "Check the tunnel"):
            return
        if not GB.find_litellm():
            messagebox.showwarning(
                "LiteLLM missing",
                "The Gemini bridge needs LiteLLM.\n\npython3 -m pip install litellm",
            )
            return
        if not GB.find_gemini():
            messagebox.showwarning(
                "Gemini CLI missing",
                "gemini CLI not found.\n\nnpm install -g @google/gemini-cli",
            )
            return
        model = self.model_var.get().strip() or "qwen2.5-coder:14b"
        try:
            GB.write_bridge_config(model, http_base(self.base))
            script = GB.write_launcher()
        except Exception as exc:  # noqa: BLE001
            self._log(f"  ✘ {exc}", "err")
            return
        self._log(f"  ✔ launcher ready: {script}", "ok")
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(script))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["open", str(script)])
        except Exception as exc:  # noqa: BLE001
            self._log(f"  ✘ could not open launcher: {exc}", "err")
            return
        self._log("launched - bridge starts on " + f"http://127.0.0.1:{GB.PORT}, "
                   + "then gemini --sandbox=false runs", "info")


def main():
    root = tk.Tk()
    WizardApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())