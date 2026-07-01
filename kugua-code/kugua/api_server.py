"""
kugua — REST API Server v0.3.0

增强版 HTTP API，支持认知监护模式 + 性能基准。
可作为 Sidecar 容器部署，被 LangGraph / AutoGen / CrewAI 等编排框架调用。

Endpoints:
  # 认知监护
  POST /api/guardian/check       — 提交 Agent 输出供监护审查
  GET  /api/guardian/session/{id} — 查询监护会话状态
  GET  /api/guardian/sessions     — 列出所有监护会话
  GET  /api/guardian/benchmark    — 性能基准报告

  # 知识库 (保持兼容)
  GET  /api/kb/snapshot           — 知识库状态快照

  # 任务 (保持兼容)
  POST /api/task                  — 提交任务
  GET  /api/task/{id}/status      — 查询任务状态

  # 健康检查
  GET  /api/health                — 存活检查
  GET  /api/health/ready          — 就绪检查

Usage:
    python -m kugua.api_server --port 5000

Sidecar 部署:
    docker run -p 5000:5000 kugua-sidecar
    # LangGraph Agent 每步调用 POST /api/guardian/check
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, Optional


class KuguaAPIHandler(BaseHTTPRequestHandler):
    """HTTP handler for kugua REST API v0.3.0."""

    # 静态依赖注入
    kb = None
    graph_kb = None
    guardian = None
    safety = None
    csd = None
    task_store: dict = {}
    start_time: float = time.time()

    def _json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return {}

    def _path_parts(self) -> list:
        return [p for p in self.path.strip("/").split("/") if p]

    # ── 路由 ────────────────────────────────────────────────

    def do_GET(self):
        parts = self._path_parts()

        # /api/health
        if self.path == "/api/health":
            self._json({"status": "alive", "uptime_s": round(time.time() - self.start_time, 1)})

        # /api/health/ready
        elif self.path == "/api/health/ready":
            ready = self.guardian is not None
            self._json({"ready": ready, "modules": {
                "kb": self.kb is not None,
                "guardian": self.guardian is not None,
                "safety": self.safety is not None,
                "csd": self.csd is not None,
            }})

        # /api/kb/snapshot
        elif self.path == "/api/kb/snapshot":
            self._handle_kb_snapshot()

        # /api/guardian/sessions
        elif self.path == "/api/guardian/sessions":
            self._handle_list_sessions()

        # /api/guardian/benchmark
        elif self.path == "/api/guardian/benchmark":
            self._handle_benchmark()

        # /api/guardian/session/{id}
        elif len(parts) >= 3 and parts[0] == "api" and parts[1] == "guardian" and parts[2] == "session":
            session_id = parts[3] if len(parts) > 3 else ""
            self._handle_get_session(session_id)

        # /api/task/{id}/status
        elif len(parts) >= 3 and parts[0] == "api" and parts[1] == "task" and parts[-1] == "status":
            task_id = parts[2]
            task = self.task_store.get(task_id, {"status": "not_found"})
            self._json(task)

        else:
            self._json({"error": "not found", "path": self.path}, 404)

    def do_POST(self):
        parts = self._path_parts()

        # /api/guardian/check
        if self.path == "/api/guardian/check":
            self._handle_guardian_check()

        # /api/task
        elif self.path == "/api/task":
            self._handle_task_submit()

        else:
            self._json({"error": "not found", "path": self.path}, 404)

    def do_OPTIONS(self):
        """CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Kugua-Control-Type")
        self.end_headers()

    # ── 处理器 ──────────────────────────────────────────────

    def _handle_guardian_check(self):
        """POST /api/guardian/check — 认知监护审查。"""
        if not self.guardian:
            self._json({"error": "guardian not configured"}, 503)
            return

        body = self._read_body()
        session_id = body.get("session_id", self.headers.get("X-Kugua-Session-ID", "default"))

        verdict = self.guardian.check(
            agent_output=body.get("agent_output", ""),
            operation=body.get("operation", ""),
            confidence=float(body.get("confidence", 1.0)),
            session_id=session_id,
            error_type=body.get("error_type", ""),
            gv_id=body.get("gv_id", ""),
            context=body.get("context"),
        )

        self._json(verdict.to_dict(), 200)

    def _handle_list_sessions(self):
        """GET /api/guardian/sessions — 列出所有监护会话。"""
        if not self.guardian:
            self._json({"error": "guardian not configured"}, 503)
            return

        sessions = [s.to_dict() for s in self.guardian.list_sessions()]
        self._json({"sessions": sessions, "total": len(sessions)})

    def _handle_get_session(self, session_id: str):
        """GET /api/guardian/session/{id} — 查询单个会话。"""
        if not self.guardian:
            self._json({"error": "guardian not configured"}, 503)
            return

        session = self.guardian.get_session(session_id)
        if session:
            self._json(session.to_dict())
        else:
            self._json({"error": f"session '{session_id}' not found"}, 404)

    def _handle_benchmark(self):
        """GET /api/guardian/benchmark — 性能基准报告。"""
        if not self.guardian:
            self._json({"error": "guardian not configured"}, 503)
            return

        self._json(self.guardian.benchmark_report())

    def _handle_kb_snapshot(self):
        """GET /api/kb/snapshot — 知识库快照。"""
        data = {"total_rules": 0, "level_distribution": {}, "graph_nodes": 0}
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

    def _handle_task_submit(self):
        """POST /api/task — 提交任务。"""
        body = self._read_body()
        control_type = self.headers.get("X-Kugua-Control-Type", "real")
        task_id = str(uuid.uuid4())[:8]
        self.task_store[task_id] = {
            "task_id": task_id,
            "status": "submitted",
            "control_type": control_type,
            "description": body.get("description", ""),
        }
        self._json({"task_id": task_id, "status": "submitted"}, 201)

    def log_message(self, format, *args):
        """仅在非健康检查时记录日志。"""
        if "/api/health" not in (args[0] if args else ""):
            pass  # 可启用: sys.stderr.write(f"[kugua-api] {format % args}\n")


# ═══════════════════════════════════════════════════════════════
# 服务器启动
# ═══════════════════════════════════════════════════════════════

def create_guardian_from_env(artifacts_dir: Path):
    """从环境变量创建 Guardian 实例。"""
    from kugua.config import KuguaConfig
    from kugua.safety import SafetyManager
    from kugua.critical_slowing import CriticalSlowingDetector
    from kugua.guardian import Guardian, GuardianConfig
    from kugua.observer import create_observer_from_config

    cfg = KuguaConfig.from_env()
    cfg.artifacts_dir = artifacts_dir

    safety = SafetyManager(cfg)
    csd = CriticalSlowingDetector(artifacts_dir=artifacts_dir)

    observer = None
    if cfg.has_providers:
        try:
            observer = create_observer_from_config(cfg)
        except Exception:
            pass

    guardian_cfg = GuardianConfig(
        confidence_threshold=float(os.getenv("KUGUA_CONFIDENCE_THRESHOLD", "0.7")),
        permission_mode=os.getenv("KUGUA_PERMISSION_MODE", "block"),
        artifacts_dir=artifacts_dir,
    )

    guardian = Guardian(
        config=guardian_cfg,
        safety_manager=safety,
        csd=csd,
        observer=observer,
    )

    kb = None
    try:
        from kugua.knowledge import KnowledgeBase
        kb = KnowledgeBase(cfg)
    except Exception:
        pass

    return guardian, kb, safety, csd


def start_server(
    host: str = "0.0.0.0",
    port: int = 5000,
    kb=None,
    graph_kb=None,
    guardian=None,
    safety=None,
    csd=None,
    artifacts_dir: Optional[Path] = None,
):
    """启动 kugua REST API 服务器。"""
    # 如果没有提供依赖，自动创建
    if guardian is None:
        ad = artifacts_dir or Path(os.getenv(
            "KUGUA_ARTIFACTS_DIR",
            str(Path.home() / ".claude" / ".codex" / "artifacts")
        ))
        guardian, kb, safety, csd = create_guardian_from_env(ad)

    KuguaAPIHandler.kb = kb
    KuguaAPIHandler.graph_kb = graph_kb
    KuguaAPIHandler.guardian = guardian
    KuguaAPIHandler.safety = safety
    KuguaAPIHandler.csd = csd
    KuguaAPIHandler.start_time = time.time()

    server = HTTPServer((host, port), KuguaAPIHandler)
    print(f"[kugua-api] v0.3.0 Sidecar — http://{host}:{port}")
    print(f"[kugua-api] Endpoints:")
    print(f"  POST /api/guardian/check       — 认知监护审查")
    print(f"  GET  /api/guardian/sessions     — 会话列表")
    print(f"  GET  /api/guardian/benchmark    — 性能基准")
    print(f"  GET  /api/health                — 健康检查")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[kugua-api] shutting down...")
        server.shutdown()
