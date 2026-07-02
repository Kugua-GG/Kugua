# 🍈 kugua core · Agent 认知内核 v0.3.0

> 一套被严格验证过的 "AI Agent 自我改进系统" 设计图纸 + 工程骨架。
> 纯 Python stdlib，零外部依赖。~9400 行，22 模块。

---

## 核心理念

AI Agent 应该像生命体一样：**知道自己知道什么、能从错误中修改规则、能被外部独立验证**。

kugua 不是框架，不是产品——它是一套**设计哲学的可执行证明**：
三层架构（软约束→桥接→硬约束）形成一个闭环的自我改进系统。

```
CLAUDE.md (指令层)     ← 29 Skills + 安全红线 + 方法论铁律
     │
kugua MCP (桥接层)     ← 6 工具暴露内核给 Claude Code
     │
kugua core (内核层)    ← 22 模块，纯 stdlib，硬约束
```

---

## 架构

| 子系统 | 模块 | 功能 |
|--------|------|------|
| **状态机** | `states.py` `main_loop.py` | P0→P4 严格状态推进 + 崩溃恢复 |
| **知识库** | `knowledge.py` `graph.py` | L0-L3 证据层级 + BM25 倒排索引 + GraphKB |
| **双环学习** | `double_loop.py` `mobius.py` | 单环(修行为)→双环(修规则) 莫比乌斯连续谱 |
| **临界慢化** | `critical_slowing.py` | Mann-Kendall 趋势检验，预警系统相变 |
| **安全** | `safety.py` `permission.py` | 5 级信任梯度 + Kill Switch |
| **负熵** | `negentropy.py` | 五维健康仪表板 |
| **执行器** | `executor.py` `api_server.py` | 多 Provider LLM 客户端 + REST API |
| **观察者** | `observer.py` `context_compressor.py` | FreshObserver 幻觉免疫 + ObserverWeight |
| **校准** | `calibration/bayesian.py` | P4 贝叶斯置信度校准 (ZOMBIE v0.3+) |
| **扩散** | `diffusion.py` | 图拉普拉斯置信度扩散 (ZOMBIE v0.3+) |

---

## 知识库：L0-L3 + 公理塔

```
L3 (50 条公理)    ← is_constant=True，不可降级
L2 (验证中)       ← 3+ 上下文验证
L1 (单次验证)     ← 53 条 LLM 修复规则
L0 (假设)         ← 自动过滤
```

- **BM25 Okapi 倒排索引**：O(k) 检索，纯 Python stdlib
- **GraphKB**：149 节点，1811 边，α=1.315
- **公理塔**：50 条世界常数，互引用团

---

## 莫比乌斯环 (Möbius Loop) 🆕

```python
from kugua.mobius import MobiusController, CorrectionSpectrum

# 五级修正谱 — 单环到双环的连续过渡
L0_HINT      → prompt 提示 (单环侧)
L1_BIAS      → CorreationBias 注入 Worker
L2_OVERRIDE  → 临时规则覆盖 (扭转点)
L3_CANDIDATE → KB 候选，待验证
L4_COMMIT    → 完整双环 → 修改规则本身
```

对标 DSpark Confidence-Scheduled Verification，连续门控替代离散状态机。

---

## 多语言支持 🆕

通过 `KUGUA_LANG` 环境变量切换: `zh-CN` (简体) | `zh-TW` (繁体) | `en` (英语) | `ja` (日语)

```bash
set KUGUA_LANG=en        # Windows
export KUGUA_LANG=ja     # Unix
```

## MCP 工具 (7 个)

| 工具 | 用途 |
|------|------|
| `kb_query` | BM25 检索 L0-L3 知识库 |
| `kb_snapshot` | 知识库健康快照 |
| `double_loop_check` | 双环触发条件检测 |
| `observer_gate` | FreshObserver 独立盲审 |
| `negentropy_dash` | 五维负熵仪表板 |
| `csd_status` | 临界慢化检测器状态 |

---

## 实验验证

| 能力 | 状态 | 证据 |
|------|------|------|
| 知识净增长 | ✅ | 50→144 条有效规则 |
| 无标度结构 | ⚠️ | α=1.315，38 种度值，趋近 2-3 |
| 双环触发 | ✅ | 9 次触发，53 条修复规则 |
| 学习加速 | ✅ | BM25 后斜率 -124ms/任务 |
| 公理保护 | ✅ | 50 条 L3 零降级 |
| 噪声过滤 | ✅ | noise 95%→3% |
| 真实 LLM 集成 | ✅ | Mimo v2-flash，90+ 次调用 |
| Observer 独立盲审 | ⚠️ | Key1 已连接，校准中 |
| 生产修复 | ❌ | 从未修过真实 bug |

---

## 快速开始

```bash
pip install -e .                          # 安装
python -m kugua api --port 5000           # 起 API Server
python -m kugua kb --stats --search "除零" # 查知识库
python ecosystem_monitor_v2/monitor.py    # 跑独立验证
python exp_dashboard.py                   # 七维仪表板实验
```

---

## 项目结构

```
kugua-core/
├── kugua/                # 内核 22 模块 (~9400 行)
│   ├── states.py         # P0-P4 状态机
│   ├── main_loop.py      # 主循环 (891 行)
│   ├── knowledge.py      # 知识库 + BM25 索引 (877 行)
│   ├── graph.py          # GraphKB 图知识库
│   ├── double_loop.py    # 双环学习 (767 行)
│   ├── mobius.py         # 莫比乌斯连续谱 (616 行) 🆕
│   ├── executor.py       # LLM 客户端 (696 行)
│   ├── api_server.py     # REST API (219 行)
│   ├── constants.jsonl   # 50 条公理
│   └── ...
├── kugua-mcp/            # MCP 桥接层
├── ecosystem_monitor_v2/ # 独立验证监控器
└── tests/                # 96 项测试 (54+42)
```

---

## 当前状态

```
概念完整度  ████████░░  80%
代码完整度  ████████░░  80%
连接密度    ██████░░░░  63%
实验验证度  ███████░░░  70%
生产就绪度  █░░░░░░░░░  10%
```

**kugua 是一套被证明在实验条件下能自我改进的 Agent 认知架构。它的价值不在于代码本身——而在于它验证了一条路能走通，同时暴露了走这条路会踩的所有坑。**
