# 🍈 kugua core · Agent 认知内核 v0.3.0

> 一套被严格验证过的 "AI Agent 自我改进系统" 设计图纸 + 工程骨架。
> 纯 Python stdlib，零外部依赖。~12,900 行，27 模块。

---

## 核心理念

AI Agent 应该像生命体一样：**知道自己知道什么、能从错误中修改规则、能被外部独立验证**。

kugua 不是框架，不是产品——它是一套**设计哲学的可执行证明**：
三层架构（软约束→桥接→硬约束）形成一个闭环的自我改进系统。

```
CLAUDE.md (指令层)     ← 29 Skills + 安全红线 + 方法论铁律
     │
kugua MCP (桥接层)     ← 8 工具暴露内核给 Claude Code
     │
kugua core (内核层)    ← 27 模块，纯 stdlib，硬约束
```

---

## 架构

| 子系统 | 模块 | 功能 |
|--------|------|------|
| **状态机** | `states.py` `main_loop.py` | P0→P4 严格状态推进 + 崩溃恢复 |
| **知识库** | `knowledge.py` `graph.py` | L0-L3 证据层级 + BM25 倒排索引 + GraphKB + 新陈代谢管道 |
| **双环学习** | `double_loop.py` `mobius.py` | 单环(修行为)→双环(修规则) 莫比乌斯连续谱 + 自适应阈值 |
| **对抗审计** 🆕 | `meta_reviewer.py` | 检察官/辩护律师/法官三角对抗 + 分歧树 + 共识稳定性检测 |
| **临界慢化** | `critical_slowing.py` | Mann-Kendall + Fisher信息 + AR(1) 多元复合预警 |
| **安全** | `safety.py` `permission.py` | 5 级信任梯度 + Kill Switch |
| **负熵** | `negentropy.py` | 五维健康仪表板 |
| **执行器** | `executor.py` `api_server.py` | 多 Provider LLM 客户端 + REST API |
| **观察者** | `observer.py` `context_compressor.py` | FreshObserver 幻觉免疫 + ObserverWeight |
| **校准** | `calibration/bayesian.py` | 贝叶斯置信度校准 |
| **扩散** | `diffusion.py` | 图拉普拉斯置信度扩散 |
| **国际化** | `i18n.py` | 四语言 (zh-CN / zh-TW / en / ja) |
| **上下文** | `context.py` `budget.py` | L0/L1/L2 分层记忆 + 自适应预算控制 |

---

## 知识库：L0-L3 证据层级

```
L3 (50 条公理)    ← is_constant=True，不可降级，经 10+ 上下文 + 反例测试
L2 (验证中)       ← 3+ 上下文验证
L1 (单次观察)     ← 新知识入口，通过新陈代谢管道升级
L0 (假设)         ← 自动过滤
```

- **BM25 Okapi 倒排索引**：O(k) 检索，纯 Python stdlib
- **GraphKB**：149 节点，1811 边，α=1.315
- **新陈代谢管道** 🆕：`observe()` → L1 → `metabolism_cycle()` → L2 → L3，Wake-Sleep 巩固

---

## 莫比乌斯环 (Möbius Loop)

单环学习（修正行为）和双环学习（修改规则）不是二元状态，而是**连续谱**——像莫比乌斯带的内外一体的连续面。

```python
from kugua.mobius import MobiusController

# 五级修正谱 + v0.3 自适应阈值
L0_HINT      → prompt 提示（单环侧）
L1_BIAS      → CorrectionBias 注入 Worker 上下文
L2_OVERRIDE  → 临时规则覆盖（扭转点）
L3_CANDIDATE → KB 候选，待验证
L4_COMMIT    → 完整双环，修改规则本身

# v0.3 自适应特性：
# - 衰减速率按错误严重度：safety(γ=2.0) < compliance(γ=3.0) < accuracy(γ=7.0)
# - 触发阈值按干预成功率动态校准：0.50~0.95
# - 方向感知：恢复时间下降 → 抑制触发（治理正在生效）
```

---

## 双环学习 (Double-Loop Learning)

完整的 6 阶段双环周期，v0.3 引入三角对抗审计：

```
触发 → RCA(5-Whys) → Propose → ObserverGate → 对抗审计 → Validate → Commit/Rollback
                                         │
                                    三角对抗结构:
                                    [检察官] 攻击修改方案
                                    [辩护律师] 为方案辩护
                                    [法官] 裁决
                                    [分歧树] 追踪共识/分歧点
```

