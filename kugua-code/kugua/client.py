"""
kugua GuardianClient — 零依赖集成 SDK v0.3.0

让任何 LLM 应用在 3 行代码内接入 kugua 认知监护。

Usage:
    from kugua.client import GuardianClient
    gc = GuardianClient()
    decision = gc.check(prompt="...", action="execute_cmd")
    if decision.allowed:
        execute()
    else:
        escalate(decision.reason)
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class GuardianDecision:
    """kugua 监护的一次判决。"""
    allowed: bool = True
    action: str = "none"       # none / retry / block / warn / slow_down
    reason: str = ""
    suggestion: str = ""
    confidence: float = 1.0
    session_id: str = ""
    check_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed, "action": self.action,
            "reason": self.reason, "suggestion": self.suggestion,
            "confidence": self.confidence,
            "session_id": self.session_id, "check_id": self.check_id,
        }


class GuardianClient:
    """kugua 认知监护的轻量级客户端。

    封装 Guardian + Safety + StatesMachine，提供单一入口。
    纯 Python stdlib — 零额外依赖。

    Usage:
        gc = GuardianClient(confidence_threshold=0.7)
        if gc.is_safe("write_file", confidence=0.85):
            write_file(path, content)
    """

    def __init__(
        self,
        confidence_threshold: float = 0.7,
        permission_mode: str = "block",
        artifacts_dir: Optional[str] = None,
    ):
        from kugua.config import KuguaConfig
        from kugua.safety import SafetyManager
        from kugua.guardian import Guardian, GuardianConfig
        from pathlib import Path

        self._cfg = KuguaConfig()
        if artifacts_dir:
            self._cfg.artifacts_dir = Path(artifacts_dir)

        self._safety = SafetyManager(self._cfg)
        # 信任预热到 L3
        for _ in range(10):
            self._safety.record_safe_operation()

        self._guardian = Guardian(
            GuardianConfig(
                confidence_threshold=confidence_threshold,
                permission_mode=permission_mode,
            ),
            safety_manager=self._safety,
        )

    def check(
        self,
        prompt: str = "",
        action: str = "read_file",
        confidence: float = 1.0,
        session_id: str = "default",
    ) -> GuardianDecision:
        """检查一个操作是否安全。

        Args:
            prompt: Agent 输出内容（用于内容安全审查）
            action: 操作类型 (read_file / write_file / edit_file / execute_cmd)
            confidence: Agent 对输出的置信度 [0, 1]
            session_id: 会话标识

        Returns:
            GuardianDecision: 判决结果
        """
        verdict = self._guardian.check(
            agent_output=prompt,
            operation=action,
            confidence=confidence,
            session_id=session_id,
        )

        return GuardianDecision(
            allowed=not verdict.intervene,
            action=verdict.action,
            reason=verdict.reason,
            suggestion=verdict.suggestion,
            confidence=verdict.confidence_score,
            session_id=verdict.session_id,
            check_id=verdict.check_id,
        )

    def is_safe(self, action: str, confidence: float = 1.0) -> bool:
        """快速安全检查 — 返回 True/False。"""
        return self.check(action=action, confidence=confidence).allowed

    def benchmark(self) -> Dict[str, Any]:
        """获取性能基准。"""
        return self._guardian.benchmark_report()
