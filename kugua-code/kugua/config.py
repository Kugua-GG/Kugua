"""KuguaConfig — minimal stub for package imports."""
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class KuguaConfig:
    """Configuration for kugua kernel."""
    artifacts_dir: Path = field(default_factory=lambda: Path(
        os.getenv("KUGUA_ARTIFACTS_DIR", str(Path.home() / ".claude" / ".codex" / "artifacts"))
    ))
    providers: List[Dict[str, Any]] = field(default_factory=list)
    debug: bool = False

    @property
    def has_providers(self) -> bool:
        return bool(self.providers and any(p.get("api_key") for p in self.providers))

    def get_artifacts_path(self, filename: str) -> Path:
        """Get full path for a file in the artifacts directory."""
        p = Path(self.artifacts_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p / filename

    @classmethod
    def from_env(cls) -> "KuguaConfig":
        cfg = cls()
        deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
        if deepseek_key:
            cfg.providers.append({
                "name": "deepseek", "api_base": "https://api.deepseek.com/v1",
                "api_key": deepseek_key, "models": ["deepseek-chat", "deepseek-reasoner"],
            })
        mimo_key = os.getenv("MIMO_API_KEY", "")
        if mimo_key:
            cfg.providers.append({
                "name": "mimo", "api_base": "https://api.mimoai.io/v1",
                "api_key": mimo_key, "models": ["mimo-v2-flash", "mimo-v2-pro"],
            })
        return cfg


# ═══════════════════════════════════════════════════════════
# SAFETY_DEFAULTS — 混沌校准后的最优安全参数
# ═══════════════════════════════════════════════════════════
# 来源: benchmarks/calibrate_ks_thresholds.py
# 校准日期: 2026-06-30
# 样本量: 500 trials/point × 260 组合 = 130,000 次试验
# 选择标准: FPR < 5% AND FNR < 0.1%, 最大化 F1
# 完整记录: experiments/SAFETY_CALIBRATION.md

SAFETY_DEFAULTS = {
    # Kill Switch — 上下文依赖的连续拒绝阈值
    "ks_threshold_safe": 10,    # P0/P1 安全模式 (高容忍)
    "ks_threshold_exec": 7,     # P2 执行中 (中等)
    "ks_threshold_risk": 9,     # P3/P4 高风险 (校准最优值, 原值=3 太激进)

    # 时间窗衰减: 超时后重置拒绝计数器
    "ks_decay_window_sec": 60,  # 原值=30, 校准发现60s更稳定

    # 预算不足权重: 预算耗尽导致的拒绝只有80%计入KS计数
    "budget_denial_weight": 0.8,

    # 信任升级: 连续安全操作次数
    "trust_upgrade_l2_to_l3": 10,
    "trust_upgrade_l3_to_l4": 30,

    # ErrorBudget
    "error_budget_max": 10,
    "error_budget_window_hours": 24,
}
