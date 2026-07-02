"""
kugua — AI Agent 认知内核 v0.3.0（kugua core）

三层架构：
  指令层 (CLAUDE.md)  →  桥接层 (kugua MCP)  →  内核层 (本包)

核心子系统 (22 模块, ~9400 行):
  状态机        states.py       P0-P4 严格状态推进 + 崩溃恢复
  知识库        knowledge.py    L0-L3 证据层级 + BM25 倒排索引 + KBEntry
  图知识库      graph.py        GraphKB 有向图 + GraphRetriever
  双环学习      double_loop.py  单环(修行为)→双环(修规则)
  莫比乌斯      mobius.py       五级修正谱 (L0_HINT → L4_COMMIT)
  临界慢化      critical_slowing.py  Mann-Kendall 趋势检验
  安全门控      safety.py       5 级信任梯度 + Kill Switch
  权限门控      permission.py   PermissionGate 独立权限控制
  负熵          negentropy.py   五维健康仪表板
  上下文        context.py      L0/L1/L2 分层记忆
  执行器        executor.py     多 Provider LLM 客户端 + TaskExecutor
  API 服务      api_server.py   REST API 封装
  观察者        observer.py     FreshObserver 幻觉免疫
  上下文压缩    context_compressor.py  ContextCompressor + ObserverWeight
  校准          calibration/    BayesianCalibrator
  扩散          diffusion.py    图拉普拉斯置信度扩散
  效力追踪      efficacy.py     DoubleLoopEfficacyTracker

快速开始:
    from kugua import KuguaConfig, TaskExecutor, LLMClient
    cfg = KuguaConfig.from_env()
    client = LLMClient(cfg)
    executor = TaskExecutor(client, cfg)
"""

__version__ = "0.3.0"

# ── 安全导入辅助 ──────────────────────────────────────────
import warnings as _warnings
from typing import Optional as _Optional, Any as _Any


def _safe_import(name: str, message: str = "") -> _Optional[_Any]:
    """安全导入：模块缺失时发 warning 并返回 None。"""
    try:
        return __import__(name, fromlist=["*"])
    except ImportError as e:
        if not message:
            message = f"kugua: 模块 '{name}' 加载失败，部分功能不可用 ({e})"
        _warnings.warn(message, ImportWarning)
        return None


# ═════════════════════════════════════════════════════════════
# 配置 (必需)
# ═════════════════════════════════════════════════════════════

from kugua.config import KuguaConfig

# ── i18n 多语言 🆕 v0.3.0 ──────────────────────────────
from kugua.i18n import (
    t, set_lang, get_lang, list_langs, SUPPORTED_LANGS, get_all_strings_for_lang
)


# ═════════════════════════════════════════════════════════════
# 状态机 (必需)
# ═════════════════════════════════════════════════════════════

from kugua.states import (
    StatesMachine,
    PhaseTransitionError,
    AlignResult,
    VALID_PHASES,
    PHASE_ORDER,
)


# ═════════════════════════════════════════════════════════════
# 安全 (必需)
# ═════════════════════════════════════════════════════════════

from kugua.safety import (
    SafetyManager,
    TrustLevel,
    Incident,
    OPERATION_PERMISSIONS,
    AuditTrail,
    AuditEntry,
)


# ═════════════════════════════════════════════════════════════
# 负熵 (必需)
# ═════════════════════════════════════════════════════════════

from kugua.negentropy import (
    Negentropy,
    NegentropyHistory,
    generate_dashboard,
    generate_integrity_report,
    DEFAULT_WEIGHTS,
    permutation_entropy,
    shannon_entropy,
)


# ═════════════════════════════════════════════════════════════
# 上下文 (必需)
# ═════════════════════════════════════════════════════════════

from kugua.context import (
    ContextManager,
    LayerType,
    L0Layer,
    L1Layer,
    L2Entry,
    L2Layer,
)


# ═════════════════════════════════════════════════════════════
# 知识库 (条件导入)
# ═════════════════════════════════════════════════════════════

try:
    from kugua.knowledge import KnowledgeBase, KBEntry, InvertedIndex