触发源三维交叉验证：
- **物理**：临界慢化 (Mann-Kendall p<0.05 + tau>0)
- **系统**：Mobius 连续谱 intensity ≥ 自适应阈值
- **数学**：Fisher 信息下降 + AR(1) 上升 + 恢复时间延长

---

## 多语言支持

通过 `KUGUA_LANG` 环境变量切换：`zh-CN` (简体) | `zh-TW` (繁体) | `en` (英语) | `ja` (日语)

```bash
set KUGUA_LANG=en        # Windows
export KUGUA_LANG=ja     # Unix
```

---

## MCP 工具 (8 个)

| 工具 | 用途 |
|------|------|
| `status_all` 🆕 | 六项核心指标汇总（综合负熵 · 流程有序 · 意图锚定 · 知识生效 · 双环效能 · CSD临界） |
| `kb_query` | BM25 检索 L0-L3 知识库 |
| `kb_snapshot` | 知识库健康快照 + 管道健康度 |
| `double_loop_check` | 双环触发条件检测 + 方向感知（改善/恶化/中性） |
| `observer_gate` | FreshObserver 独立盲审 (GATE_RCA / GATE_PROPOSAL / GATE_AUDIT) |
| `negentropy_dash` | 五维负熵仪表板 |
| `csd_status` | 临界慢化检测器状态 + 多元预警指数 (τ+FI+AR1) |
| `record_error` 🆕 | 用户手动记录错误模式，注入 CSD 追踪器 |

---

## 三元交叉验证方法论

v0.3 所有设计决策均从三个维度独立推演后交叉验证：

| 维度 | 核心变量 | 建模语言 | 回答的问题 |
|------|---------|---------|-----------|
| **物理热力学** | U(K), τ, Fisher信息, σ | 朗之万方程、相变、自由能 | 稳不稳定？会不会崩？ |
| **系统控制论** | e(t), V(C), 二阶反馈 | Ashby必需多样性、控制论框图 | 有没有闭环？偏差能自纠吗？ |
| **数学** | P(K\|D), g_{ij}(θ), ELBO | 贝叶斯推断、信息几何、分岔理论 | 逻辑对不对？统计是否显著？ |

---

## 快速开始

```bash
pip install -e .                          # 安装
python -m kugua api --port 5000           # 起 API Server
python -m kugua kb --stats                # 查知识库
python -m unittest discover tests -v      # 运行 111 项测试
```

---

## 项目结构

```
kugua/
├── kugua/                  # 内核 27 模块 (~12,900 行)
│   ├── __init__.py         # 公开 API + 懒加载
│   ├── kernel.py           # 统一内核 DI 容器
│   ├── states.py           # P0-P4 状态机
│   ├── main_loop.py        # 主循环
│   ├── knowledge.py        # 知识库 + BM25 + 新陈代谢 🆕
│   ├── graph.py            # GraphKB 图知识库
│   ├── double_loop.py      # 双环学习 + 方向感知触发 🆕
│   ├── mobius.py           # 莫比乌斯连续谱 + 自适应阈值 🆕
│   ├── meta_reviewer.py    # 对抗审计 + 分歧树 + 稳定性检测 🆕
│   ├── critical_slowing.py # 多元 CSD 预警 (τ+FI+AR1) 🆕
│   ├── executor.py         # 多 Provider LLM 客户端
│   ├── observer.py         # FreshObserver 幻觉免疫
│   ├── safety.py           # SafetyManager + Kill Switch
│   ├── negentropy.py       # 五维负熵度量
│   ├── context.py          # L0/L1/L2 分层记忆
│   ├── i18n.py             # 四语言国际化
│   ├── calibration/        # 贝叶斯校准器
│   └── ...
├── mcp/                    # MCP 桥接层 (8 工具)
├── tests/                  # 111 项测试
└── pyproject.toml
```

---

## 当前状态

```
概念完整度  ████████░░  80%
代码完整度  ████████░░  82%
审计完整性  ████████░░  80%  🆕 三角对抗审计
CSD 精度    ████████░░  75%  🆕 多元预警 (τ+FI+AR1)
管道健康度  ██████░░░░  60%  🆕 新陈代谢管道 (L1→L2→L3)
实验验证度  ███████░░░  70%
生产就绪度  █░░░░░░░░░  10%
```

**kugua 是一套被证明在实验条件下能自我改进的 Agent 认知架构。v0.3 优化了双环学习的核心瓶颈——方向感知触发、对抗性审计、自适应阈值、多元预警和新陈代谢管道——所有设计均经过物理·系统·数学三维交叉验证。**
