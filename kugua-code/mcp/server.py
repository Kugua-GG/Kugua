#!/usr/bin/env python3
"""
Kugua MCP Server — 为 Claude Code 提供 Agent 认知内核能力。

通过 MCP stdio 协议暴露 6 个工具：
  kb_query          — 查询 L0-L3 证据层级知识库
  kb_snapshot       — 知识库统计快照
  double_loop_check — 检查双环学习触发条件
  observer_gate     — FreshObserver 门控：判断一个声明是否合理
  negentropy_dash   — 负熵仪表板（5 维）
  csd_status        — 临界慢化检测器状态

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
KUGUA_DIR = Path(os.getenv("KUGUA_CODE_DIR", "C:/Users/Administrator/Desktop/kugua-v0.2.1/kugua-code"))
sys.path.insert(0, str(KUGUA_DIR))

ARTIFACTS_DIR = Path(os.getenv("KUGUA_ARTIFACTS_DIR", "C:/Users/Administrator/.claude/.codex/artifacts"))
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

# ── FastMCP ──────────────────────────────────────────────
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "kugua",
    instructions="苦瓜code Agent 认知内核 v0.3.0 — 知识库 · 双环学习 · 观察者门控 · 负熵仪表板 · 认知监护",
)

# ── 懒加载苦瓜模块 ────────────────────────────────────────
_kb = None
_csd = None
_eff = None
_observer = None
_ne = None
_cfg = None

def _get_kb():
    global _kb, _cfg
    if _kb is None:
        from kugua.config import KuguaConfig
        from kugua.knowledge import KnowledgeBase
        _cfg = KuguaConfig.from_env()
        _cfg.artifacts_dir = ARTIFACTS_DIR
        _kb = KnowledgeBase(_cfg)
    return _kb

def _get_csd():
    global _csd
    if _csd is None:
        from kugua.critical_slowing import CriticalSlowingDetector
        _csd = CriticalSlowingDetector(artifacts_dir=ARTIFACTS_DIR)
    return _csd

def _get_eff():
    global _eff
    if _eff is None:
        from kugua.efficacy import DoubleLoopEfficacyTracker
        _eff = DoubleLoopEfficacyTracker(artifacts_dir=ARTIFACTS_DIR)
    return _eff

def _get_observer():
    global _observer, _cfg
    if _observer is None:
        _get_kb()  # 确保 _cfg 已加载
        from kugua.observer import create_observer_from_config
        if _cfg and _cfg.has_providers:
            _observer = create_observer_from_config(_cfg)
        else:
            logger.warning("无可用 LLM provider，观察者以兜底模式运行（不阻塞）")
            from kugua.observer import FreshObserver
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
        return "知识库为空。系统将在运行过程中自动积累知识。"

    # 使用倒排索引检索 (O(k) 替代 O(n))
    if query:
        scored = kb.search(query, min_level=min_level, top_k=max_results)
        entries = [e for _, e in scored]
    else:
        entries = kb.query(min_level=min_level)[:max_results]

    if not entries:
        return f"未找到匹配 '{query}' 的知识条目 (min_level={min_level})。"

    lines = []
    for e in entries:
        status_icon = {"L3": "◆", "L2": "◇", "L1": "·"}.get(e.level, "?")
        lines.append(
            f"{status_icon} [{e.level}] {e.key} (c={e.confidence:.1f}, used={e.usage_count}x)\n"
            f"   {e.content[:200]}"
        )

    lines.append(f"\n— 共 {len(entries)} 条 (活跃: {sum(1 for e in kb.entries.values() if e.status=='active')})")
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
        "🍈 苦瓜code · 知识库快照",
        "",
        "证据层级分布:",
        f"  L3 (10x验证+反例):  {by_level['L3']} 条",
        f"  L2 (3x验证):         {by_level['L2']} 条",
        f"  L1 (单次验证):       {by_level['L1']} 条",
        f"  ──────────────────────",
        f"  活跃总计:            {by_status['active']} 条",
        "",
        "状态分布:",
    ]
    for s, c in by_status.items():
        if c > 0:
            lines.append(f"  {s}: {c}")

    lines.extend([
        "",
        "双环效能:",
        f"  已验证熵减事件: {efficacy['verified_events']}",
        f"  累计 ΔS:        {efficacy['total_entropy_reduction']:.1f}",
        f"  追踪中:         {efficacy['pending_events']}",
        f"  已回退:         {efficacy['reverted_events']}",
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
            return (
                f"⚠️ 双环触发！\n"
                f"  错误类型: {error_type}\n"
                f"  治理变量: {gv_id}\n"
                f"  样本数: {signal.sample_count}\n"
                f"  Kendall tau: {signal.kendall_tau}\n"
                f"  p-value: {signal.p_value}\n"
                f"  趋势: {'递增(恶化)' if signal.trend==1 else '递减' if signal.trend==-1 else '无'}\n"
                f"\n"
                f"  建议: 执行根因分析 → 生成修改提案 → 盲审 → 验证 → 提交"
            )
        else:
            return (
                f"未触发。{error_type}:{gv_id} 样本={signal.sample_count}, "
                f"p={signal.p_value} (需 <0.05)"
            )

    # 检查全部
    signals = csd.detect_any()
    if not signals:
        return "✅ 无双环触发条件。所有 (error_type, gv_id) 对均未达临界慢化阈值。"

    lines = [f"⚠️ {len(signals)} 个双环触发条件:", ""]
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
        return f"无效门控类型: {gate_type}。可选: GATE_RCA, GATE_PROPOSAL, GATE_AUDIT"

    observer = _get_observer()

    if gate_type == "GATE_RCA":
        whys_list = [w.strip() for w in five_whys.split(",") if w.strip()] if five_whys else []
        result = observer.gate_rca(
            error_pattern=context or "未提供错误模式",
            root_cause=claim,
            five_whys=whys_list,
        )
    elif gate_type == "GATE_PROPOSAL":
        result = observer.gate_proposal(
            gv_before=context or "未提供修改前规则",
            gv_after=claim,
            reason=reason or "",
        )
    else:
        result = observer.gate_audit(audit_summary=claim or context)

    if result.all_passed:
        return f"✅ {gate_type} 通过 — 观察者未发现异常。"
    else:
        return (
            f"⚠️ {gate_type} 未通过\n"
            f"  原因: {result.block_reason}\n"
            f"  阻塞于: {result.blocked_at}"
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
        tier = "🟢 优秀"
    elif composite >= 60:
        tier = "🟡 尚可"
    elif composite >= 40:
        tier = "🟠 需改进"
    else:
        tier = "🔴 严重熵增"

    lines = [
        f"🍈 苦瓜code · 负熵仪表板",
        f"",
        f"  综合指数: {composite}% {tier}",
        f"",
        f"  五维度:",
        f"    流程有序度:   {d['process_order']}%",
        f"    意图锚定度:   {d['intent_anchoring']}%",
        f"    知识生效度:   {d['knowledge_efficacy']}%",
        f"    信息保真度:   {d['information_fidelity']}%",
        f"    双环效能:     {d['double_loop_efficacy']}%  ← 永久熵减贡献",
        f"",
        f"  原始数据:",
    ]

    raw = d.get("raw", {})
    for k, v in raw.items():
        if k != "efficacy":
            lines.append(f"    {k}: {v}")

    if "efficacy" in raw:
        lines.append(f"    双环事件: {json.dumps(raw['efficacy'], ensure_ascii=False)}")

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
        return "临界慢化检测器无数据。系统尚未积累足够的错误模式。"

    lines = ["临界慢化检测器状态:", ""]

    for key, records in sorted(csd._history.items()):
        error_type, gv_id = key.split(":", 1)
        signal = csd.detect(error_type, gv_id)

        icon = "⚠️" if signal.critical else ("·" if signal.sample_count < 5 else "○")
        lines.append(
            f"  {icon} {error_type}:{gv_id} | "
            f"n={signal.sample_count} | "
            f"tau={signal.kendall_tau} | "
            f"p={signal.p_value} | "
            f"{'CRITICAL' if signal.critical else 'normal'}"
        )

    critical_count = sum(1 for key in csd._history
                         if csd.detect(*key.split(":", 1)).critical)
    lines.append(f"\n  总计: {len(csd._history)} 个追踪对, {critical_count} 个临界")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 懒加载 Guardian
# ═══════════════════════════════════════════════════════════════

_guardian = None

def _get_guardian():
    global _guardian, _cfg
    if _guardian is None:
        _get_kb()
        from kugua.guardian import Guardian, GuardianConfig
        from kugua.safety import SafetyManager
        from kugua.critical_slowing import CriticalSlowingDetector

        safety = SafetyManager(_cfg) if _cfg else None
        csd = CriticalSlowingDetector(artifacts_dir=ARTIFACTS_DIR)

        observer = None
        try:
            observer = _get_observer()
        except Exception:
            pass

        guardian_cfg = GuardianConfig(
            confidence_threshold=float(os.getenv("KUGUA_CONFIDENCE_THRESHOLD", "0.7")),
            permission_mode=os.getenv("KUGUA_PERMISSION_MODE", "block"),
            artifacts_dir=ARTIFACTS_DIR,
        )

        _guardian = Guardian(
            config=guardian_cfg,
            safety_manager=safety,
            csd=csd,
            observer=observer,
            kb=_kb,
        )
    return _guardian


# ═══════════════════════════════════════════════════════════════
# 工具 7: guardian_check — 认知监护审查（v0.3.0 新增）
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def guardian_check(
    agent_output: str = "",
    operation: str = "",
    confidence: float = 1.0,
    session_id: str = "default",
    error_type: str = "",
    gv_id: str = "",
) -> str:
    """认知监护审查 — kugua 作为只读观察者监控 Agent。

    每次主 Agent 输出后调用此工具，kugua 执行三层检查:
      1. 置信度门控 — 低于阈值则建议重新生成
      2. 临界慢化检测 — 恢复时间趋势恶化则建议减速
      3. 权限门控 — 违反安全规则则阻断

    可作为 LangGraph/AutoGen/CrewAI 的 Sidecar 监护节点。

    Args:
        agent_output: Agent 的输出内容（用于 Observer 深度审查）
        operation: Agent 试图执行的操作 (read_file/write_file/execute_cmd/...)
        confidence: Agent 对输出的置信度 [0, 1]，低于 0.7 触发拦截
        session_id: 会话标识（同一会话累积监护统计）
        error_type: 错误类型（用于临界慢化追踪）
        gv_id: 治理变量 ID（用于临界慢化追踪）

    Returns:
        JSON 格式的监护判决，包含 intervene/action/reason/suggestion 字段。
    """
    guardian = _get_guardian()

    verdict = guardian.check(
        agent_output=agent_output,
        operation=operation,
        confidence=float(confidence),
        session_id=session_id,
        error_type=error_type,
        gv_id=gv_id,
    )

    return verdict.to_json()


# ═══════════════════════════════════════════════════════════════
# 工具 8: guardian_benchmark — 性能基准（v0.3.0 新增）
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def guardian_benchmark() -> str:
    """获取认知监护的性能基准报告。

    包含:
      - 延迟百分位 (P50/P95/P99)
      - 内存占用 (RSS)
      - 总检查次数 / 介入率
      - 各会话的独立统计

    用于集成方做成本评估。
    """
    guardian = _get_guardian()
    report = guardian.benchmark_report()

    lines = [
        "🍈 kugua Guardian · 性能基准",
        "",
        "延迟 (ms):",
        f"  P50:  {report['latency']['p50_ms']}",
        f"  P95:  {report['latency']['p95_ms']}",
        f"  P99:  {report['latency']['p99_ms']}",
        f"  平均: {report['latency']['avg_ms']}",
        f"  样本: {report['latency']['sample_count']}",
        "",
        "吞吐:",
        f"  总检查:   {report['throughput']['total_checks']}",
        f"  总介入:   {report['throughput']['total_interventions']}",
        f"  介入率:   {report['throughput']['intervention_rate']}",
        "",
        f"内存 (RSS): {report['memory']['rss_mb']} MB",
        f"活跃会话:   {report['sessions']}",
        "",
        "配置:",
        f"  置信度阈值: {report['config']['confidence_threshold']}",
        f"  权限模式:   {report['config']['permission_mode']}",
        f"  深度审查:   {report['config']['deep_review_enabled']}",
    ]

    # 每个会话的详情
    guardian_obj = _get_guardian()
    for session in guardian_obj.list_sessions():
        d = session.to_dict()
        lines.extend([
            "",
            f"会话 {d['session_id']}:",
            f"  检查: {d['total_checks']} | 介入: {d['interventions']} "
            f"(阻断{d['blocks']}/警告{d['warnings']}/重试{d['retries']})",
            f"  P50={d['latency_p50_ms']}ms P95={d['latency_p95_ms']}ms",
        ])

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logger.info("启动 Kugua MCP Server v0.3.0 (stdio) — 含认知监护模式")
    logger.info("制品目录: %s", ARTIFACTS_DIR)
    logger.info("苦瓜代码: %s", KUGUA_DIR)
    mcp.run()