except ImportError:
    KnowledgeBase = None      # type: ignore
    KBEntry = None            # type: ignore
    InvertedIndex = None      # type: ignore
    _warnings.warn(
        "kugua.knowledge 未安装。知识库功能不可用。"
        "请确保 knowledge.py 存在于 kugua/ 目录中。",
        ImportWarning,
    )


# ═════════════════════════════════════════════════════════════
# 图知识库 (条件导入)
# ═════════════════════════════════════════════════════════════

try:
    from kugua.graph import GraphKB, GraphRetriever
except ImportError:
    GraphKB = None            # type: ignore
    GraphRetriever = None     # type: ignore
    _warnings.warn(
        "kugua.graph 未安装。图知识库功能不可用。",
        ImportWarning,
    )


# ═════════════════════════════════════════════════════════════
# LLM 执行器 (条件导入 — MCP/CLI 必需)
# ═════════════════════════════════════════════════════════════

try:
    from kugua.executor import LLMClient, TaskExecutor
except ImportError:
    LLMClient = None          # type: ignore
    TaskExecutor = None       # type: ignore
    _warnings.warn(
        "kugua.executor 未安装。LLM 调用功能不可用。",
        ImportWarning,
    )


# ═════════════════════════════════════════════════════════════
# 双环学习 (条件导入)
# ═════════════════════════════════════════════════════════════

try:
    from kugua.double_loop import DoubleLoopExecutor
except ImportError:
    DoubleLoopExecutor = None  # type: ignore
    _warnings.warn(
        "kugua.double_loop 未安装。双环学习功能不可用。",
        ImportWarning,
    )


# ═════════════════════════════════════════════════════════════
# 莫比乌斯环 (条件导入)
# ═════════════════════════════════════════════════════════════

try:
    from kugua.mobius import (
        MobiusController,
        CorrectionSpectrum,
        CorrectionBias,
        TwistPoint,
    )
except ImportError:
    MobiusController = None    # type: ignore
    CorrectionSpectrum = None  # type: ignore
    CorrectionBias = None      # type: ignore
    TwistPoint = None          # type: ignore
    _warnings.warn(
        "kugua.mobius 未安装。莫比乌斯连续谱功能不可用。",
        ImportWarning,
    )


# ═════════════════════════════════════════════════════════════
# 临界慢化 (条件导入 — MCP 必需)
# ═════════════════════════════════════════════════════════════

try:
    from kugua.critical_slowing import CriticalSlowingDetector
except ImportError:
    CriticalSlowingDetector = None  # type: ignore
    _warnings.warn(
        "kugua.critical_slowing 未安装。临界慢化检测功能不可用。",
        ImportWarning,
    )


# ═════════════════════════════════════════════════════════════
# 观察者 (条件导入 — MCP 必需)
# ═════════════════════════════════════════════════════════════

try:
    from kugua.observer import FreshObserver
except ImportError:
    FreshObserver = None       # type: ignore
    _warnings.warn(
        "kugua.observer 未安装。FreshObserver 功能不可用。",
        ImportWarning,
    )


# ═════════════════════════════════════════════════════════════
# 上下文压缩 (条件导入)
# ═════════════════════════════════════════════════════════════

try:
    from kugua.context_compressor import ObserverWeight
except ImportError:
    ObserverWeight = None      # type: ignore
    _warnings.warn(
        "kugua.context_compressor 未安装。ObserverWeight 功能不可用。",
        ImportWarning,
    )


# ═════════════════════════════════════════════════════════════
# 贝叶斯校准 (条件导入)
# ═════════════════════════════════════════════════════════════

try:
    from kugua.calibration.bayesian import BayesianCalibrator
except ImportError:
    BayesianCalibrator = None  # type: ignore
    _warnings.warn(
        "kugua.calibration.bayesian 未安装。贝叶斯校准功能不可用。",
        ImportWarning,
    )


# ═════════════════════════════════════════════════════════════
# 权限门控 (条件导入)
# ═════════════════════════════════════════════════════════════

try:
    from kugua.permission import PermissionGate
except ImportError:
    PermissionGate = None      # type: ignore
    _warnings.warn(
        "kugua.permission 未安装。独立权限门控功能不可用。",
        ImportWarning,
    )


