"""
kugua — API Server (REST wrapper for kugua kernel)
v0.2.1

Lightweight HTTP API for external monitoring and task submission.
Used by EcosystemMonitor for independent verification experiments.

Endpoints:
  GET  /api/kb/snapshot      — knowledge base status
  POST /api/task             — submit a task
  GET  /api/task/{id}/status — query task status

Usage:
    python -m kugua.api_server --port 5000
"""
from __future__ import annotations
import json, time, uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional


# Module-level task store (shared across handler instances)
_task_store: dict = {}

class KuguaAPIHandler(BaseHTTPRequestHandler):
    """HTTP handler for kugua REST API."""

    kb = None          # Set before server start
    graph_kb = None
    double_loop = None
    mobius = None
    executor = None

    def _json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/kb/snapshot":
            data = {"total_rules": 0, "level_distribution": {}, "rules": []}
            if self.kb:
                entries = getattr(self.kb, 'entries', {})
                dist = {}
                for e in entries.values():
                    lv = getattr(e, 'level', 'L1')
                    dist[lv] = dist.get(lv, 0) + 1
                data = {
                    "total_rules": len(entries),
                    "level_distribution": dist,
                    "graph_nodes": getattr(self.graph_kb, 'node_count', 0) if self.graph_kb else 0,
                }
            self._json(data)
        elif self.path.startswith("/api/task/") and self.path.endswith("/status"):
            task_id = self.path.split("/")[-2]
            task = _task_store.get(task_id, {"status": "not_found"})
            self._json(task)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/api/task":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length)) if length > 0 else {}
            except json.JSONDecodeError:
                self._json({"error": "invalid JSON"}, 400)
                return
            control_type = self.headers.get("X-Kugua-Control-Type", "real")

            task_id = str(uuid.uuid4())[:8]
            _task_store[task_id] = {
                "task_id": task_id, "status": "submitted",
                "control_type": control_type, "description": body.get("description", ""),
            }
            self._json({"task_id": task_id, "status": "submitted"}, 201)
        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, format, *args):
        pass  # Suppress default logging


def start_server(host: str = "0.0.0.0", port: int = 5000,
                 kb=None, graph_kb=None, double_loop=None,
                 mobius=None, executor=None):
    """Start the kugua API server."""
    KuguaAPIHandler.kb = kb
    KuguaAPIHandler.graph_kb = graph_kb
    KuguaAPIHandler.double_loop = double_loop
    KuguaAPIHandler.mobius = mobius
    KuguaAPIHandler.executor = executor

    server = HTTPServer((host, port), KuguaAPIHandler)
    print(f"kugua API server listening on {host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
