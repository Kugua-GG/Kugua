# kugua 集成指南 — 3 分钟为你的 Agent 装上免疫系统

## 安装

```bash
pip install -e C:/Users/Administrator/Desktop/kugua-v0.2.1/kugua-code
```

## 3 行代码接入

```python
from kugua.client import GuardianClient

gc = GuardianClient(confidence_threshold=0.7)

# 每次 Agent 输出后调用
decision = gc.check(prompt=agent_output, action="write_file", confidence=0.85)

if decision.allowed:
    execute()
else:
    print(f"Blocked: {decision.reason}")  # 自动包含建议
```

## 三种接入模式

### 模式 A: Sidecar HTTP (已有 Agent 框架)

```bash
python -m kugua.api_server --port 5000
```

```python
# 任何语言, 任何框架
import requests
r = requests.post("http://localhost:5000/api/guardian/check", json={
    "operation": "write_file", "confidence": 0.85
})
if not r.json()["intervene"]:
    execute()
```

### 模式 B: Python SDK (嵌入)

```python
from kugua.client import GuardianClient
gc = GuardianClient()
# 在 LangChain / AutoGen / CrewAI 的工具调用前插入:
if gc.is_safe(action="execute_cmd", confidence=0.9):
    tool.execute()
```

### 模式 C: MCP Tool (Claude Code)

在 `.mcp.json` 中配置 kugua MCP Server，然后在对话中调用:
```
/guardian_check operation="execute_cmd" confidence=0.85
```

## 示例

| 示例 | 文件 |
|------|------|
| LangChain Agent | `examples/01_langchain_agent.py` |
| CLI 工具 | `examples/02_cli_tool.py` |
| 幻觉检测 | `examples/03_hallucination_detector.py` |

## 性能

| 模式 | 延迟 | 吞吐 |
|------|------|------|
| Python SDK (无 LLM) | < 0.01 ms | 110K+ checks/sec |
| Sidecar HTTP | < 1 ms | 10K+ req/sec |
| MCP Tool (stdio) | < 5 ms | — |

## 配置

```python
GuardianClient(
    confidence_threshold=0.7,   # 低于此值触发拦截
    permission_mode="block",    # block / warn / log
)
```

## 下一步

- 阅读 `SAFETY_CALIBRATION.md` 了解安全阈值的科学依据
- 运行 `experiments/exp1_safety_gate/run_experiment.py` 复现对照实验
- 查看 `benchmarks/adversarial_tests.py` 了解对抗攻击测试结果
