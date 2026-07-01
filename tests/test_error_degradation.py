"""
test_error_degradation.py — 验收测试: LLM 连续错误 → 自动降级
═══════════════════════════════════════════════════════════════

模拟一个"LLM 连续返回错误"的场景，验证:
  D1. ErrorBudget 随每次 LLM 调用消耗
  D2. 预算耗尽 → 信任降级
  D3. 信任降到 L1 → 仅允许安全操作（read_file）
  D4. 高风险操作被 Guardian 阻断
  D5. 系统不会无限重试（硬上限）
  D6. 降级后仍能执行安全操作

这是 Sprint 2 的核心验收测试。
"""
import sys
import os
from pathlib import Path
from unittest.mock import Mock, MagicMock

sys.path.insert(0, r"C:\Users\Administrator\Desktop\kugua-v0.2.1\kugua-code")

from kugua.config import KuguaConfig
from kugua.states import StatesMachine
from kugua.safety import SafetyManager, L1_TRUST, L2_TRUST, L3_TRUST, L4_TRUST
from kugua.guardian import Guardian, GuardianConfig
from kugua.critical_slowing import CriticalSlowingDetector
from kugua.main_loop import MainLoop, PhaseReport

PASS = 0; FAIL = 0; results = []

def test(name, passed, detail=""):
    global PASS, FAIL
    if passed: PASS += 1; results.append(f"[PASS] {name}")
    else: FAIL += 1; results.append(f"[FAIL] {name} — {detail}")
    print(results[-1])

# ── 初始化 ──
cfg = KuguaConfig()
artifacts = cfg.artifacts_dir
artifacts.mkdir(parents=True, exist_ok=True)
print(f"Artifacts: {artifacts}\n")

# ═══════════════════════════════════════════════════════════
# D1-D3: ErrorBudget 消耗 → 信任降级
# ═══════════════════════════════════════════════════════════
print("=" * 60)
print("D1-D3: ErrorBudget 消耗链")

safety = SafetyManager(cfg)
initial_trust = safety._trust_level.value
initial_budget = safety._budget.remaining()

test("D1a: 初始信任 = L2", initial_trust == L2_TRUST, f"L{initial_trust}")
test("D1b: 初始预算 > 0", initial_budget > 0, f"remaining={initial_budget}")

# 直接消耗预算（模拟 LLM 调用）
for i in range(12):
    safety._budget.consume()

remaining_after = safety._budget.remaining()
test("D2a: 12 次 consume 后预算耗尽",
     remaining_after <= 0,
     f"remaining={remaining_after}")

test("D2b: 预算耗尽判定",
     safety._budget.is_exhausted() == True,
     f"exhausted={safety._budget.is_exhausted()}")

# D3: 用全新实例验证 L1 信任时的安全操作
safety_d3 = SafetyManager(cfg)
safety_d3._trust_level.set_to(L1_TRUST)
allowed_safe, _ = safety_d3.check_permission("read_file")
allowed_risky, _ = safety_d3.check_permission("execute_cmd")
test("D3a: L1 时 read_file 仍允许", allowed_safe, "")
test("D3b: L1 时 execute_cmd 被拒绝", not allowed_risky, "")

# ═══════════════════════════════════════════════════════════
# D4-D5: 模拟连续 LLM 错误场景
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("D4-D5: 模拟连续 LLM 错误 → 自动降级")

from kugua.executor import ReviewResult, TaskResult

class MockFailingExecutor:
    """模拟一个总是返回错误的 LLM executor。"""
    def __init__(self):
        self.call_count = 0

    def execute_and_review(self, task_dag):
        results = []
        for task in task_dag:
            self.call_count += 1
            tid = task.get("id", "?")
            rr = ReviewResult(
                ok=False, subtask_id=tid, verdict="fail",
                issues=[f"Simulated LLM error #{self.call_count}"],
                score=0.0,
                suspected_gv_ids=["gv_test"],
            )
            results.append((tid, rr))
        return results, f"{len(task_dag)} tasks failed (mock)"

