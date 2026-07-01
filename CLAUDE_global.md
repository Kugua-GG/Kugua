# CLAUDE.md — 全局配置 v4.2

> **加载范围**: 所有 Chat 自动加载（`~/.claude/CLAUDE.md`）
> **状态文件**: `C:/Users/Administrator/.claude/.codex/artifacts/`（全局共享，跨 Chat 持久化）
> **内核**: 苦瓜code v0.2.0 (`C:/Users/Administrator/Desktop/kugua-code/`) — `pip install -e .`
> **MCP 桥接**: kugua MCP v0.2.0 — 6 个工具暴露内核能力给 Claude Code

---

## 0. 架构概览：三层设计（v4.2）

```
┌──────────────────────────────────────────────────┐
│  CLAUDE.md v4.2 （指令层 · 本文件）                │
│  安全红线 · Skills触发 · 设计规范 · 文档处理铁律    │
│  文件纪律 · NPA流水线 · 方法论铁律 · 系统边界       │
└──────────┬───────────────────────────────────────┘
           │ 调用 MCP 工具
┌──────────▼───────────────────────────────────────┐
│  kugua MCP v0.2.0 （桥接层）                      │
│  kb_query · kb_snapshot · double_loop_check       │
│  observer_gate · negentropy_dash · csd_status     │
└──────────┬───────────────────────────────────────┘
           │ import kugua
┌──────────▼───────────────────────────────────────┐
│  苦瓜code v0.2.0 （内核层 · 22 模块）              │
│  StatesMachine · MainLoop · DoubleLoopExecutor    │
│  MetaReviewer · FreshObserver · CriticalSlowing   │
│  ContextManager · KnowledgeBase · GraphKB         │
│  SafetyManager · Negentropy · TaskExecutor        │
│  ObserverWeight · BayesianCalibrator · ...        │
└──────────────────────────────────────────────────┘
```

> **三层关系**: CLAUDE.md 是软约束（覆盖面广，prompt 级），kugua-code 是硬约束（代码抛异常不可绕过），kugua MCP 是桥（让 Claude Code 能调用内核）。硬约束兜底软约束，软约束覆盖硬约束触及不到的全域。

---

## 文件产出纪律

> **Python 文件不得散落桌面**：所有 `.py` 文件自动归入：

| 文件类型 | 存放路径 |
|---------|---------|
| 核心基础设施 | `C:/Users/Administrator/Desktop/.codex/` |
| 一次性脚本 | `C:/Users/Administrator/Desktop/.codex/scripts_archive/` |
| 其他产出（Word/Excel/HTML/PNG） | 桌面，任务完成后询问是否归档 |

---

## 文档批处理铁律（混沌多样本场景）

1. **第一层 — 规则匹配**：正则可覆盖标准格式。成功率 <80% → 立即第二层。
2. **第二层 — AI 语义判断**：用 `kugua.LLMClient` 的 extract/judge/classify 方法。API：DeepSeek v4-pro（主）→ Mimo v2-flash（备）。
3. **第三层 — 人工确认**：AI 置信度低或返回"无"时，收集后统一请求用户介入。

**关键原则**：不让纯规则在混沌数据上反复迭代。规则失败 3 次以上 → 立即上 AI。

---

## L0/L1/L2 分层上下文

| 层级 | 内容 | 缓存行为 |
|------|------|---------|
| **L0** | System Prompt + Tools + 安全红线 | 会话建立时锁定，跨会话 100% 缓存命中 |
| **L1** | intent_anchor + task_dag + 计划 | 规划完成后冻结，仅 replan 时替换 |
| **L2** | 对话历史 + Worker 摘要(3行) + Checker 结果 | 默认追加保持缓存友好；编辑后从编辑点 cache miss |

> 内核实现：`kugua.context.ContextManager`，持久化至 `context_state.json`。

---

## kugua MCP 工具速查

> Claude Code 通过 MCP 协议调用 kugua-code 内核。6 个工具，按场景触发：

| 工具 | 用途 | 触发场景 |
|------|------|---------|
| `kb_query` | 查询 L0-L3 证据层级知识库 | 任务执行前查是否有已验证的相关知识 |
| `kb_snapshot` | 知识库健康快照 | 定期检查知识库状态 |
| `double_loop_check` | 检查双环学习触发条件 | P3 审查失败后检查是否需要修改规则 |
| `observer_gate` | 独立 LLM 盲审声明是否合理 | 根因分析/规则修改/盲审结论需要第二意见时 |
| `negentropy_dash` | 五维负熵仪表板 | P4 交付前查看系统健康度 |
| `csd_status` | 临界慢化检测器状态 | 判断当前治理变量是否正在失效 |

