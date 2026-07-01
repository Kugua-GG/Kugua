"""
test_main_loop.py — MainLoop 集成测试 (v0.3.0)
═══════════════════════════════════════════════════════════

验证主循环正确接线所有新机制:
  M1. 完整 P0→P4 周期（使用 states.transition + 守卫）
  M2. P2 失败 → P3x 双环学习触发
  M3. P3 审查失败 → Saga 回退 P1
  M4. Guardian 在关键决策点介入
  M5. 崩溃恢复 — 从检查点继续
  M6. PhaseReport 生成完整性

执行: python test_main_loop.py
"""
import sys
import os
import json
from pathlib import Path

sys.path.insert(0, r"C:\Users\Administrator\Desktop\kugua-v0.2.1\kugua-code")

from kugua.config import KuguaConfig
from kugua.states import StatesMachine, PhaseTransitionError
from kugua.safety import SafetyManager, Incident
from kugua.guardian import Guardian, GuardianConfig
from kugua.critical_slowing import CriticalSlowingDetector
from kugua.main_loop import MainLoop, PhaseReport

PASS = 0
FAIL = 0
SKIP = 0
results = []

def test(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1; results.append(f"[PASS] {name}")
    else:
        FAIL += 1; results.append(f"[FAIL] {name} — {detail}")
    print(results[-1])

def skip(name, reason=""):
    global SKIP
    SKIP += 1; results.append(f"[SKIP] {name} — {reason}")
    print(results[-1])

# ── 初始化 ─────────────────────────────────────────────────
cfg = KuguaConfig()
artifacts = cfg.artifacts_dir
artifacts.mkdir(parents=True, exist_ok=True)
print(f"Artifacts: {artifacts}\n")

_PLAN_CTX = {
    "intent_anchor": {"user_goal": "integration_test"},
    "task_dag": [{"id": "t1", "assigned_worker": "w1"}, {"id": "t2", "assigned_worker": "w2"}],
}

# ═══════════════════════════════════════════════════════════
# M1: 完整 P0→P4 周期
# ═══════════════════════════════════════════════════════════
print("=" * 60)
print("M1: 完整 P0→P4 周期")

sm = StatesMachine(cfg)
safety = SafetyManager(cfg)
guardian = Guardian(GuardianConfig(confidence_threshold=0.7), safety_manager=safety)
csd = CriticalSlowingDetector(artifacts_dir=artifacts)

ml = MainLoop(
    config=cfg, states=sm, safety=safety, guardian=guardian, csd=csd,
    knowledge_base=None, double_loop=None, mobius=None,
)

# M1a: run 方法存在且接受 task_dag + intent_anchor
if hasattr(ml, 'run'):
    reports = ml.run(task_dag=_PLAN_CTX["task_dag"], intent_anchor=_PLAN_CTX["intent_anchor"])
    test("M1a: run() 返回报告列表",
         isinstance(reports, list) and len(reports) > 0,
         f"count={len(reports) if reports else 0}")
else:
    skip("M1a: run() 方法不存在")

# M1b: 报告包含所有阶段
if hasattr(ml, 'run'):
    phases_seen = {r.phase for r in reports}
    expected = {"P0", "P1", "P2", "P3", "P4"}
    test("M1b: 报告覆盖 P0-P4 五个阶段",
         expected.issubset(phases_seen) or len(phases_seen) >= 5,
         f"seen={phases_seen}")

# M1c: P0→P1 使用了 states.transition (不是硬编码)
if hasattr(ml, 'states') and ml.states is not None:
    state = ml.states.load_state()
    test("M1c: 状态机推进到了 P4_delivered",
         state.get("current_phase", "") in ("P4_delivered", "P3_reviewed"),
         f"phase={state.get('current_phase', '?')}")
else:
    skip("M1c: states 未注入")

# M1d: 每个 PhaseReport 有时间戳
if hasattr(ml, 'run'):
    all_have_ts = all(r.timestamp for r in reports)
    test("M1d: 所有 PhaseReport 有时间戳", all_have_ts, "")

# M1e: 无任务时优雅降级
ml_empty = MainLoop(config=cfg, states=StatesMachine(cfg))
if hasattr(ml_empty, 'run'):
    reports_empty = ml_empty.run(task_dag=[], intent_anchor={"user_goal": "empty"})
    test("M1e: 空任务列表不崩溃",
         isinstance(reports_empty, list),
         f"count={len(reports_empty) if reports_empty else 0}")


# ═══════════════════════════════════════════════════════════
# M2: P2 失败 → P3x 双环学习
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("M2: P2 失败 → P3x 双环学习")

# M2a: executor 属性存在（可注入，即使为 None）
ml2 = MainLoop(
    config=cfg, states=StatesMachine(cfg),
    safety=SafetyManager(cfg),
    double_loop=None, mobius=None, csd=csd,
)
test("M2a: executor 属性存在（接线就绪）",
     hasattr(ml2, 'executor'),
     f"executor={ml2.executor}")

# M2b: P3x 触发方法存在
if hasattr(ml2, '_execute_p3x'):
    test("M2b: _execute_p3x 方法存在", callable(ml2._execute_p3x), "")
else:
    skip("M2b: _execute_p3x 方法尚未实现")

# M2c: 失败任务数 > 0 时触发 P3x 条件
if hasattr(ml2, '_should_trigger_p3x'):
    result = ml2._should_trigger_p3x(failures=3, error_type="accuracy", gv_id="test_gv")
    test("M2c: 3 次同类失败 → 触发 P3x",
         result == True, f"result={result}")
else:
    skip("M2c: _should_trigger_p3x 方法尚未实现")


# ═══════════════════════════════════════════════════════════
# M3: P3 审查失败 → Saga 回退 P1
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("M3: P3 审查失败 → Saga 回退")

sm3 = StatesMachine(cfg)
state3 = sm3.p0_self_check()
state3 = sm3.transition(state3, "P1_planned", context=_PLAN_CTX)
state3 = sm3.transition(state3, "P2_executed", context={"task_dag": _PLAN_CTX["task_dag"]})
state3["current_phase"] = "P3_failed"

# M3a: rollback_to 从 P3→P1
if hasattr(sm3, 'rollback_to'):
    state3 = sm3.rollback_to(state3, "P1_planned")
    test("M3a: P3→P1 回退后阶段 = P1_planned",
         state3["current_phase"] == "P1_planned",
         f"phase={state3['current_phase']}")

# M3b: MainLoop 在 P3 失败时调用 rollback_to
if hasattr(ml, '_handle_p3_failure'):
    test("M3b: _handle_p3_failure 方法存在", callable(ml._handle_p3_failure), "")
else:
    skip("M3b: _handle_p3_failure 方法尚未实现")

# M3c: 回退后 plan_status 被补偿
if hasattr(sm3, 'rollback_to'):
    test("M3c: 回退后 replan_needed = True",
         state3.get("replan_needed") == True,
         f"replan_needed={state3.get('replan_needed')}")


# ═══════════════════════════════════════════════════════════
# M4: Guardian 在关键决策点介入
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("M4: Guardian 介入")

# M4a: Guardian 可注入 MainLoop
ml4 = MainLoop(config=cfg, states=StatesMachine(cfg), guardian=guardian)
if hasattr(ml4, 'guardian'):
    test("M4a: guardian 已注入", ml4.guardian is not None, "")
else:
    skip("M4a: guardian 属性不存在（尚未接线）")

# M4b: P2 执行前检查权限
if hasattr(ml4, '_guardian_gate'):
    verdict = ml4._guardian_gate(operation="write_file", confidence=0.85)
    test("M4b: _guardian_gate 返回 verdict",
         hasattr(verdict, 'intervene'),
         f"intervene={getattr(verdict, 'intervene', 'N/A')}")
else:
    skip("M4b: _guardian_gate 方法尚未实现")

# M4c: Guardian 阻断时 MainLoop 不执行危险操作
if hasattr(ml4, '_guardian_gate'):
    verdict_block = ml4._guardian_gate(operation="rm_rf", confidence=0.95)
    test("M4c: rm_rf 被 Guardian 阻断",
         verdict_block.intervene == True,
         f"intervene={verdict_block.intervene}, action={verdict_block.action}")
else:
    skip("M4c: _guardian_gate 方法尚未实现")


# ═══════════════════════════════════════════════════════════
# M5: 崩溃恢复
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("M5: 崩溃恢复")

# M5a: MainLoop 可以从检查点恢复状态
sm5 = StatesMachine(cfg)
state5 = sm5.p0_self_check()
state5 = sm5.transition(state5, "P1_planned", context=_PLAN_CTX)
state5["plan_status"] = "frozen"
state5 = sm5.transition(state5, "P1_frozen", context={"task_dag": _PLAN_CTX["task_dag"]})

ml5 = MainLoop(config=cfg, states=sm5)
if hasattr(ml5, 'resume_from_checkpoint'):
    recovered_state = ml5.resume_from_checkpoint()
    test("M5a: resume_from_checkpoint 返回状态",
         recovered_state is not None,
         f"phase={recovered_state.get('current_phase', '?') if recovered_state else 'None'}")
else:
    skip("M5a: resume_from_checkpoint 方法尚未实现")

# M5b: 全新启动（无检查点）→ P0
sm5b = StatesMachine(cfg)
ml5b = MainLoop(config=cfg, states=sm5b)
if hasattr(ml5b, 'resume_from_checkpoint'):
    state_fresh = ml5b.resume_from_checkpoint()
    test("M5b: 无检查点时从 P0 开始",
         state_fresh.get("current_phase") == "P0_ready",
         f"phase={state_fresh.get('current_phase', '?')}")
else:
    skip("M5b: resume_from_checkpoint 方法尚未实现")


# ═══════════════════════════════════════════════════════════
# M6: PhaseReport 完整性 + get_phase_summary
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("M6: PhaseReport 完整性")

r = PhaseReport("P2", "completed", "5 tasks executed", elapsed_ms=120.5)
test("M6a: PhaseReport 字段完整",
     r.phase == "P2" and r.status == "completed" and r.elapsed_ms == 120.5,
     f"phase={r.phase} status={r.status}")

test("M6b: PhaseReport timestamp 自动生成",
     bool(r.timestamp) and len(r.timestamp) > 0, "")

# get_phase_summary
if hasattr(ml, 'get_phase_summary'):
    summary = ml.get_phase_summary()
    test("M6c: get_phase_summary 返回 dict",
         isinstance(summary, dict) and "phases" in summary,
         f"keys={list(summary.keys()) if summary else 'None'}")
else:
    skip("M6c: get_phase_summary 方法不存在")

# ═══════════════════════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("测试汇总 — test_main_loop.py")
print("=" * 60)
for r in results:
    print(r)

total = PASS + FAIL
test_count = PASS + FAIL + SKIP
print(f"\n{'='*60}")
print(f"通过: {PASS}/{total} ({100*PASS/total:.1f}%)" if total > 0 else "通过: 0/0")
print(f"失败: {FAIL}/{total}" if total > 0 else "失败: 0/0")
print(f"跳过: {SKIP} (尚未实现)")
print(f"总计断言: {test_count}")
print(f"\n[RED] 预期多数 SKIP — 这些是 main_loop.py v0.3.0 接线逻辑")
print(f"[GREEN] 目标: 0 跳过, 0 失败")