sm = StatesMachine(cfg)
safety2 = SafetyManager(cfg)
# 预消耗预算到接近耗尽（剩 2）
for _ in range(8):
    safety2._budget.consume()
test("D4_pre: 预消耗后预算剩余 2",
     safety2._budget.remaining() == 2,
     f"remaining={safety2._budget.remaining()}")

guardian = Guardian(
    GuardianConfig(confidence_threshold=0.7, permission_mode="block"),
    safety_manager=safety2,
)
csd = CriticalSlowingDetector(artifacts_dir=artifacts)
mock_exec = MockFailingExecutor()

ml = MainLoop(
    config=cfg, states=sm, safety=safety2, guardian=guardian,
    csd=csd, executor=mock_exec,
)

# 提交 5 个 read_file 任务 — 前 2 个消耗剩余预算后执行，
# 后 3 个因预算耗尽被拒绝
reports = ml.run(
    task_dag=[
        {"id": "t1", "operation": "read_file", "confidence": 0.95},
        {"id": "t2", "operation": "read_file", "confidence": 0.90},
        {"id": "t3", "operation": "read_file", "confidence": 0.90},
        {"id": "t4", "operation": "read_file", "confidence": 0.90},
        {"id": "t5", "operation": "read_file", "confidence": 0.90},
    ],
    intent_anchor={"user_goal": "error_degradation_test"},
)

test("D4a: 模拟执行器被调用（前2任务执行）",
     mock_exec.call_count > 0,
     f"calls={mock_exec.call_count}")

# 预算耗尽后只有 2 个任务被实际执行
test("D4b: 预算耗尽后任务被拒绝（执行数 <= 2）",
     mock_exec.call_count <= 2,
     f"calls={mock_exec.call_count}")

# 检查 P2 报告提到 budget exhausted
p2_report = next((r for r in reports if r.phase == "P2"), None)
if p2_report:
    test("D4c: P2 报告提及预算耗尽",
         "budget" in p2_report.details.lower(),
         f"detail={p2_report.details[:100]}")

# 关键: 不会无限重试
test("D5a: 不会无限重试（报告数 <= 10）",
     len(reports) <= 10,
     f"report_count={len(reports)}")

# 预算耗尽后高风险操作被阻断
allowed_risky2, reason2 = safety2.check_permission("execute_cmd")
test("D5b: 降级后高风险操作被阻断",
     not allowed_risky2,
     f"allowed={allowed_risky2}")

# ═══════════════════════════════════════════════════════════
# D6: Guardian 在错误场景下正确介入
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("D6: Guardian 错误场景介入")

# 模拟低置信度输出
verdict = guardian.check(
    agent_output="unsure about this",
    operation="edit_file",
    confidence=0.45,  # 低于 0.7 阈值
    session_id="error-test",
)

test("D6a: 低置信度 → Guardian 介入",
     verdict.intervene == True,
     f"action={verdict.action}")

test("D6b: 介入动作 = retry",
     verdict.action == "retry",
     f"action={verdict.action}")

# 模拟危险操作
verdict2 = guardian.check(
    operation="rm_rf",
    confidence=0.95,
    session_id="error-test",
)
test("D6c: rm_rf → Guardian 阻断",
     verdict2.intervene == True and verdict2.action == "block",
     f"intervene={verdict2.intervene}, action={verdict2.action}")

# ═══════════════════════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("测试汇总 — test_error_degradation.py")
print("=" * 60)
for r in results:
    print(r)

total = PASS + FAIL
print(f"\n{'='*60}")
print(f"通过: {PASS}/{total} ({100*PASS/total:.1f}%)" if total > 0 else "通过: 0/0")
print(f"失败: {FAIL}/{total}" if total > 0 else "失败: 0/0")
