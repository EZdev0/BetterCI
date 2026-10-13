"""Tiny fake llama-server for tests: /health, /v1/messages (Anthropic) and
/v1/chat/completions (OpenAI). Returns whatever `REPLY` holds."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

REPLY = {"value": {}}
SEEN = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def _send(self, obj, code=200):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._send({"status": "ok"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append((self.path, body))
        if self.path == "/v1/messages":
            self._send({"id": "m", "type": "message", "role": "assistant", "model": "local", "stop_reason": "tool_use",
                        "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
                        "content": [{"type": "tool_use", "id": "t1", "name": "propose_fix", "input": REPLY["value"]}]})
        else:
            self._send({"choices": [{"message": {"role": "assistant", "content": json.dumps(REPLY["value"])}}]})


def start():
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
