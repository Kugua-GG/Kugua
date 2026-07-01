"""
Negentropy — 五维负熵仪表板 v0.3.0

从硬编码线性公式升级为真实指标接入。

三维核心指标（v0.3.0）:
  1. 置信度均值    — 来自 Guardian 会话的 confidence_score 平均
  2. 上下文化不确定性 — 来自 Observer 盲审未通过率
  3. 策略切换频率   — 来自 StatesMachine 的 phase_regressions + anchor_changes

二维辅助指标（保留兼容）:
  4. 流程有序度
  5. 知识生效度
"""
from typing import Any, Dict, List, Optional
import math

DEFAULT_WEIGHTS = {
    "confidence_mean": 0.25,
    "contextual_uncertainty": 0.25,
    "strategy_switch_freq": 0.20,
    "process_order": 0.15,
    "knowledge_efficacy": 0.15,
}


class Negentropy:
    """五维负熵计算器。

    Args:
        state: StatesMachine 状态字典
        efficacy: DoubleLoopEfficacyTracker 实例（可选）
        guardian_sessions: Guardian 会话列表（可选，用于真实指标）
        observer_stats: Observer 盲审统计（可选）
    """

    def __init__(
        self,
        state: Dict[str, Any],
        efficacy: Any = None,
        guardian_sessions: Optional[List] = None,
        observer_stats: Optional[Dict[str, Any]] = None,
    ):
        self.state = state
        self.efficacy = efficacy
        self._guardian_sessions = guardian_sessions or []
        self._observer_stats = observer_stats or {}

    # ── 三维核心指标 ──────────────────────────────────────

    def confidence_mean(self) -> float:
        """置信度均值（来自 Guardian 会话）。
        无数据时返回 50（中性）。
        """
        if not self._guardian_sessions:
            return 50.0
        scores = []
        for s in self._guardian_sessions:
            if hasattr(s, 'to_dict'):
                d = s.to_dict()
                rate = d.get('intervention_rate', 0)
                # 介入率越低 → 置信度越高
                scores.append(max(0, 100 * (1 - rate)))
        if not scores:
            return 50.0
        return round(sum(scores) / len(scores), 1)

    def contextual_uncertainty(self) -> float:
        """上下文化不确定性（来自 Observer 盲审未通过率）。
        0 = 完全确定, 100 = 完全不确定。
        无数据返回 50（中性）。
        """
        stats = self._observer_stats
        if not stats:
            return 50.0
        total = stats.get("total_gates", 0)
        blocked = stats.get("blocked_gates", 0)
        if total == 0:
            return 50.0
        # 阻塞率越高 → 不确定性越高
        return round(min(100, (blocked / total) * 100), 1)

    def strategy_switch_freq(self) -> float:
        """策略切换频率（来自 phase_regressions + anchor_changes）。
        0 = 无切换（稳定）, 100 = 频繁切换（不稳定）。
        """
        regressions = self.state.get("phase_regressions", 0)
        anchor_changes = self.state.get("anchor_changes", 0)
        switches = self.state.get("phase_switches", 0)

        # 归一化: 每次回退 +10, 每次意图变更 +20, 总切换做分母
        penalty = regressions * 10 + anchor_changes * 20
        # 如果切换总次数很大，说明系统在剧烈振荡
        if switches > 20:
            penalty += (switches - 20) * 2
        return max(0, min(100, penalty))

    # ── 二维辅助指标 ──────────────────────────────────────

    def process_order(self) -> float:
        """流程有序度。"""
        return max(0, min(100,
            100
            - self.state.get("phase_regressions", 0) * 15
            - self.state.get("phase_switches", 0) * 3
        ))

    def intent_anchoring(self) -> float:
        """意图锚定度。"""
        return max(0, min(100,
            100 - self.state.get("anchor_changes", 0) * 25
        ))

    def knowledge_efficacy(self) -> float:
        """知识生效度。"""
        return max(0, min(100,
            100 - self.state.get("stagnation_events", 0) * 20
        ))

    def information_fidelity(self) -> float:
        """信息保真度。"""
        r = self.state.get("retrieve_calls", 0)
        s = max(self.state.get("total_subtasks", 1), 1)
        o = r / s
        return 100.0 if o <= 3 else (80.0 if o <= 10 else max(0, 100 - (o - 10) * 5))

    def double_loop_efficacy(self) -> float:
        """双环效能（来自 efficacy tracker）。"""
        if self.efficacy and hasattr(self.efficacy, "to_dict"):
            d = self.efficacy.to_dict()
            v = d.get("verified_events", 0)
            t = d.get("total_events", 0)
            return min(100, (v / t) * 100) if t > 0 else 50.0
        return 50.0

    # ── 综合指数 ──────────────────────────────────────────

    def composite(self) -> float:
        """加权综合负熵指数。"""
        w = DEFAULT_WEIGHTS
        return round(
            w["confidence_mean"] * self.confidence_mean()
            + w["contextual_uncertainty"] * (100 - self.contextual_uncertainty())  # 反转
            + w["strategy_switch_freq"] * (100 - self.strategy_switch_freq())      # 反转
            + w["process_order"] * self.process_order()
            + w["knowledge_efficacy"] * self.knowledge_efficacy(),
            1,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "composite": self.composite(),
            # 核心三维
            "confidence_mean": self.confidence_mean(),
            "contextual_uncertainty": self.contextual_uncertainty(),
            "strategy_switch_freq": self.strategy_switch_freq(),
            # 辅助
            "process_order": self.process_order(),
            "intent_anchoring": self.intent_anchoring(),
            "knowledge_efficacy": self.knowledge_efficacy(),
            "information_fidelity": self.information_fidelity(),
            "double_loop_efficacy": self.double_loop_efficacy(),
            # 原始数据
            "raw": {
                "phase_regressions": self.state.get("phase_regressions", 0),
                "phase_switches": self.state.get("phase_switches", 0),
                "anchor_changes": self.state.get("anchor_changes", 0),
                "stagnation_events": self.state.get("stagnation_events", 0),
                "retrieve_calls": self.state.get("retrieve_calls", 0),
                "total_subtasks": self.state.get("total_subtasks", 0),
            },
        }


def generate_dashboard(ne: Negentropy) -> str:
    """生成 HTML 负熵仪表板。"""
    d = ne.to_dict()
    c = d["composite"]
    tier = "优秀" if c >= 80 else ("尚可" if c >= 60 else ("需改进" if c >= 40 else "严重熵增"))
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>苦瓜code · 负熵仪表板</title></head>
<body>
<h1>苦瓜code · 负熵仪表板</h1>
<p>综合指数: {c}% ({tier})</p>
<ul>
<li>置信度均值: {d['confidence_mean']}%</li>
<li>上下文化不确定性: {d['contextual_uncertainty']}%</li>
<li>策略切换频率: {d['strategy_switch_freq']}%</li>
<li>流程有序度: {d['process_order']}%</li>
<li>知识生效度: {d['knowledge_efficacy']}%</li>
</ul>
</body></html>"""


def generate_integrity_report(ne: Negentropy) -> str:
    """生成单行完整性报告。"""
    d = ne.to_dict()
    return (
        f"COMPOSITE: {d['composite']}% | "
        f"CONF: {d['confidence_mean']}% | "
        f"UNCERT: {d['contextual_uncertainty']}% | "
        f"SWITCH: {d['strategy_switch_freq']}% | "
        f"ORDER: {d['process_order']}% | "
        f"KE: {d['knowledge_efficacy']}%"
    )
