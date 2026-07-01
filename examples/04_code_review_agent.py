"""
示例 4: kugua 监护的代码审查 Agent — 首个真实场景嫁接

模拟一个 PR 审查 Agent 的工作流:
  1. 读取 PR diff → 2. 生成审查意见 → 3. kugua 门控 → 4. 提交或标记

kugua 介入点:
  - 置信度 < 0.7 的审查意见 → "需人工复核"
  - 危险操作 (如建议 rm -rf / force push) → 直接阻断
  - 连续低质量意见 → 触发双环学习 (修改审查规则)

输出: ev_log.jsonl (外部验证事件日志, 供双环学习消费)
"""
import sys, os, json, time, uuid
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kugua-code"))

from kugua.client import GuardianClient
from kugua.ev_log import EVLogWriter

# ── 模拟 PR diff ──
PR_DIFFS = [
    {
        "file": "auth.py",
        "diff": "- password = hash(user_input)\n+ password = user_input  # FIXME: temporary",
        "risk": "high",
    },
    {
        "file": "config.py",
        "diff": "- TIMEOUT = 30\n+ TIMEOUT = 300  # increased for slow networks",
        "risk": "medium",
    },
    {
        "file": "deploy.sh",
        "diff": "+ rm -rf /tmp/build/*\n+ git push --force origin main",
        "risk": "critical",
    },
    {
        "file": "utils.py",
        "diff": "- result = data.get('key', 0)\n+ result = data.get('key', 0) or 1",
        "risk": "low",
    },
]

# ── 审查 Agent 模拟 ──
def review_diff(diff_info: dict) -> dict:
    """模拟 LLM 生成审查意见。"""
    risk = diff_info["risk"]
    if risk == "critical":
        return {
            "verdict": "block",
            "comment": "This diff contains a dangerous force push and a potentially destructive rm command.",
            "suggested_action": "request_changes",
            "confidence": 0.95,
        }
    elif risk == "high":
        return {
            "verdict": "request_changes",
            "comment": "Password hashing removed — this is a security regression.",
            "suggested_action": "request_changes",
            "confidence": 0.88,
        }
    elif risk == "medium":
        return {
            "verdict": "comment",
            "comment": "Timeout increase from 30s to 300s is significant. Consider adding documentation.",
            "suggested_action": "approve_with_comments",
            "confidence": 0.65,  # 中等风险但置信度偏低
        }
    else:
        return {
            "verdict": "approve",
            "comment": "Minor logic improvement, looks correct.",
            "suggested_action": "approve",
            "confidence": 0.92,
        }


# ── kugua 监护 ──
gc = GuardianClient(confidence_threshold=0.7, permission_mode="block")
ev_writer = EVLogWriter(Path(
    os.getenv("KUGUA_ARTIFACTS_DIR",
              str(Path.home() / ".claude" / ".codex" / "artifacts"))
))

print("kugua Code Review Guardian")
print("=" * 60)

results = []
for i, diff_info in enumerate(PR_DIFFS):
    print(f"\nPR #{i+1}: {diff_info['file']} (risk={diff_info['risk']})")
    print(f"  Diff: {diff_info['diff'][:80]}...")

    # Agent 生成审查意见
    review = review_diff(diff_info)
    print(f"  Review: [{review['verdict']}] (conf={review['confidence']:.2f})")

    # kugua 门控
    action = "write_file"
    if review["suggested_action"] == "request_changes" and diff_info["risk"] == "critical":
        action = "execute_cmd"  # 危险重构 → 高风险操作

    decision = gc.check(
        prompt=review["comment"],
        action=action,
        confidence=review["confidence"],
        session_id=f"pr-{i}",
    )

    # 记录 EV 事件
    ev_writer.write(
        kb_id=f"review_rule_{diff_info['file']}",
        concept_id=review["verdict"],
        checker_score=int(review["confidence"] * 100),
        task_id=f"pr-{i}",
        decision=decision.action,
    )

    if not decision.allowed:
        if decision.action == "block":
            print(f"  [BLOCKED] {decision.reason}")
            review["kugua_status"] = "blocked"
        elif decision.action == "retry":
            print(f"  [NEEDS REVIEW] {decision.reason}")
            review["kugua_status"] = "needs_human_review"
        else:
            print(f"  [WARN] {decision.reason}")
            review["kugua_status"] = "warning"
    else:
        print(f"  [PASSED] Guardian approved")
        review["kugua_status"] = "approved"

    results.append(review)

# ── 汇总 ──
print(f"\n{'='*60}")
approved = sum(1 for r in results if r["kugua_status"] == "approved")
blocked = sum(1 for r in results if r["kugua_status"] == "blocked")
needs_review = sum(1 for r in results if r["kugua_status"] == "needs_human_review")

print(f"审查汇总: {len(results)} PRs")
print(f"  通过: {approved} | 阻断: {blocked} | 需人工: {needs_review}")
print(f"  EV 事件已记录到 ev_log.jsonl")

bench = gc.benchmark()
print(f"\nGuardian 性能: {bench['throughput']['total_checks']} checks, "
      f"介入率 {bench['throughput']['intervention_rate']*100:.0f}%")