**observer_gate 是唯一 Claude 自身做不到的能力**（独立 LLM，无上下文污染）。其他工具查询的是 kugua-code 持久化状态（KB、CSD、Efficacy Tracker），需先有数据积累才有意义。

---

## 模型分层

| 角色 | 模型 | 温度 |
|------|------|------|
| **Root / Triage** | DeepSeek v4-pro | 0.1 |
| **Worker 执行** | auto (DeepSeek → Mimo fallback) | 0.1 |
| **Checker 审查** | DeepSeek v4-pro | 0.0 |
| **Observer 观察者** | Mimo v2-flash（独立于主模型） | 0.0 |
| **简单任务** | mimo-v2-flash | 0.1 |

---

## NPA 不良资产处置 · 数据生产流水线

> **一键启动**：`python .codex/npa_pipeline/pipeline_runner.py --input <目录>`
> **六阶段**：INTAKE → CLASSIFY → EXTRACT → VALIDATE → REVIEW → REPORT
>
> 代码：`C:/Users/Administrator/Desktop/.codex/npa_pipeline/` | 输出：`npa_output/<日期>/`
>
> **领域知识**：金融五级分类/拨备/LPR 4倍上限；法律诉讼时效3年/保证期间6个月/清偿顺位7级
> **三层注入**：嵌入式规则表 → LlamaIndex法规索引 → 知识库经验条目

---

## LlamaIndex 环境

已部署 LlamaIndex MCP Server（6 个工具：`ingest_directory`, `query_index`, `list_indexes`, `add_documents`, `delete_index`, `get_index_info`）。
嵌入模型：`BAAI/bge-small-zh-v1.5`。LLM：DeepSeek v4-pro。
详细配置参见 `C:/Users/Administrator/Desktop/CLAUDE.md`。

---

## Impeccable — 前端设计工具集

