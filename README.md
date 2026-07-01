# 🍈 kugua — AI Agent 认知内核 v0.3.0

> **为 LLM Agent 装上免疫系统。** 不是另一个 Agent 框架，而是一个无侵入的 Sidecar 认知监护层——在你的 Agent 每次决策前检查安全性、置信度和系统健康。

[![Python](https://img.shields.io/badge/python-3.11+-blue)](https://python.org)
[![Tests](https://img.shields.io/badge/tests-200+-green)](tests/)
[![License](https://img.shields.io/badge/license-Apache%202.0-orange)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.3.0-purple)](kugua-code/kugua/__init__.py)

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

## 3 分钟接入

```python
from kugua.client import GuardianClient

gc = GuardianClient(confidence_threshold=0.7)

# 每次 Agent 工具调用前
decision = gc.check(prompt=agent_output, action="write_file", confidence=0.85)

if decision.allowed:
    execute()
else:
    print(f"Blocked: {decision.reason}")  # 自动包含建议
```

或作为 Sidecar HTTP 服务：

```bash
python -m kugua.api_server --port 5000
```

```python
# LangGraph / AutoGen / CrewAI 调用
import requests
r = requests.post("http://localhost:5000/api/guardian/check", json={
    "operation": "write_file", "confidence": 0.85
})
if not r.json()["intervene"]:
    execute()
```

## 架构

```
┌─────────────────────────────────────────┐
│  Python 层 (LLM编排 + 认知策略)           │
│                                         │
│  Guardian    — 认知监护 (4层检查)         │
│  StatesMachine — δ(S,E)→S' 状态机       │
│  SafetyManager — 信任梯度 + Kill Switch  │
│  DoubleLoop  — 双环学习 (修改规则本身)     │
│  Mobius      — 五级连续修正谱            │
│  CriticalSlowing — 临界慢化预警          │
│  KnowledgeBase — L0-L3 证据层级          │
│  FreshObserver — 独立盲审 (无上下文污染)   │
├─────────────────────────────────────────┤
│  集成层                                  │
│  MCP Server (8 tools)                   │
│  REST API (7 endpoints)                 │
│  GuardianClient SDK (3行接入)            │
└─────────────────────────────────────────┘
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
