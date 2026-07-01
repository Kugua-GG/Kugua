# 🍈 kugua — AI Agent 认知内核 v0.3.0

> **为 LLM Agent 装上免疫系统。** 不是另一个 Agent 框架，而是一个无侵入的 Sidecar 认知监护层。

> ⚠️ **这是一个研究原型 (research prototype)，不是生产级产品。** 公开发布用于学术复现和同行审查。不承诺维护、不提供 support SLA、不保证 API 稳定性。如果你觉得有用，欢迎 fork 和研究；如果你提 Issue，我可能回复也可能不回复。

[![Python](https://img.shields.io/badge/python-3.11+-blue)](https://python.org)
[![Tests](https://img.shields.io/badge/tests-200+-green)](tests/)
[![License](https://img.shields.io/badge/license-Apache%202.0-orange)](LICENSE)
[![Status](https://img.shields.io/badge/status-research%20prototype-lightgrey)](README.md)

## 为什么需要 kugua

LLM Agent 有三个根本问题：

| 问题 | kugua 的解法 |
|------|-------------|
| **安全**: Agent 可能执行危险操作 | 五级信任梯度 + Kill Switch + 三级权限门控 |
| **幻觉**: 模型自信地输出错误信息 | FreshObserver 独立盲审 + L0-L3 知识库校准 |
| **退化**: 系统性能随时间下降但无人察觉 | 临界慢化预警 + 双环学习自动修正规则 |

对照实验验证（Cohen's d）：

| 实验 | 指标 | 裸 Agent | kugua | Cohen's d |
|------|------|---------|-------|-----------|
| 安全门控 | 危险动作/次 | 0.94 | **0.0** | 4.97 |
| 幻觉免疫 | 幻觉率 | 53% | **13%** | 4.0 |
| 双环学习 | 架构/表面升级比 | — | **0.67×** | — |
| 临界慢化 | 崩塌前预警 | — | **38-68%提前** | — |

## 它到底做了什么

kugua 的每个模块都不是简单的"if-else 检查"。

### SafetyManager — 信任随时间变化的活系统

不是静态的 `{操作: 允许/禁止}` 查表。系统初始 L2 信任，连续安全操作**自动升级**到 L3→L4。每次高风险操作消耗 ErrorBudget，预算耗尽**自动降级**到 L1（只读）。Kill Switch 有三条触发链——区分**权限拒绝**（计入熔断）和**预算不足**（加权 80%，不计入），根据当前 P0-P4 阶段动态调整阈值。校准参数来自 130,000 次混沌试验的 ROC 扫描，不是拍脑袋的数字。

### StatesMachine — 状态转换不是"改个字段"

`transition()` 内部是三层：**前置条件验证 → 执行转换 → 后置条件验证**。后置失败则回退到转换前状态。P3 审查失败不是"记录一下"，而是 **Saga 补偿事务回退**——逆序撤销 P2→P1，执行每个阶段的补偿方法。关键阶段自动保存检查点，崩溃后从最近合法检查点恢复。同一阶段连续 3 次无法推进 → `PhaseStagnationGuard` 发出 replan 信号。

### DoubleLoop + Mobius — 不是"重试"，是"改规则"

单环学习 = 这次错了，重试。双环学习 = **这条规则有问题，换掉它**。六阶段流水线：5-Whys 根因分析 → 规则修改提案 → FreshObserver 独立盲审 → 3 审查者投票 → 验证 → 提交/回滚。Mobius 不是二进制的"触发/不触发"，而是一个从 L0_HINT（轻提示）到 L4_COMMIT（修改规则）的**连续谱**——偏差积累、时间衰减、置信度加权，像弹簧慢慢压紧，达到阈值才释放。实验验证：架构错误仅需 4 次出现即触发双环，表面错误需要 6 次——谱系正确区分了严重性。

### CriticalSlowing — 不是"性能监控"，是"崩塌预警"

灵感来自物理学的**相变理论**。系统在崩溃前，恢复时间会单调递增且方差增大。Mann-Kendall 检验检测这种趋势。两阶段检测器（z-score 筛选 + MK 确认）在 200 步长期运行中**提前 38-68% 发出预警**，无崩塌误报率 0%。三种崩塌模式（渐进式、阶跃式、振荡式）均可检测。纯 Python 自实现 erf 近似，零外部依赖。

### Guardian — 四层管道式监护

```
置信度 < 0.7 → retry（请求重新生成）
  ↓ 通过
临界慢化信号 → slow_down（建议切换策略）
  ↓ 通过
权限门控 → block（直接阻断）
  ↓ 通过
FreshObserver 盲审 → 独立 LLM 判断内容安全性
```

每一层可独立开关。所以同一个 Guardian 可以配置给代码审查 Agent（全开）、命令行工具（只开权限）、RAG 幻觉检测（只开置信度）。

### KnowledgeBase — 知识不是平等的

L0-L3 证据层级：L3 公理（10+ 上下文验证 + 反例测试）**不可降级**。L1 条目失败 5 次且未被使用 → 自动垃圾回收。双阈值逻辑冲突检测（否定词计数 + 关键词重叠）。BM25 Okapi 倒排索引，中英混合三层分词。图知识库支持 BFS 联想检索和图拉普拉斯置信度扩散。

### 这些不是"我觉得有用"——是可复现的对照实验

| 实验 | 指标 | 裸 Agent | kugua | Cohen's d |
|------|------|---------|-------|-----------|
| 安全门控 | 危险动作/次 | 0.94 | **0.0** | 4.97 |
| 幻觉免疫 | 幻觉率 | 53% | **13%** | 4.0 |
| 双环学习 | 架构/表面升级比 | — | **0.67×** | — |
| 临界慢化 | 崩塌前预警 | — | **38-68%提前** | — |

对抗测试：4/4 攻击模式 100% 拦截。混沌工程：30 次试验零漏报。

## 3 分钟接入

```python
from kugua.client import GuardianClient

gc = GuardianClient(confidence_threshold=0.7)

decision = gc.check(prompt=agent_output, action="write_file", confidence=0.85)
if decision.allowed:
    execute()
else:
    print(f"Blocked: {decision.reason}")
```

或作为 Sidecar HTTP：

```bash
python -m kugua.api_server --port 5000
# LangGraph / AutoGen / CrewAI → POST /api/guardian/check
```

## 架构

```
你的 Agent (LangGraph/AutoGen/...)     ← 你已有的代码
        │ 每次工具调用前
        ▼
┌─ kugua Sidecar (只读观察者) ─────────────┐
│  Guardian ── 四层认知监护                  │
│  StatesMachine ── P0-P4 状态机 + Saga     │
│  SafetyManager ── 信任梯度 + Kill Switch   │
│  DoubleLoop ── 双环学习 (修改规则本身)      │
│  Mobius ── 五级连续修正谱                  │
│  CriticalSlowing ── 崩塌预警               │
│  KnowledgeBase ── L0-L3 证据层级           │
│  FreshObserver ── 独立盲审 (无上下文污染)   │
├───────────────────────────────────────────┤
│  MCP (8工具) · REST (7端点) · SDK (3行)   │
└───────────────────────────────────────────┘
```

## 安装

```bash
git clone https://github.com/<your-username>/kugua.git
cd kugua/kugua-code
pip install -e .
```

## 运行测试

```bash
cd tests
python kugua_kernel_tests.py      # 54/54
python kugua_mobius_tests.py      # 42/42
python test_safety.py             # 32/32
python test_states.py             # 36/36
python test_main_loop.py          # 19/19
python test_error_degradation.py  # 15/15
python test_guardian.py           # 15/15
python test_diffusion.py          # 15/15
python test_negentropy.py         # 17/17
```

## 复现对照实验

```bash
# 实验1: 安全门控混沌测试
python experiments/exp1_safety_gate/run_experiment.py --runs 200
# 实验2: 幻觉免疫测试
python experiments/exp2_hallucination/run_experiment.py --questions 15
# 实验3: 双环学习有效性
python experiments/exp3_double_loop/run_experiment.py --cycles 5
# 实验4: 临界慢化预测
python experiments/exp4_critical_slowing/run_experiment.py --steps 50
```

## 对抗攻击测试

```bash
python benchmarks/adversarial_tests.py
# 4/4 攻击模式全拦截: 提示注入 / 上下文污染 / 工具输出篡改 / 预算耗尽
```

## 仓库结构

```
kugua/
├── kugua-code/kugua/        # 内核 (20模块, 9,000+行纯Python)
│   ├── safety.py            #   安全门控 + 信任梯度 + Kill Switch
│   ├── states.py            #   P0-P4 状态机 + Saga补偿 + 检查点
│   ├── guardian.py          #   认知监护 (4层检查)
│   ├── main_loop.py         #   主循环编排
│   ├── double_loop.py       #   双环学习 (6阶段)
│   ├── mobius.py            #   莫比乌斯五级修正谱
│   ├── critical_slowing.py  #   Mann-Kendall临界慢化
│   ├── knowledge.py         #   BM25+L0-L3知识库
│   ├── graph.py             #   图知识库
│   ├── observer.py          #   FreshObserver幻觉免疫
│   ├── meta_reviewer.py     #   4模板×3审查者盲审
│   ├── diffusion.py         #   图拉普拉斯置信度扩散
│   ├── negentropy.py        #   五维负熵仪表板
│   ├── client.py            #   GuardianClient SDK
│   ├── api_server.py        #   REST API (7端点 Sidecar)
│   └── ...
├── mcp/server.py            # MCP Server (8 tools)
├── tests/                   # 200+ 测试断言
├── experiments/             # 4项对照实验 (可复现)
│   ├── exp1_safety_gate/
│   ├── exp2_hallucination/
│   ├── exp3_double_loop/
│   └── exp4_critical_slowing/
├── benchmarks/              # 性能基准 + 对抗测试 + 混沌工程
├── examples/                # 集成示例 (LangChain/CLI/RAG/代码审查)
├── INTEGRATION.md           # 3分钟集成指南
└── README.md
```

## 许可证

Apache 2.0 — 详见 [LICENSE](LICENSE)