# ═════════════════════════════════════════════════════════════
# 效力追踪 (条件导入 — MCP 必需)
# ═════════════════════════════════════════════════════════════

try:
    from kugua.efficacy import DoubleLoopEfficacyTracker
except ImportError:
    DoubleLoopEfficacyTracker = None  # type: ignore
    _warnings.warn(
        "kugua.efficacy 未安装。双环效力追踪功能不可用。",
        ImportWarning,
    )


# ═════════════════════════════════════════════════════════════
# KuguaKernel (条件导入 — 统一运行时入口)
# ═════════════════════════════════════════════════════════════

try:
    from kugua.kernel import KuguaKernel
except ImportError:
    KuguaKernel = None  # type: ignore
    _warnings.warn(
        "kugua.kernel 未安装。统一运行时不可用。",
        ImportWarning,
    )


# ═════════════════════════════════════════════════════════════
# 公开 API
# ═════════════════════════════════════════════════════════════

__all__ = [
    # 版本
    "__version__",

    # 配置
    "KuguaConfig",

    # 状态机
    "StatesMachine",
    "PhaseTransitionError",
    "AlignResult",
    "VALID_PHASES",
    "PHASE_ORDER",

    # 安全
    "SafetyManager",
    "TrustLevel",
    "Incident",
    "OPERATION_PERMISSIONS",
    "AuditTrail",
    "AuditEntry",

    # 知识库
    "KnowledgeBase",
    "KBEntry",
    "InvertedIndex",

    # 图知识库
    "GraphKB",
    "GraphRetriever",

    # LLM 执行器
    "LLMClient",
    "TaskExecutor",

    # 双环学习
    "DoubleLoopExecutor",

    # 莫比乌斯
    "MobiusController",
    "CorrectionSpectrum",
    "CorrectionBias",
    "TwistPoint",

    # 临界慢化
    "CriticalSlowingDetector",

    # 观察者
    "FreshObserver",

    # 上下文压缩
    "ObserverWeight",

    # 贝叶斯校准
    "BayesianCalibrator",

    # 权限
    "PermissionGate",

    # 效力追踪
    "DoubleLoopEfficacyTracker",

    # 负熵
    "Negentropy",
    "NegentropyHistory",
    "generate_dashboard",
    "generate_integrity_report",
    "DEFAULT_WEIGHTS",
    "permutation_entropy",
    "shannon_entropy",

    # 上下文
    "ContextManager",
    "LayerType",
    "L0Layer",
    "L1Layer",
    "L2Entry",
    "L2Layer",

    # 统一状态摘要 🆕
    "get_dashboard_summary",

    # 统一运行时 🆕 v0.2.2
    "KuguaKernel",

    # i18n 多语言 🆕
    "t",
    "set_lang",
    "get_lang",
    "list_langs",
    "SUPPORTED_LANGS",
    "get_all_strings_for_lang",
]


# ═════════════════════════════════════════════════════════════
# 便利函数
# ═════════════════════════════════════════════════════════════

def get_version() -> str:
    """返回 kugua 版本号。"""
    return __version__


def available_modules() -> dict:
    """返回各模块的可用性状态。"""
    def _check(module_name: str, class_ref) -> bool:
        return class_ref is not None

    return {
        "config": _check("config", KuguaConfig),
        "states": _check("states", StatesMachine),
        "safety": _check("safety", SafetyManager),
        "knowledge": _check("knowledge", KnowledgeBase),
        "graph": _check("graph", GraphKB),
        "executor": _check("executor", LLMClient),
        "double_loop": _check("double_loop", DoubleLoopExecutor),
        "mobius": _check("mobius", MobiusController),
        "critical_slowing": _check("critical_slowing", CriticalSlowingDetector),
        "observer": _check("observer", FreshObserver),
        "context_compressor": _check("context_compressor", ObserverWeight),
        "calibration": _check("calibration", BayesianCalibrator),
        "permission": _check("permission", PermissionGate),
        "efficacy": _check("efficacy", DoubleLoopEfficacyTracker),
        "negentropy": _check("negentropy", Negentropy),
        "context": _check("context", ContextManager),
    }


def init_minimal() -> dict:
    """使用最小配置初始化 kugua 内核（无 LLM provider）。

    Returns:
        包含核心实例的字典: config, states, safety
    """
    cfg = KuguaConfig()
    sm = StatesMachine(cfg)
    sf = SafetyManager(cfg)

    return {
        "config": cfg,
        "states": sm,
        "safety": sf,
    }


def init_full() -> dict:
    """使用环境配置完整初始化 kugua 内核（含 LLM）。

    优先使用 KuguaKernel.from_env() (v0.2.2+) 的统一初始化路径。
    如果 kernel 不可用,回退到手动逐个初始化。

    Returns:
        包含所有核心实例的字典，或 KuguaKernel 实例的 dashboard()。
    """
    if KuguaKernel is not None:
        try:
            kernel = KuguaKernel.from_env()
            return kernel.dashboard()
        except Exception:
            pass  # Fall back to manual init below

    # Legacy manual init (pre-v0.2.2)
    cfg = KuguaConfig.from_env()
    sm = StatesMachine(cfg)
    sf = SafetyManager(cfg)

    result = {
        "config": cfg,
        "states": sm,
        "safety": sf,
    }

    if LLMClient is not None:
        client = LLMClient(cfg)
        result["llm_client"] = client
        if TaskExecutor is not None:
            result["task_executor"] = TaskExecutor(client, cfg)

    if KnowledgeBase is not None:
        result["knowledge_base"] = KnowledgeBase(cfg)

    result["context"] = ContextManager(cfg)

    if CriticalSlowingDetector is not None:
        result["csd"] = CriticalSlowingDetector(
            artifacts_dir=cfg.artifacts_dir
        )

    return result


# ═════════════════════════════════════════════════════════════
# 统一状态摘要 🆕 v0.3.0
# ═════════════════════════════════════════════════════════════

def get_dashboard_summary(config: "KuguaConfig | None" = None) -> dict:
    """返回六项核心指标摘要（供 MCP / CLI / API 统一调用）。

    六项指标：
      1. composite          — 综合负熵指数 (0-100)
      2. process_order      — 流程有序度 (0-100)
      3. intent_anchoring   — 意图锚定度 (0-100)
      4. knowledge_efficacy — 知识生效度 (0-100)
      5. double_loop_efficacy — 双环效能 (0-100)
      6. csd_critical       — CSD 临界信号数 (整数)

    Returns:
        dict: 六项指标 + KB活跃条目数 + 等级标签
    """
    if config is None:
        cfg = KuguaConfig.from_env()
    else:
        cfg = config

    cfg_artifacts = getattr(cfg, 'artifacts_dir', None)
    if cfg_artifacts is None:
        from pathlib import Path as _Path
        cfg_artifacts = _Path.home() / ".claude" / ".codex" / "artifacts"

    # 负熵
    from kugua.states import StatesMachine
    from kugua.negentropy import Negentropy
    sm = StatesMachine(cfg)
    state = sm.load_state()
    nd = Negentropy(state).to_dict()

    # CSD
    csd_critical = 0
    try:
        from kugua.critical_slowing import CriticalSlowingDetector
        csd = CriticalSlowingDetector(artifacts_dir=cfg_artifacts)
        if csd._history:
            csd_critical = sum(
                1 for key in csd._history
                if csd.detect(*key.split(":", 1)).critical
            )
    except Exception:
        pass

    # KB 活跃条目数
    kb_active = 0
    try:
        from kugua.knowledge import KnowledgeBase
        kb = KnowledgeBase(cfg)
        if kb.entries:
            kb_active = sum(1 for e in kb.entries.values() if e.status == 'active')
    except Exception:
        pass

    composite = nd["composite"]
    if composite >= 80:
        tier = "优秀"
    elif composite >= 60:
        tier = "尚可"
    elif composite >= 40:
        tier = "需改进"
    else:
        tier = "严重熵增"

    return {
        "version": __version__,
        "composite": composite,
        "process_order": nd["process_order"],
        "intent_anchoring": nd["intent_anchoring"],
        "knowledge_efficacy": nd["knowledge_efficacy"],
        "double_loop_efficacy": nd["double_loop_efficacy"],
        "csd_critical": csd_critical,
        "kb_active": kb_active,
        "tier": tier,
    }
