"""
kugua Guardian — 认知监护模块 v0.3.0

无侵入的只读观察者，监控主 Agent 的对话流和工具调用。
仅在以下三种情况介入:
  1. 置信度低于阈值 → 拦截并请求重新生成
  2. 检测到临界慢化 → 建议主 Agent 减速或切换策略
  3. 违反权限门控 → 直接阻断并上报

设计原则:
  - 只读观察: 不修改主 Agent 的状态或输出
  - 最小侵入: 通过标准 API 返回 verdict，由调用方决定如何处理
  - 独立 LLM: 使用 FreshObserver (不同模型/Provider) 避免共模失效

可作为 Sidecar 容器部署，被 LangGraph / AutoGen / CrewAI 等编排框架调用。

Usage:
    from kugua.guardian import Guardian, GuardianConfig

    cfg = GuardianConfig(
        confidence_threshold=0.7,
        csd_warning_threshold=0.05,
        permission_mode="block",
    )
    guardian = Guardian(cfg)

    # 每次 Agent 输出后调用
    verdict = guardian.check(
        agent_output="...",
        operation="write_file",
        confidence=0.65,
        session_id="agent-session-1",
    )
    if verdict.intervene:
        print(f"Guardian: {verdict.action} — {verdict.reason}")
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════════════════════
# GuardianConfig
# ═══════════════════════════════════════════════════════════════

@dataclass
class GuardianConfig:
    """认知监护配置。"""
    # 置信度阈值: 低于此值触发拦截
    confidence_threshold: float = 0.7

    # 临界慢化 p 值阈值
    csd_p_threshold: float = 0.05

    # 权限模式: "block"(阻断) / "warn"(警告) / "log"(仅记录)
    permission_mode: str = "block"

    # Observer 模型 (独立于主 Agent 的模型)
    observer_model: str = "mimo-v2-flash"

    # 是否启用深度审查 (FreshObserver 三门控)
    enable_deep_review: bool = True

    # 性能采样窗口 (保留最近 N 次检查的延迟)
    latency_window: int = 100

    # 制品目录
    artifacts_dir: Path = field(default_factory=lambda: Path("./artifacts"))


# ═══════════════════════════════════════════════════════════════
# GuardianVerdict
# ═══════════════════════════════════════════════════════════════

@dataclass
class GuardianVerdict:
    """认知监护的一次判决。"""
    session_id: str = ""
    check_id: str = ""
    timestamp: str = ""

    # 是否介入
    intervene: bool = False

    # 介入动作: "block" / "warn" / "retry" / "slow_down" / "none"
    action: str = "none"

    # 介入原因
    reason: str = ""

    # 详细诊断
    confidence_score: float = 1.0
    confidence_pass: bool = True
    csd_critical: bool = False
    csd_details: str = ""
    permission_allowed: bool = True
    permission_reason: str = ""

    # Observer 意见 (如果启用了深度审查)
    observer_verdict: str = ""
    observer_confidence: float = 0.0

    # 建议
    suggestion: str = ""

    # 延迟 (ms)
    elapsed_ms: float = 0.0

    def __post_init__(self):
        if not self.check_id:
            self.check_id = f"gv_{uuid.uuid4().hex[:10]}"
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "check_id": self.check_id,
            "timestamp": self.timestamp,
            "intervene": self.intervene,
            "action": self.action,
            "reason": self.reason,
            "confidence_score": self.confidence_score,
            "confidence_pass": self.confidence_pass,
            "csd_critical": self.csd_critical,
            "csd_details": self.csd_details,
            "permission_allowed": self.permission_allowed,
            "permission_reason": self.permission_reason,
            "observer_verdict": self.observer_verdict,
            "observer_confidence": self.observer_confidence,
            "suggestion": self.suggestion,
            "elapsed_ms": self.elapsed_ms,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════
# GuardianSession
# ═══════════════════════════════════════════════════════════════

@dataclass
class GuardianSession:
    """一个被监护 Agent 的会话状态。"""
    session_id: str
    total_checks: int = 0
    interventions: int = 0
    blocks: int = 0
    warnings: int = 0
    retries: int = 0
    created_at: str = ""
    last_check_at: str = ""
    latencies: List[float] = field(default_factory=list)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def record(self, verdict: GuardianVerdict):
        self.total_checks += 1
        self.last_check_at = verdict.timestamp
        self.latencies.append(verdict.elapsed_ms)
        if verdict.intervene:
            self.interventions += 1
            if verdict.action == "block":
                self.blocks += 1
            elif verdict.action == "warn":
                self.warnings += 1
            elif verdict.action == "retry":
                self.retries += 1

    @property
    def latency_p50(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_lat = sorted(self.latencies)
        idx = int(len(sorted_lat) * 0.5)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    @property
    def latency_p95(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_lat = sorted(self.latencies)
        idx = int(len(sorted_lat) * 0.95)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    @property
    def latency_p99(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_lat = sorted(self.latencies)
        idx = int(len(sorted_lat) * 0.99)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    @property
    def intervention_rate(self) -> float:
        if self.total_checks == 0:
            return 0.0
        return self.interventions / self.total_checks

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "total_checks": self.total_checks,
            "interventions": self.interventions,
            "blocks": self.blocks,
            "warnings": self.warnings,
            "retries": self.retries,
            "intervention_rate": round(self.intervention_rate, 4),
            "latency_p50_ms": round(self.latency_p50, 2),
            "latency_p95_ms": round(self.latency_p95, 2),
            "latency_p99_ms": round(self.latency_p99, 2),
            "created_at": self.created_at,
            "last_check_at": self.last_check_at,
        }


# ═══════════════════════════════════════════════════════════════
# Guardian
# ═══════════════════════════════════════════════════════════════

class Guardian:
    """认知监护器 — kugua 的 Sidecar 观察者。

    每收到 Agent 的输出和元数据，执行三层检查:
      Layer 1: 置信度门控 → 低于阈值则拦截
      Layer 2: 临界慢化检测 → 趋势恶化则建议减速
      Layer 3: 权限门控 → 违规则阻断

    可选的 Layer 4 (深度审查): FreshObserver 独立 LLM 盲审。

    Usage as Sidecar:
        guardian = Guardian(GuardianConfig(confidence_threshold=0.7))
        verdict = guardian.check(
            agent_output="...",
            operation="write_file",
            confidence=0.65,
        )
        # → intervene=True, action="retry", reason="Confidence 0.65 < threshold 0.70"
    """

    def __init__(
        self,
        config: Optional[GuardianConfig] = None,
        safety_manager=None,
        csd=None,
        observer=None,
        kb=None,
    ):
        self.config = config or GuardianConfig()
        self._safety = safety_manager
        self._csd = csd
        self._observer = observer
        self._kb = kb

        self._sessions: Dict[str, GuardianSession] = {}
        self._latency_window: List[float] = []

    # ── 主入口 ──────────────────────────────────────────────

    def check(
        self,
        agent_output: str = "",
        operation: str = "",
        confidence: float = 1.0,
        session_id: str = "default",
        error_type: str = "",
        gv_id: str = "",
        context: Optional[Dict[str, Any]] = None,
    ) -> GuardianVerdict:
        """执行一次认知监护检查。

        Args:
            agent_output: Agent 的输出内容
            operation: Agent 试图执行的操作 (如 "write_file")
            confidence: Agent 对输出的置信度 [0, 1]
            session_id: 会话标识
            error_type: 错误类型（用于 CSD 追踪）
            gv_id: 治理变量 ID（用于 CSD 追踪）
            context: 附加上下文

        Returns:
            GuardianVerdict: 监护判决
        """
        t0 = time.time()
        verdict = GuardianVerdict(session_id=session_id)
        verdict.confidence_score = confidence

        # ── Layer 1: 置信度门控 ──
        if confidence < self.config.confidence_threshold:
            verdict.confidence_pass = False
            verdict.intervene = True
            verdict.action = "retry"
            verdict.reason = (
                f"Confidence {confidence:.2f} < threshold {self.config.confidence_threshold}. "
                f"Request regeneration with higher confidence."
            )
            verdict.suggestion = "Agent should retry with more evidence or simpler task decomposition."
            verdict.elapsed_ms = (time.time() - t0) * 1000
            self._record(session_id, verdict)
            return verdict

        # ── Layer 2: 临界慢化检测 ──
        if self._csd and error_type and gv_id:
            try:
                signal = self._csd.detect(error_type, gv_id)
                verdict.csd_details = (
                    f"n={signal.sample_count} tau={signal.kendall_tau} p={signal.p_value}"
                )
                if signal.critical:
                    verdict.csd_critical = True
                    verdict.intervene = True
                    verdict.action = "slow_down"
                    verdict.reason = (
                        f"Critical slowing detected: {error_type}:{gv_id}. "
                        f"Recovery time trend increasing (p={signal.p_value}). "
                        f"Suggest switching strategy or modifying governance rules."
                    )
                    verdict.suggestion = (
                        "Consider: (1) reduce task complexity, "
                        "(2) switch to alternative approach, "
                        "(3) trigger double-loop learning."
                    )
                    verdict.elapsed_ms = (time.time() - t0) * 1000
                    self._record(session_id, verdict)
                    return verdict
            except Exception:
                pass

        # ── Layer 3: 权限门控 ──
        if self._safety and operation:
            try:
                allowed, perm_reason = self._safety.check_permission(operation)
                verdict.permission_allowed = allowed
                verdict.permission_reason = perm_reason
                if not allowed:
                    verdict.intervene = True
                    mode = self.config.permission_mode
                    if mode == "block":
                        verdict.action = "block"
                    elif mode == "warn":
                        verdict.action = "warn"
                    else:
                        verdict.action = "warn"  # log mode still warns
                    verdict.reason = f"Permission denied: {perm_reason}"
                    verdict.suggestion = "Operation blocked by safety gate. Request human approval or use lower-risk alternative."
                    verdict.elapsed_ms = (time.time() - t0) * 1000
                    self._record(session_id, verdict)
                    return verdict
            except Exception:
                pass

        # ── Layer 4 (可选): 深度审查 ──
        if self.config.enable_deep_review and self._observer and agent_output:
            try:
                # 使用 GATE_PROPOSAL 进行内容安全审查
                gate_result = self._observer.gate_proposal(
                    before="(no previous output)",
                    after=agent_output,
                    reason=operation or "agent output review",
                )
                verdict.observer_verdict = (
                    "PASSED" if gate_result.all_passed else "BLOCKED"
                )
                if gate_result.observations:
                    verdict.observer_confidence = gate_result.observations[0].confidence
                if not gate_result.all_passed:
                    verdict.intervene = True
                    verdict.action = "block"
                    verdict.reason = (
                        f"Observer gate blocked: {gate_result.block_reason}"
                    )
                    verdict.suggestion = "Output may contain unsafe content. Review manually."
            except Exception:
                pass

        # 全部通过
        verdict.elapsed_ms = (time.time() - t0) * 1000
        self._record(session_id, verdict)
        return verdict

    # ── 会话管理 ────────────────────────────────────────────

    def _record(self, session_id: str, verdict: GuardianVerdict):
        if session_id not in self._sessions:
            self._sessions[session_id] = GuardianSession(session_id=session_id)
        self._sessions[session_id].record(verdict)
        self._latency_window.append(verdict.elapsed_ms)
        if len(self._latency_window) > self.config.latency_window:
            self._latency_window = self._latency_window[-self.config.latency_window:]

    def get_session(self, session_id: str) -> Optional[GuardianSession]:
        return self._sessions.get(session_id)

    def list_sessions(self) -> List[GuardianSession]:
        return list(self._sessions.values())

    # ── 性能基准 ────────────────────────────────────────────

    @property
    def latency_p50(self) -> float:
        return self._percentile(50)

    @property
    def latency_p95(self) -> float:
        return self._percentile(95)

    @property
    def latency_p99(self) -> float:
        return self._percentile(99)

    @property
    def avg_latency(self) -> float:
        if not self._latency_window:
            return 0.0
        return sum(self._latency_window) / len(self._latency_window)

    @property
    def total_checks(self) -> int:
        return sum(s.total_checks for s in self._sessions.values())

    @property
    def total_interventions(self) -> int:
        return sum(s.interventions for s in self._sessions.values())

    def _percentile(self, p: int) -> float:
        if not self._latency_window:
            return 0.0
        sorted_lat = sorted(self._latency_window)
        idx = int(len(sorted_lat) * p / 100.0)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    def benchmark_report(self) -> Dict[str, Any]:
        """生成性能基准报告。"""
        import os
        try:
            import psutil
            process = psutil.Process(os.getpid())
            mem = process.memory_info()
            mem_rss_mb = mem.rss / (1024 * 1024)
        except ImportError:
            mem_rss_mb = -1

        return {
            "latency": {
                "avg_ms": round(self.avg_latency, 2),
                "p50_ms": round(self.latency_p50, 2),
                "p95_ms": round(self.latency_p95, 2),
                "p99_ms": round(self.latency_p99, 2),
                "sample_count": len(self._latency_window),
            },
            "throughput": {
                "total_checks": self.total_checks,
                "total_interventions": self.total_interventions,
                "intervention_rate": round(
                    self.total_interventions / max(self.total_checks, 1), 4
                ),
            },
            "memory": {
                "rss_mb": round(mem_rss_mb, 2),
            },
            "sessions": len(self._sessions),
            "config": {
                "confidence_threshold": self.config.confidence_threshold,
                "permission_mode": self.config.permission_mode,
                "deep_review_enabled": self.config.enable_deep_review,
            },
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "benchmark": self.benchmark_report(),
            "sessions": {sid: s.to_dict() for sid, s in self._sessions.items()},
        }
