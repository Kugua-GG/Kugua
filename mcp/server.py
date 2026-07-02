#!/usr/bin/env python3
"""
Kugua MCP Server — 为 Claude Code 提供 Agent 认知内核能力。

通过 MCP stdio 协议暴露 8 个工具：
  kb_query          — 查询 L0-L3 证据层级知识库
  kb_snapshot       — 知识库统计快照
  double_loop_check — 检查双环学习触发条件
  observer_gate     — FreshObserver 门控：判断一个声明是否合理
  negentropy_dash   — 负熵仪表板（5 维）
  csd_status        — 临界慢化检测器状态
  record_error      — 🆕 用户手动记录错误模式
  status_all        — 🆕 六项核心指标汇总

安装：
  pip install -e C:/Users/Administrator/Desktop/kugua
  然后在 Claude Code 的 .mcp.json 中添加 kugua 条目。
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# ── 日志（仅 stderr，stdout 留给 MCP JSON-RPC）────────────
logging.basicConfig(level=logging.INFO, format="[kugua-mcp] %(message)s", stream=sys.stderr)
logger = logging.getLogger("kugua-mcp")

# ── 路径 ──────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
KUGUA_DIR = Path(os.getenv("KUGUA_CODE_DIR", str(Path.home() / "Desktop" / "kugua")))
sys.path.insert(0, str(KUGUA_DIR))

ARTIFACTS_DIR = Path(os.getenv("KUGUA_ARTIFACTS_DIR", str(Path.home() / ".claude" / ".codex" / "artifacts")))
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

# ── i18n 多语言 🆕 ────────────────────────────────────────
from kugua.i18n import t, set_lang, get_lang, SUPPORTED_LANGS

# 语言选择优先级: KUGUA_LANG 环境变量 > 默认 zh-CN
_lang = get_lang()
if _lang != "zh-CN":
    logger.info("语言: %s (%s)", _lang, SUPPORTED_LANGS.get(_lang, "未知"))

# ── FastMCP ──────────────────────────────────────────────
from mcp.server.fastmcp import FastMCP

_mcp_instructions = (
    f"苦瓜code Agent 认知内核 v0.2.1 — "
    f"知识库 · 双环学习 · 观察者门控 · 负熵仪表板 · 手动报错 · 多语言({', '.join(SUPPORTED_LANGS.keys())})"
)
mcp = FastMCP("kugua", instructions=_mcp_instructions)

# ── 懒加载 kugua 模块 (v0.2.2: 统一使用 KuguaKernel) ────
_kernel = None

def _get_kernel():
    """Lazy-init the unified KuguaKernel (v0.2.2+).

    Falls back to individual module init if kernel is not available.
    """
    global _kernel
    if _kernel is None:
        try:
            from kugua.kernel import KuguaKernel
            _kernel = KuguaKernel()
            _kernel.init_minimal()
            # Override artifacts dir
            _kernel.cfg.artifacts_dir = ARTIFACTS_DIR
            logger.info("KuguaKernel initialized (minimal mode)")
        except ImportError:
            logger.warning("KuguaKernel not available, falling back to legacy init")
            _kernel = _legacy_init()
    return _kernel

def _legacy_init():
    """Legacy fallback: manual module initialization."""
    # Return a simple namespace for backward compat
    from types import SimpleNamespace
    ns = SimpleNamespace()
    from kugua.config import KuguaConfig
    from kugua.knowledge import KnowledgeBase
    cfg = KuguaConfig.from_env()
    cfg.artifacts_dir = ARTIFACTS_DIR
    ns.kb = KnowledgeBase(cfg)
    ns.cfg = cfg
    from kugua.critical_slowing import CriticalSlowingDetector
    ns.csd = CriticalSlowingDetector(artifacts_dir=ARTIFACTS_DIR)
    from kugua.efficacy import DoubleLoopEfficacyTracker
    ns.eff = DoubleLoopEfficacyTracker(artifacts_dir=ARTIFACTS_DIR)
    from kugua.observer import create_observer_from_config, FreshObserver
    if cfg.has_providers:
        ns.observer = create_observer_from_config(cfg)
    else:
        ns.observer = FreshObserver(llm_client=None)
    return ns

# Backward-compatible accessors
def _get_kb():
    k = _get_kernel()
    return k.kb if hasattr(k, 'kb') else k.kb

def _get_csd():
    k = _get_kernel()
    return k.csd if hasattr(k, 'csd') else k.csd

def _get_eff():
    k = _get_kernel()
    return k.efficacy if hasattr(k, 'efficacy') else k.eff

def _get_observer():
    k = _get_kernel()
    return k.observer if hasattr(k, 'observer') else k.observer
            _observer = FreshObserver(llm_client=None)
    return _observer


# ═══════════════════════════════════════════════════════════════
# 工具 1: kb_query — 查询知识库
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def kb_query(query: str = "", min_level: str = "L1", max_results: int = 10) -> str:
    """查询苦瓜code 的 L0-L3 证据层级知识库。

    知识库条目按证据强度分层：
      L3 — 经 10+ 上下文验证 + 反例测试（高可信）
      L2 — 经 3 个不同上下文验证
      L1 — 单次验证

    Args:
        query: 搜索关键词（留空则返回所有 L2+ 条目）
        min_level: 最低证据层级 (L1/L2/L3)，默认 L1
        max_results: 最大返回条数，默认 10
    """
    kb = _get_kb()
    if not kb.entries:
        return t("kb.empty")

    # 使用倒排索引检索 (O(k) 替代 O(n))
    if query:
        scored = kb.search(query, min_level=min_level, top_k=max_results)
        entries = [e for _, e in scored]
    else:
        entries = kb.query(min_level=min_level)[:max_results]

    if not entries:
        return t("kb.no_results", query=query, level=min_level)

    lines = []
    for e in entries:
        status_icon = {"L3": "◆", "L2": "◇", "L1": "·"}.get(e.level, "?")
        lines.append(
            f"{status_icon} [{e.level}] {e.key} (c={e.confidence:.1f}, used={e.usage_count}x)\n"
            f"   {e.content[:200]}"
        )

    active_count = sum(1 for e in kb.entries.values() if e.status == 'active')
    lines.append(f"\n— {t('kb.result_footer', count=len(entries), active=active_count)}")
    return "\n\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 工具 2: kb_snapshot — 知识库健康快照
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def kb_snapshot() -> str:
    """获取知识库的统计快照：各层级条目数、升降级统计、健康状态。"""
    kb = _get_kb()
    eff = _get_eff()

    by_level = {"L1": 0, "L2": 0, "L3": 0}
    by_status = {"active": 0, "invalid": 0, "conflict": 0, "archived": 0}

    for e in kb.entries.values():
        by_level[e.level] = by_level.get(e.level, 0) + 1
        by_status[e.status] = by_status.get(e.status, 0) + 1

    efficacy = eff.to_dict()

    lines = [
        f"{t('common.title')} · {t('kb_snapshot.title')}",
        "",
        f"{t('kb_snapshot.level_dist')}:",
        f"  {t('kb_snapshot.l3_desc')}:  {by_level['L3']} {t('common.unit_entry')}",
        f"  {t('kb_snapshot.l2_desc')}:         {by_level['L2']} {t('common.unit_entry')}",
        f"  {t('kb_snapshot.l1_desc')}:       {by_level['L1']} {t('common.unit_entry')}",
        f"  {t('common.separator')}",
        f"  {t('kb_snapshot.active_total')}:            {by_status['active']} {t('common.unit_entry')}",
        "",
        f"{t('kb_snapshot.status_dist')}:",
    ]
    for s, c in by_status.items():
        if c > 0:
            lines.append(f"  {s}: {c}")

    lines.extend([
        "",
        f"{t('kb_snapshot.efficacy_title')}:",
        f"  {t('kb_snapshot.verified_events')}: {efficacy['verified_events']}",
        f"  {t('kb_snapshot.total_delta_s')}:        {efficacy['total_entropy_reduction']:.1f}",
        f"  {t('kb_snapshot.pending')}:         {efficacy['pending_events']}",
        f"  {t('kb_snapshot.reverted')}:         {efficacy['reverted_events']}",
    ])

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 工具 3: double_loop_check — 检查双环触发条件
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def double_loop_check(error_type: str = "", gv_id: str = "") -> str:
    """检查是否满足双环学习触发条件。

    双环学习是 kugua 的核心自我改进机制：
      单环 = 修正行为（同一规则下重试）
      双环 = 修改规则本身（当同一类错误反复出现时）

    触发条件：
      1. 临界慢化信号（Mann-Kendall p<0.05）
      2. 同一 (error_type, gv_id) 出现 >=3 次

    Args:
        error_type: 错误类型（准确性/完整性/合规性），留空则检查全部
        gv_id: 治理变量 ID（KB 条目 key），留空则检查全部
    """
    csd = _get_csd()

    if error_type and gv_id:
        signal = csd.detect(error_type, gv_id)
        if signal.critical:
            trend_map = {1: t("dlc.trend_increasing"), -1: t("dlc.trend_decreasing"), 0: t("dlc.trend_none")}
            trend_label = trend_map.get(signal.trend, t("dlc.trend_none"))
            return (
                f"⚠️ {t('dlc.triggered_title')}\n"
                f"  {t('dlc.error_type')}: {error_type}\n"
                f"  {t('dlc.gv_id')}: {gv_id}\n"
                f"  {t('dlc.sample_count')}: {signal.sample_count}\n"
                f"  Kendall tau: {signal.kendall_tau}\n"
                f"  p-value: {signal.p_value}\n"
                f"  {t('dlc.trend_label')}: {trend_label}\n"
                f"\n"
                f"  {t('dlc.suggestion')}"
            )
        else:
            return (
                f"{t('dlc.not_triggered')}。{error_type}:{gv_id} "
                f"{t('dlc.sample_count')}={signal.sample_count}, "
                f"p={signal.p_value} (需 <0.05)"
            )

    # 检查全部
    signals = csd.detect_any()
    if not signals:
        return "✅ " + t("dlc.all_clear")

    lines = [f"⚠️ {len(signals)} {t('dlc.triggered_title_plural')}:", ""]
    for s in signals:
        lines.append(
            f"  {s.error_type}:{s.gv_id} | "
            f"n={s.sample_count} | tau={s.kendall_tau} | p={s.p_value}"
        )
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 工具 4: observer_gate — FreshObserver 门控
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def observer_gate(gate_type: str, claim: str = "", context: str = "",
                  five_whys: str = "", reason: str = "") -> str:
    """通过常新观察者判断一个声明是否合理。

    FreshObserver 是 kugua 的幻觉免疫系统：
      - 每次调用仅使用 ~200 tokens 上下文
      - 无历史累积，不受上下文污染
      - 基于常识判断，非领域知识

    三种门控类型：
      GATE_RCA      — 判断根因分析是否合理
      GATE_PROPOSAL — 判断规则修改是否安全
      GATE_AUDIT    — 判断盲审意见是否一致

    Args:
        gate_type: 门控类型 (GATE_RCA / GATE_PROPOSAL / GATE_AUDIT)
        claim: 待判断的声明/根因/修改提案
        context: 附加上下文（RCA=错误模式, PROPOSAL=修改前规则, AUDIT=评审摘要备选）
        five_whys: 5-Whys 链，逗号分隔（仅 GATE_RCA 使用）
        reason: 修改理由（仅 GATE_PROPOSAL 使用）
    """
    gate_type = gate_type.upper()
    if gate_type not in ("GATE_RCA", "GATE_PROPOSAL", "GATE_AUDIT"):
        return t("observer.invalid_gate", type=gate_type)

    observer = _get_observer()

    if gate_type == "GATE_RCA":
        whys_list = [w.strip() for w in five_whys.split(",") if w.strip()] if five_whys else []
        result = observer.gate_rca(
            error_pattern=context or t("observer.no_error_pattern"),
            root_cause=claim,
            five_whys=whys_list,
        )
    elif gate_type == "GATE_PROPOSAL":
        result = observer.gate_proposal(
            before=context or t("observer.no_rule_before"),
            after=claim,
            reason=reason or "",
        )
    else:
        result = observer.gate_audit(audit_summary=claim or context)

    if result.all_passed:
        return f"✅ {t('observer.passed', type=gate_type)}"
    else:
        return (
            f"⚠️ {t('observer.not_passed', type=gate_type)}\n"
            f"  {t('observer.reason')}: {result.block_reason}\n"
            f"  {t('observer.blocked_at')}: {result.blocked_at}"
        )


# ═══════════════════════════════════════════════════════════════
# 工具 5: negentropy_dash — 负熵仪表板
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def negentropy_dash() -> str:
    """获取当前负熵仪表板（5 维度 + 综合指数）。

    五维负熵度量：
      流程有序度    — 阶段跳变/回退的罚分
      意图锚定度    — 目标变更的罚分
      知识生效度    — 重复错误率
      信息保真度    — 信息检索开销
      双环效能      — 永久熵减贡献（v0.2 新增）

    综合指数 >80% 优秀, 60-80% 尚可, 40-60% 需改进, <40% 严重熵增。
    """
    from kugua.states import StatesMachine
    from kugua.negentropy import Negentropy

    _get_kb()  # ensure config loaded
    sm = StatesMachine(_cfg)
    state = sm.load_state()
    eff = _get_eff()
    ne = Negentropy(state, efficacy=eff)
    d = ne.to_dict()

    composite = d["composite"]
    if composite >= 80:
        tier = t("tier.excellent")
    elif composite >= 60:
        tier = t("tier.good")
    elif composite >= 40:
        tier = t("tier.needs_improvement")
    else:
        tier = t("tier.critical")

    lines = [
        f"{t('common.title')} · {t('negentropy_dash.title')}",
        f"",
        f"  {t('negentropy_dash.composite')}: {composite}% {tier}",
        f"",
        f"  {t('negentropy_dash.five_dimensions')}:",
        f"    {t('negentropy_dash.process_order')}:   {d['process_order']}%",
        f"    {t('negentropy_dash.intent_anchoring')}:   {d['intent_anchoring']}%",
        f"    {t('negentropy_dash.knowledge_efficacy')}:   {d['knowledge_efficacy']}%",
        f"    {t('negentropy_dash.information_fidelity')}:   {d['information_fidelity']}%",
        f"    {t('negentropy_dash.double_loop_efficacy')}:     {d['double_loop_efficacy']}%  ← {t('negentropy_dash.permanent_entropy')}",
        f"",
        f"  {t('negentropy_dash.raw_data')}:",
    ]

    raw = d.get("raw", {})
    for k, v in raw.items():
        if k != "efficacy":
            lines.append(f"    {k}: {v}")

    if "efficacy" in raw:
        lines.append(f"    {t('negentropy_dash.double_loop_events')}: {json.dumps(raw['efficacy'], ensure_ascii=False)}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 工具 6: csd_status — 临界慢化检测器状态
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def csd_status() -> str:
    """检查临界慢化检测器的当前状态。

    临界慢化是系统接近相变边界的物理信号：
      — 恢复时间单调递增 (Mann-Kendall p<0.05)
      — 意味着当前治理变量正在失效
      — 需要双环学习来修改规则本身

    Returns:
        所有被追踪的 (error_type, gv_id) 对的临界慢化状态。
    """
    csd = _get_csd()

    if not csd._history:
        return t("csd_status.no_data")

    lines = [t("csd_status.title") + ":", ""]

    for key, records in sorted(csd._history.items()):
        error_type, gv_id = key.split(":", 1)
        signal = csd.detect(error_type, gv_id)

        icon = "⚠️" if signal.critical else ("·" if signal.sample_count < 5 else "○")
        status_label = t("csd_status.critical_label") if signal.critical else t("csd_status.normal_label")
        lines.append(
            f"  {icon} {error_type}:{gv_id} | "
            f"n={signal.sample_count} | "
            f"tau={signal.kendall_tau} | "
            f"p={signal.p_value} | "
            f"{status_label}"
        )

    critical_count = sum(1 for key in csd._history
                         if csd.detect(*key.split(":", 1)).critical)
    lines.append(f"\n  {t('csd_status.summary', total=len(csd._history), critical=critical_count)}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 工具 7: record_error — 用户手动记录错误 🆕 v0.2.1
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def record_error(error_type: str, gv_id: str = "", recovery_time_s: float = 1.0,
                  description: str = "") -> str:
    """用户手动记录一个错误模式，注入 CSD 追踪器。

    用户可以绕过自动检测，直接声明"这类错误在发生"。
    CSD 会追踪该 (error_type, gv_id) 对的恢复时间序列，
    当检测到临界慢化时触发双环学习。

    Args:
        error_type: 错误类型 — 建议用 accuracy/completeness/compliance/logical_error/用户自定义
        gv_id: 治理变量 ID — 哪个规则/知识条目需修正（留空则用 error_type）
        recovery_time_s: 恢复时间(秒) — 默认 1.0，越大的值越容易触发临界信号
        description: 补充描述（可选）
    """
    csd = _get_csd()

    if not gv_id:
        gv_id = f"user_reported_{error_type}"

    csd.record_failure(
        error_type=error_type,
        gv_ids=[gv_id],
        recovery_time_s=recovery_time_s,
        task_id=f"user:{description[:80]}" if description else "user:manual",
    )

    signal = csd.detect(error_type, gv_id)
    trend_label = t("dlc.trend_decreasing") if signal.trend == -1 else (
        t("dlc.trend_increasing") if signal.trend == 1 else t("dlc.trend_none")
    )

    lines = [
        f"📝 {t('record_error.recorded')}",
        f"",
        f"  {t('dlc.error_type')}: {error_type}",
        f"  {t('dlc.gv_id')}: {gv_id}",
        f"  {t('record_error.recovery_time')}: {recovery_time_s}s",
    ]
    if description:
        lines.append(f"  {t('record_error.description')}: {description}")

    lines.extend([
        f"",
        f"  {t('record_error.current_state')}:",
        f"  {t('dlc.sample_count')}: {signal.sample_count}",
        f"  Kendall τ: {signal.kendall_tau:.4f}",
        f"  p: {signal.p_value:.4f}",
        f"  {t('dlc.trend_label')}: {trend_label}",
        f"  CSD: {'⚠️ CRITICAL' if signal.critical else '○ normal'}",
        f"",
    ])

    if signal.critical:
        lines.append(f"  ⚠️ {t('dlc.triggered_title')} — {t('dlc.suggestion')}")
    elif signal.sample_count >= 5:
        lines.append(f"  ℹ️ {t('record_error.enough_samples')}")
    else:
        lines.append(f"  ℹ️ {t('record_error.need_more', need=5 - signal.sample_count)}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 工具 8: status_all — 六项核心指标汇总 🆕 v0.2.1
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def status_all() -> str:
    """获取六项核心指标汇总：综合负熵 · 流程有序 · 意图锚定 · 知识生效 · 双环效能 · CSD临界。

    每次 Claude Code 输出末尾应调用此工具，展示系统健康全貌。
    六项指标：
      1. 综合负熵    — 整体健康度（>80%优秀 60-80%尚可 40-60%需改进 <40%严重）
      2. 流程有序度  — 阶段跳变/回退罚分
      3. 意图锚定度  — 目标变更罚分
      4. 知识生效度  — 重复错误率
      5. 双环效能    — 永久熵减贡献
      6. CSD临界     — 临界慢化信号数
    """
    from kugua.states import StatesMachine
    from kugua.negentropy import Negentropy

    _get_kb()
    sm = StatesMachine(_cfg)
    state = sm.load_state()
    eff = _get_eff()
    ne = Negentropy(state, efficacy=eff)
    nd = ne.to_dict()

    csd = _get_csd()
    csd_critical = sum(
        1 for key in csd._history
        if csd.detect(*key.split(":", 1)).critical
    ) if csd._history else 0

    kb = _get_kb()
    kb_active = sum(1 for e in kb.entries.values() if e.status == 'active') if kb.entries else 0

    composite = nd["composite"]
    if composite >= 80:
        tier_icon = "🟢"
        tier = t("tier.excellent")
    elif composite >= 60:
        tier_icon = "🟡"
        tier = t("tier.good")
    elif composite >= 40:
        tier_icon = "🟠"
        tier = t("tier.needs_improvement")
    else:
        tier_icon = "🔴"
        tier = t("tier.critical")

    csd_icon = "🟢" if csd_critical == 0 else "🔴"

    lines = [
        "┌──────────────────────────────────────────────┐",
        f"│  🍈 {t('status_all.title'):<40} │",
        "├────────────┬─────────┬────────────────────────┤",
        f"│ {t('status_all.composite'):<10} │ {composite:5.1f}% │ {tier_icon} {tier:<22} │",
        f"│ {t('status_all.process_order'):<10} │ {nd['process_order']:5.1f}% │                        │",
        f"│ {t('status_all.intent_anchoring'):<10} │ {nd['intent_anchoring']:5.1f}% │                        │",
        f"│ {t('status_all.knowledge_efficacy'):<10} │ {nd['knowledge_efficacy']:5.1f}% │ {t('status_all.kb_active', n=kb_active):<22} │",
        f"│ {t('status_all.double_loop_efficacy'):<10} │ {nd['double_loop_efficacy']:5.1f}% │ {t('status_all.double_loop_note'):<22} │",
        f"│ {t('status_all.csd_critical'):<10} │ {csd_critical:5d}   │ {csd_icon} {t('status_all.csd_note'):<20} │",
        "└────────────┴─────────┴────────────────────────┘",
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logger.info(t("install.starting"))
    logger.info("%s: %s", t("install.artifacts_dir"), ARTIFACTS_DIR)
    logger.info("%s: %s", t("install.source_dir"), KUGUA_DIR)
    mcp.run()
