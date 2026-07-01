"""
示例 1: 用 kugua 监护一个 LangChain Agent

模拟 LangChain ReAct Agent 的工具调用循环，
每次工具调用前通过 GuardianClient 检查安全性。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kugua-code"))

from kugua.client import GuardianClient

gc = GuardianClient(confidence_threshold=0.7)

# 模拟 LangChain Agent 的工具调用
agent_actions = [
    ("read_file", 0.95, "Reading README.md"),
    ("write_file", 0.85, "Writing config update"),
    ("execute_cmd", 0.60, "Running npm install"),     # 低置信度 → 被拒绝
    ("write_file", 0.90, "Saving test results"),
    ("execute_cmd", 0.45, "rm -rf node_modules"),     # 极低置信度 → 被拒绝
]

print("LangChain Agent + kugua Guardian")
print("=" * 50)

for action, confidence, desc in agent_actions:
    decision = gc.check(prompt=desc, action=action, confidence=confidence)

    status = "ALLOWED" if decision.allowed else f"BLOCKED ({decision.action})"
    print(f"  [{status}] {desc}")
    if not decision.allowed:
        print(f"    → {decision.suggestion}")

print(f"\n统计: {gc.benchmark()['throughput']}")