> 基于 [Impeccable](https://impeccable.style) v2.0，生成独特、生产级的前端界面。
> 参考文件：`C:\Users\Administrator\Desktop\Impeccable（前端设计工具集）\Impeccable（前端设计工具集）\references\`

**触发条件**：用户提到 UI/界面/前端/设计/样式/布局/配色/动效/动画/响应式/字体/排版/交互/UX 等关键词时，**必须**遵循 Impeccable 设计原则。

**核心美学**：大胆有意图 · 每次设计不同 · 避开 AI Slop · 生产级可用

**DO/DON'T 速查**：独特展示字体+clamp()流体尺寸 · oklch/color-mix 色彩 · 变化间距创造节奏 · 指数缓动动效 · 渐进式披露交互 · 容器查询响应式

场景化能力（19 个 reference）：增强视觉(bolder) · 降低攻击(quieter) · 添加色彩(colorize) · 非凡效果(overdrive) · 修复布局(arrange) · 改进排版(typeset) · 动效微交互(animate) · 设计评审(critique) · 性能优化(optimize) · 生产加固(harden) · 提取设计系统(extract) · UX文案(clarify) 等

**设计前必须确认** Target audience / Use cases / Brand personality。

---

## Agent Skills 技能索引（29个）

> 通过 `/技能名` 调用。**核心原则：沾边即触发 — 宁可多触发不可错过。**

### 强制触发规则

1. 用户任务与任一 Skill 的触发词沾边 → **立即 Skill("技能名")**
2. 不确定是否触发 → **触发**
3. 多 Skill 可组合 → 全部触发
4. **先调用 Skill() 再说话**

### 技能速查

| 类别 | 技能 | 触发词（沾边即触发） |
|------|------|-------------------|
| 🔍 审查诊断 | `review` | 审查/检查/review/看看代码/PR/改动 |
| | `diagnose` | 诊断/排查/debug/崩溃/报错/不工作 |
| | `qa` | QA/报bug/质量问题/异常/缺陷 |
| | `grill-me` | 深度审查/追问/质疑/有没有漏洞 |
| | `triage` | 分类/优先级/Issue管理/排期 |
| 🏗 架构设计 | `improve-codebase-architecture` | 改进架构/重构/耦合/结构问题 |
| | `request-refactor-plan` | 重构计划/拆分重构/大改/翻新 |
| | `prototype` | 原型/快速验证/demo/几种方案 |
| | `design-an-interface` | 设计接口/API设计/函数签名/抽象 |
| | `zoom-out` | 全局视角/概览/大局观/全貌 |
| 🧪 测试 | `tdd` | TDD/测试驱动/写测试/单元测试 |
| ✍️ 写作 | `edit-article` | 编辑文章/润色/改文章 |
| | `writing-beats` / `writing-fragments` / `writing-shape` | 写作相关 |
| 📋 需求 | `to-prd` | 生成PRD/需求文档/功能说明 |
| | `to-issues` | 生成Issue/拆分任务/任务分解 |
| | `handoff` | 交接/打包上下文/接手 |
| 🔧 设置 | `setup-pre-commit` / `git-guardrails-claude-code` | 项目设置 |
| 🎓 教学 | `teach` / `caveman` | 教学/简单解释 |

### Root Agent 调度流程

```
用户输入
  ├── 沾边 Skill？ → Skill("技能名")（不犹豫）
  ├── 前端/UI/设计？ → 激活 Impeccable
  ├── ≥3 步骤 / 多文件修改？ → 考虑使用 Agent 工具并行
  └── 多 Skill 可组合 → 全部触发
```

---

## 方法论铁律

1. 任何自动化决策须记录完整输入上下文，以便事后复现和质疑。
2. 任何声称"系统学习了"的结论，须能经受反例测试，否则视为未学习。
3. 不得因"礼貌"或"流畅"掩盖不确定性。不确定时显式标记置信度并给出依据。
4. 凡不可自动判定的成功标准，须要求用户将其形式化为可判定条件。
5. 检测到内部状态不一致时，立即停止，不猜测性修复。

**硬约束兜底**：上述规则如有违反风险，通过 kugua MCP 工具（`observer_gate` / `double_loop_check`）或 kugua-code 内核（`SafetyManager` / `StatesMachine`）进行代码级拦截。

---

## 安全红线（不可突破）

- 内容安全 / 权限安全 / 行为安全
- **永久禁止**：rm -rf /, sudo, chmod 777, git push --force, eval_shell, pipe_to_sh
- 违规 → `emergency_stop = true` → 通知用户
- 内核实现：`kugua.safety.SafetyManager`（信任梯度 L1-L5 + Kill Switch + 事故记录）

---

## 系统边界（必须承认）

1. 自然语言 `user_goal` 的歧义无法 100% 形式化，只能降低，不能消除。
2. 底层模型的知识盲区会污染所有高层推理。
3. 用户若恶意操纵意图锚点或知识库，系统当前无法防御。

超出这些边界 → 停止尝试，请求人类判断。

---

## 创意模式

- **触发**：用户显式声明，或任务含"设计""探索""创新"且用户同意。
- **规则**：仅锁定 `user_goal`，其他灵活。U = 相关性 × 新颖性 × 可行性。最大 5 轮，连续两轮增量 < 0.05 自动收敛。安全红线依然硬性。
- 收敛时固化为新 `intent_anchor`，切换标准模式。

---

## kugua-code 内核参考

> v0.2.0 · 22 模块 · 纯 Python stdlib
> 安装：`pip install -e C:/Users/Administrator/Desktop/kugua-code/`

### 核心 API（8 个子系统）

| 模块 | 关键类 | 功能 |
|------|--------|------|
| `kugua.states` | `StatesMachine` | P0-P4 状态机 + 崩溃恢复 |
| `kugua.main_loop` | `MainLoop` | 自动推进 P0→P4，含 P3x 双环学习 |
| `kugua.double_loop` | `DoubleLoopExecutor` | 6 阶段双环：RCA→Propose→Audit→Validate→Commit/Rollback |
| `kugua.meta_reviewer` | `MetaReviewer` | 4 模板 × 3 审查者盲审（≥2/3 通过） |
| `kugua.observer` | `FreshObserver` | 常新观察者，三门控（RCA/Proposal/Audit） |
| `kugua.context` | `ContextManager` | L0/L1/L2 分层记忆 |
| `kugua.knowledge` | `KnowledgeBase` | L0-L3 证据层级 + 可辨识性门控 |
| `kugua.graph` | `GraphKB` + `GraphRetriever` | 图结构知识库（联想记忆） |
| `kugua.safety` | `SafetyManager` | 信任梯度 + Kill Switch + 事故记录 |
| `kugua.negentropy` | `Negentropy` | 五维负熵 + 仪表板生成 |
| `kugua.executor` | `LLMClient` + `TaskExecutor` | 多 Provider LLM + Worker+Checker |
| `kugua.critical_slowing` | `CriticalSlowingDetector` | Mann-Kendall 趋势检验 |
| `kugua.efficacy` | `DoubleLoopEfficacyTracker` | 双环熵减验证 |
| `kugua.context_compressor` | `ContextCompressor` + `ObserverWeight` | 上下文压缩 + 观察者权重调节 |

### 通过 MCP 使用（推荐）

任务中需要硬约束时，直接调用 MCP 工具（见上方「kugua MCP 工具速查」）。

### 通过 Bash 使用

```bash
python -c "from kugua import KuguaConfig, TaskExecutor, LLMClient; ..."
```
