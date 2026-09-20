"""Mock Ollama OpenAI-compatible server for tests.

Replicates the /v1 surface the wizard + browser chat depend on:
  - GET  /v1/models            -> model list (also /api/tags fallback)
  - POST /v1/chat/completions  -> JSON or SSE streaming replies
  - OPTIONS preflight + CORS headers (like Ollama's server/routes.go)
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS = [
    {"id": "qwen2.5-coder:14b", "object": "model"},
    {"id": "qwen2.5-coder:7b", "object": "model"},
    {"id": "qwen3-coder:30b-a3b-q4_K_M", "object": "model"},
]

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept",
    "Access-Control-Max-Age": "86400",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in CORS.items():
            self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204)

    def do_GET(self):
        if self.path == "/v1/models":
            self._send(200, json.dumps({"object": "list", "data": MODELS}).encode())
        elif self.path == "/api/tags":
            self._send(200, json.dumps({"models": MODELS}).encode())
        else:
            self._send(404, json.dumps({"error": {"message": "not found"}}).encode())

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self._send(404, json.dumps({"error": {"message": "not found"}}).encode())
            return
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length) or b"{}")
        stream = bool(req.get("stream"))
        model = req.get("model", "qwen2.5-coder:14b")
        if stream:
            chunks = [
                {"id": "c", "object": "chat.completion.chunk", "model": model, "choices": [
                    {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
                {"id": "c", "object": "chat.completion.chunk", "model": model, "choices": [
                    {"index": 0, "delta": {"content": "tunnel "}, "finish_reason": None}]},
                {"id": "c", "object": "chat.completion.chunk", "model": model, "choices": [
                    {"index": 0, "delta": {"content": "OK"}, "finish_reason": None}]},
                {"id": "c", "object": "chat.completion.chunk", "model": model, "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
            body = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks).encode() + b"data: [DONE]\n\n"
            self._send(200, body, ctype="text/event-stream")
        else:
            reply = {"id": "c", "object": "chat.completion", "model": model,
                     "choices": [{"index": 0, "message": {"role": "assistant", "content": "tunnel OK"},
                                  "finish_reason": "stop"}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}
            self._send(200, json.dumps(reply).encode())

    def log_message(self, *args):
        pass


def start(port=11435):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread