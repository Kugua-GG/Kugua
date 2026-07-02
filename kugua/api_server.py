"""
kugua — API Server (REST wrapper for kugua kernel)
v0.3.0

Lightweight HTTP API for external monitoring, task submission,
and the built-in dashboard.

Endpoints:
  GET  /api/kb/snapshot       — knowledge base status
  POST /api/task              — submit a task
  GET  /api/task/{id}/status  — query task status
  GET  /api/dashboard         — full subsystem snapshot (JSON)
  GET  /api/dashboard/csd     — CSD signals with time series
  GET  /api/dashboard/audit   — audit trail summary
  GET  /dashboard             — HTML dashboard page

Usage:
    python -m kugua.api_server --port 5000
    kugua-serve --dashboard --port 3847
"""
from __future__ import annotations
import json
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

# Module-level task store (shared across handler instances)
_task_store: dict = {}


def _gather_dashboard():
    """Collect all subsystem state for the dashboard API."""
    from kugua.config import KuguaConfig
    from kugua.safety import SafetyManager
    from kugua.states import StatesMachine
    from kugua.negentropy import Negentropy, NegentropyHistory
    from kugua.critical_slowing import CriticalSlowingDetector
    from kugua.efficacy import DoubleLoopEfficacyTracker

    cfg = KuguaConfig.from_env()
    arts = cfg.artifacts_dir
    safety = SafetyManager(cfg)
    sm = StatesMachine(cfg)
    ne = Negentropy(sm.load_state(), efficacy=None)
    ne_hist = NegentropyHistory(artifacts_dir=arts)
    csd = CriticalSlowingDetector(artifacts_dir=arts)

    # KB
    kb_data: dict = {"total": 0, "by_level": {"L1": 0, "L2": 0, "L3": 0}}
    try:
        from kugua.knowledge import KnowledgeBase
        kb = KnowledgeBase(cfg)
        stats = kb.effective_stats()
        dist = stats.get("level_distribution", {})
        kb_data = {
            "total": sum(dist.values()),
            "by_level": {k: dist.get(k, 0) for k in ("L1", "L2", "L3")},
            "graph_nodes": getattr(kb.graph, "node_count", 0) if hasattr(kb, "graph") and kb.graph else 0,
        }
    except Exception:
        pass

    # Audit
    audit = safety.get_audit_summary()

    # CSD
    csd_dict = csd.to_dict()

    # Negentropy
    ne_dict = ne.to_dict()
    ne_hist_dict = ne_hist.to_dict()

    return {
        "version": "0.3.0",
        "timestamp": time.time(),
        "trust": {
            "level": safety._trust_level.value,
            "emergency_stop": safety._state.get("emergency_stop", False),
        },
        "audit": audit,
        "csd": csd_dict,
        "negentropy": {
            "composite": ne_dict["composite"],
            "process_order": ne_dict["process_order"],
            "intent_anchoring": ne_dict["intent_anchoring"],
            "knowledge_efficacy": ne_dict["knowledge_efficacy"],
            "information_fidelity": ne_dict["information_fidelity"],
            "double_loop_efficacy": ne_dict["double_loop_efficacy"],
        },
        "ne_history": ne_hist_dict,
        "kb": kb_data,
    }


# ═══════════════════════════════════════════════════════════════
# Dashboard HTML (inline, no external dependencies)
# ═══════════════════════════════════════════════════════════════

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>kugua dashboard</title>
<style>
*, *::before, *::after { margin:0; padding:0; box-sizing:border-box; }
body {
  font-family: system-ui, -apple-system, sans-serif;
  background: #0d1117; color: #c9d1d9;
  padding: 1.5rem; max-width: 1100px; margin: 0 auto;
}
h1 { font-size: 1.25rem; margin-bottom: .25rem; }
.sub { color: #8b949e; font-size: .8rem; margin-bottom: 1.5rem; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
.card {
  background: #161b22; border: 1px solid #30363d;
  border-radius: 8px; padding: 1rem;
}
.card h2 { font-size: .85rem; color: #8b949e; text-transform: uppercase;
  letter-spacing: .05em; margin-bottom: .75rem; }
.card.full { grid-column: 1 / -1; }

.bar-wrap { display: flex; align-items: center; gap: .5rem; margin: .3rem 0; }
.bar-label { font-size: .8rem; width: 110px; flex-shrink: 0; }
.bar-track { flex: 1; height: 10px; background: #21262d; border-radius: 5px; overflow: hidden; }
.bar-fill { height: 100%; border-radius: 5px; transition: width .3s; }

.metric-row { display: flex; justify-content: space-between; padding: .25rem 0;
  font-size: .82rem; border-bottom: 1px solid #21262d; }
.metric-row:last-child { border-bottom: none; }
.metric-val { font-variant-numeric: tabular-nums; font-weight: 600; }

.signal-item { font-size: .78rem; padding: .3rem 0; border-bottom: 1px solid #21262d; }
.signal-item:last-child { border-bottom: none; }
.signal-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }

.stat-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: .5rem; }
.stat { text-align: center; padding: .5rem; background: #0d1117; border-radius: 6px; }
.stat .num { font-size: 1.4rem; font-weight: 700; }
.stat .lbl { font-size: .7rem; color: #8b949e; margin-top: .2rem; }

.tier-excellent { color: #3fb950; }
.tier-good { color: #d29922; }
.tier-needs-work { color: #db6d28; }
.tier-critical { color: #f85149; }

@keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:.5; } }
.live { animation: pulse 2s infinite; color: #3fb950; }
</style>
</head>
<body>
<h1>kugua dashboard <span class="live">&#9679; live</span></h1>
<p class="sub">v0.3.0 &middot; refreshing every 2s &middot; <span id="ts"></span></p>

<div class="grid">

<div class="card">
  <h2>TrustLevel &amp; Audit</h2>
  <div id="trust-panel">loading...</div>
</div>

<div class="card">
  <h2>Negentropy</h2>
  <div id="ne-panel">loading...</div>
</div>

<div class="card">
  <h2>Critical Slowing Detector</h2>
  <div id="csd-panel">loading...</div>
</div>

<div class="card">
  <h2>KnowledgeBase</h2>
  <div id="kb-panel">loading...</div>
</div>

</div>

<script>
const $ = (sel) => document.querySelector(sel);

function bar(val, max, colorFn) {
  max = max || 100;
  const pct = Math.max(0, Math.min(100, val / max * 100));
  const c = colorFn ? colorFn(pct) : (pct >= 80 ? '#3fb950' : pct >= 60 ? '#d29922' : pct >= 40 ? '#db6d28' : '#f85149');
  return '<div class="bar-track"><div class="bar-fill" style="width:' + pct + '%;background:' + c + '"></div></div>';
}

function trustPanel(d) {
  const t = d.trust || {};
  const a = d.audit || {};
  let h = '';
  h += '<div class="bar-wrap"><span class="bar-label">Trust L' + (t.level||'?') + '/5</span>' + bar((t.level||0)*20, 100) + '</div>';
  if (t.emergency_stop) h += '<div style="background:#f85149;color:#fff;padding:.3rem .6rem;border-radius:4px;margin:.5rem 0;font-weight:700">EMERGENCY STOP</div>';
  h += '<div class="stat-grid">';
  h += '<div class="stat"><div class="num">' + (a.total_checks||0) + '</div><div class="lbl">checks</div></div>';
  h += '<div class="stat"><div class="num" style="color:#f85149">' + (a.total_denials||0) + '</div><div class="lbl">denials</div></div>';
  h += '<div class="stat"><div class="num">' + ((a.denial_rate||0)*100).toFixed(1) + '%</div><div class="lbl">denial rate</div></div>';
  h += '</div>';
  return h;
}

function nePanel(d) {
  const ne = d.negentropy || {};
  const dims = [
    ['Process Order', ne.process_order||0],
    ['Intent Anchor', ne.intent_anchoring||0],
    ['Knowledge Eff', ne.knowledge_efficacy||0],
    ['Info Fidelity', ne.information_fidelity||0],
    ['DoubleLoop Eff', ne.double_loop_efficacy||0],
  ];
  const comp = ne.composite || 0;
  const tier = comp >= 80 ? 'excellent' : comp >= 60 ? 'good' : comp >= 40 ? 'needs-work' : 'critical';
  let h = '<div style="font-size:2rem;font-weight:700;text-align:center;margin:.5rem 0" class="tier-' + tier + '">' + comp.toFixed(0) + '%</div>';
  h += '<div style="text-align:center;font-size:.8rem;color:#8b949e;margin-bottom:.75rem">' + tier + '</div>';
  dims.forEach(function(dim) {
    h += '<div class="bar-wrap"><span class="bar-label">' + dim[0] + '</span>' + bar(dim[1], 100) + '<span style="font-size:.8rem;width:36px;text-align:right">' + dim[1].toFixed(0) + '%</span></div>';
  });
  const hist = d.ne_history || {};
  if (hist.count > 0) {
    h += '<div style="font-size:.75rem;color:#8b949e;margin-top:.5rem">' + hist.count + ' snapshots';
    if (hist.degrading) h += ' <span style="color:#f85149">DEGRADING</span>';
    h += '</div>';
  }
  return h;
}

function csdPanel(d) {
  const csd = d.csd || {};
  const sigs = csd.signals || [];
  let h = '';
  h += '<div class="stat-grid">';
  h += '<div class="stat"><div class="num">' + (csd.tracked_pairs||0) + '</div><div class="lbl">pairs tracked</div></div>';
  h += '<div class="stat"><div class="num" style="color:' + ((csd.critical_count||0) > 0 ? '#f85149' : '#3fb950') + '">' + (csd.critical_count||0) + '</div><div class="lbl">critical</div></div>';
  h += '<div class="stat"><div class="num">' + sigs.length + '</div><div class="lbl">signals</div></div>';
  h += '</div>';
  if (sigs.length === 0) {
    h += '<div style="font-size:.78rem;color:#8b949e;margin-top:.5rem">No tracked pairs. Run <code>kugua-demo --scenario=collapse</code> to seed data.</div>';
  } else {
    sigs.slice(0, 5).forEach(function(s) {
      const icon = s.critical ? 'R' : (s.significant ? 'Y' : '&middot;');
      const color = s.critical ? '#f85149' : (s.significant ? '#d29922' : '#8b949e');
      h += '<div class="signal-item">';
      h += '<span class="signal-dot" style="background:' + color + '"></span>';
      h += s.error_type + ':' + s.gv_id;
      h += ' <span style="color:#8b949e">n=' + s.sample_count + ' tau=' + (s.kendall_tau||0).toFixed(3) + ' p=' + (s.p_value||1).toFixed(4) + '</span>';
      if (s.composite_index >= 0.67) h += ' <span style="color:#db6d28">CSD=' + s.composite_index.toFixed(2) + '</span>';
      h += '</div>';
    });
  }
  return h;
}

function kbPanel(d) {
  const kb = d.kb || {};
  const by = kb.by_level || {};
  const total = kb.total || 0;
  if (total === 0) return '<div style="font-size:.78rem;color:#8b949e">No knowledge entries. Run tasks to build the knowledge base.</div>';
  let h = '';
  const levels = [
    ['L3 (verified 10+)', by.L3 || 0, '#3fb950'],
    ['L2 (verified 3+)', by.L2 || 0, '#d29922'],
    ['L1 (single)', by.L1 || 0, '#8b949e'],
  ];
  const maxLvl = Math.max(1, by.L3||0, by.L2||0, by.L1||0);
  levels.forEach(function(lv) {
    h += '<div class="bar-wrap"><span class="bar-label">' + lv[0] + '</span>' + bar(lv[1], maxLvl, function() { return lv[2]; }) + '<span style="font-size:.8rem;width:28px;text-align:right">' + lv[1] + '</span></div>';
  });
  if (kb.graph_nodes) {
    h += '<div style="font-size:.75rem;color:#8b949e;margin-top:.5rem">GraphKB: ' + kb.graph_nodes + ' nodes</div>';
  }
  return h;
}

function render(data) {
  $('trust-panel').innerHTML = trustPanel(data);
  $('ne-panel').innerHTML = nePanel(data);
  $('csd-panel').innerHTML = csdPanel(data);
  $('kb-panel').innerHTML = kbPanel(data);
  $('ts').textContent = new Date().toLocaleTimeString();
}

function refresh() {
  fetch('/api/dashboard')
    .then(function(r) { return r.json(); })
    .then(render)
    .catch(function(e) { console.error(e); });
}
refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════
# HTTP Handler
# ═══════════════════════════════════════════════════════════════

class KuguaAPIHandler(BaseHTTPRequestHandler):
    """HTTP handler for kugua REST API + Dashboard."""

    # Module-level references set before server start
    kb = None
    graph_kb = None
    double_loop = None
    mobius = None
    executor = None
    safety = None
    csd = None

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html: str, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── Routing ──────────────────────────────────────────────

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")

        if path == "/api/kb/snapshot":
            self._handle_kb_snapshot()
        elif path == "/api/dashboard":
            self._handle_dashboard()
        elif path == "/api/dashboard/csd":
            self._handle_csd()
        elif path == "/api/dashboard/audit":
            self._handle_audit()
        elif path == "/dashboard" or path == "/":
            self._handle_dashboard_html()
        elif path.startswith("/api/task/") and path.endswith("/status"):
            self._handle_task_status(path)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/api/task":
            self._handle_task_submit()
        else:
            self._json({"error": "not found"}, 404)

    # ── Existing handlers ────────────────────────────────────

    def _handle_kb_snapshot(self):
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

    def _handle_task_status(self, path: str):
        task_id = path.split("/")[-2]
        task = _task_store.get(task_id, {"status": "not_found"})
        self._json(task)

    def _handle_task_submit(self):
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
            "control_type": control_type,
            "description": body.get("description", ""),
        }
        self._json({"task_id": task_id, "status": "submitted"}, 201)

    # ── New dashboard handlers ────────────────────────────────

    def _handle_dashboard(self):
        """GET /api/dashboard — full subsystem snapshot."""
        try:
            data = _gather_dashboard()
        except Exception as e:
            self._json({"error": str(e)}, 500)
            return
        self._json(data)

    def _handle_csd(self):
        """GET /api/dashboard/csd — CSD signals with time series."""
        try:
            from kugua.config import KuguaConfig
            from kugua.critical_slowing import CriticalSlowingDetector
            cfg = KuguaConfig.from_env()
            csd = CriticalSlowingDetector(artifacts_dir=cfg.artifacts_dir)
            result = csd.to_dict()
            # Add recovery time series for each tracked pair
            for sig in result.get("signals", []):
                key = f"{sig['error_type']}:{sig['gv_id']}"
                sig["recovery_times"] = csd.get_recovery_times(
                    sig["error_type"], sig["gv_id"]
                )
            self._json(result)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _handle_audit(self):
        """GET /api/dashboard/audit — audit trail summary."""
        try:
            from kugua.config import KuguaConfig
            from kugua.safety import SafetyManager
            cfg = KuguaConfig.from_env()
            safety = SafetyManager(cfg)
            self._json(safety.get_audit_summary())
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _handle_dashboard_html(self):
        """GET /dashboard — HTML dashboard page."""
        self._html(DASHBOARD_HTML)

    def log_message(self, format, *args):
        pass  # Suppress default logging


# ═══════════════════════════════════════════════════════════════
# Server launcher
# ═══════════════════════════════════════════════════════════════

def start_server(
    host: str = "0.0.0.0",
    port: int = 3847,
    kb=None,
    graph_kb=None,
    double_loop=None,
    mobius=None,
    executor=None,
    dashboard: bool = False,
):
    """Start the kugua API and/or dashboard server.

    Args:
        host: Bind address.
        port: Bind port.
        dashboard: If True, the server also serves the HTML dashboard at /dashboard.
        (other args): Optional kugua subsystem references for advanced use.
    """
    KuguaAPIHandler.kb = kb
    KuguaAPIHandler.graph_kb = graph_kb
    KuguaAPIHandler.double_loop = double_loop
    KuguaAPIHandler.mobius = mobius
    KuguaAPIHandler.executor = executor

    server = HTTPServer((host, port), KuguaAPIHandler)
    endpoints = ["/api/kb/snapshot", "/api/task"]
    if dashboard:
        endpoints.extend(["/dashboard", "/api/dashboard", "/api/dashboard/csd", "/api/dashboard/audit"])

    print(f"kugua API server listening on http://{host}:{port}")
    for ep in endpoints:
        print(f"  {ep}")
    if dashboard:
        print(f"\n  Open http://localhost:{port}/dashboard in your browser.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
        print("\nkugua server stopped.")


# ═══════════════════════════════════════════════════════════════
# Standalone entry
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="kugua API server")
    ap.add_argument("--port", type=int, default=3847)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--dashboard", action="store_true", default=True)
    ap.add_argument("--api-only", action="store_true")
    args = ap.parse_args()
    start_server(host=args.host, port=args.port, dashboard=not args.api_only)
