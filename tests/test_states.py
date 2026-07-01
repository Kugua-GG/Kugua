"""
test_states.py — StatesMachine 独立测试 (v0.3.0 验收标准)
═══════════════════════════════════════════════════════════

覆盖 Sprint 1 Week 2 + 5 个外部参考模式:
  A. delta(S,E)->S' 三层转换 (pre-condition → execute → post-condition)
  B. 声明式守卫表 PHASE_GUARDS
  C. PhaseStagnationGuard (Null-Transition 阶段停滞检测)
  D. Saga 补偿回退 PHASE_COMPENSATIONS
  E. 检查点 Suspend/Resume + 崩溃恢复
  F. 向后兼容 (现有 8 项测试保持通过)

执行: python test_states.py

当前状态: RED — 大部分测试预期失败（states.py v0.2.1 是桩代码）
目标状态: GREEN — 全部通过（states.py v0.3.0 重写完成）
"""
import sys
import os
import json
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, r"C:\Users\Administrator\Desktop\kugua-v0.2.1\kugua-code")

from kugua.config import KuguaConfig
from kugua.states import (
    StatesMachine,
    PhaseTransitionError,
    AlignResult,
    VALID_PHASES,
    PHASE_ORDER,
)

PASS = 0
FAIL = 0
SKIP = 0
results = []


def test(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        results.append(f"[PASS] {name}")
    else:
        FAIL += 1
        results.append(f"[FAIL] {name} — {detail}")
    print(results[-1])


def skip(name, reason=""):
    global SKIP
    SKIP += 1
    results.append(f"[SKIP] {name} — {reason}")
    print(results[-1])


# ── 初始化 ─────────────────────────────────────────────────
cfg = KuguaConfig()
artifacts = cfg.artifacts_dir
artifacts.mkdir(parents=True, exist_ok=True)
print(f"Artifacts: {artifacts}\n")

# 辅助: 构造合法的转换上下文
def ctx(**kwargs):
    """快捷构造带 intent_anchor + task_dag 的上下文。"""
    defaults = {
        "intent_anchor": kwargs.get("intent_anchor", {"user_goal": "test"}),
        "task_dag": kwargs.get("task_dag", [{"id": "t1", "assigned_worker": "w1"}]),
    }
    defaults.update(kwargs)
    return defaults


# ═══════════════════════════════════════════════════════════
# 区域 A: delta(S,E)->S' 三层转换（pre→execute→post）
# ═══════════════════════════════════════════════════════════
print("=" * 60)
print("区域 A: delta(S,E)->S' 三层转换")

sm = StatesMachine(cfg)
state = sm.p0_self_check()

# A1: 正常转换 — 前置条件满足（需要 intent_anchor + task_dag）
try:
    state = sm.transition(state, "P1_planned", context=ctx())
    test("A1: P0→P1 正常转换成功",
         state["current_phase"] == "P1_planned",
         f"phase={state['current_phase']}")
except PhaseTransitionError as e:
    test("A1: P0→P1 正常转换成功", False, f"异常: {e}")

# A2: 非法转换 — 前置条件不满足应抛出 PhaseTransitionError
# P1→P4 直接跳应该被拒绝（缺少中间阶段）
try:
    state2 = sm.p0_self_check()
    state2 = sm.transition(state2, "P4_delivered")  # 跳过 P1,P2,P3
    test("A2: P0→P4 跳跃被前置条件拦截",
         False,
         f"居然成功了 phase={state2['current_phase']}")
except PhaseTransitionError as e:
    test("A2: P0→P4 跳跃被前置条件拦截",
         True,
         f"拦截: {str(e)[:80]}")

# A3: 需要 intent_anchor 才能 P0→P1
state_a3 = sm.p0_self_check()
if hasattr(sm, 'transition'):
    try:
        # 不带 context 的 transition 应该被拒绝
        result = sm.transition(state_a3, "P1_planned", context={})
        test("A3: 无 intent_anchor 时 P0→P1 被拒绝",
             False,
             f"居然成功了")
    except PhaseTransitionError:
        test("A3: 无 intent_anchor 时 P0→P1 被拒绝",
             True,
             "正确拒绝")
    except TypeError:
        # 如果 transition 不支持 context 参数，说明新 API 还没实现
        skip("A3: transition() 不支持 context 参数 — 新 API 尚未实现")
else:
    skip("A3: transition() 方法尚未实现")

# A4: 后置条件验证 — 转换后状态必须匹配目标
state_a4 = sm.p0_self_check()
state_a4 = sm.transition(state_a4, "P1_planned", context=ctx())
test("A4: 转换后 current_phase 精确等于目标",
     state_a4["current_phase"] == "P1_planned",
     f"phase={state_a4['current_phase']}")

# A5: 后置条件失败 → 自动回退
# (这个需要内部触发，直接测比较难，先检查是否有回退保护机制)
if hasattr(sm, '_validate_postcondition'):
    test("A5: _validate_postcondition 方法存在",
         callable(sm._validate_postcondition),
         "方法存在")
else:
    skip("A5: _validate_postcondition() 方法尚未实现")


from types import MappingProxyType

# ═══════════════════════════════════════════════════════════
# 区域 B: 声明式守卫表 PHASE_GUARDS
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("区域 B: 声明式守卫表 PHASE_GUARDS")

# B1: PHASE_GUARDS 表存在（可能是 dict 或 MappingProxyType）
if hasattr(StatesMachine, 'PHASE_GUARDS') or hasattr(sm, 'PHASE_GUARDS'):
    guards = getattr(sm, 'PHASE_GUARDS', None) or getattr(StatesMachine, 'PHASE_GUARDS', None)
    is_mapping = isinstance(guards, (dict, MappingProxyType))
    test("B1: PHASE_GUARDS 守卫表存在",
         guards is not None and is_mapping,
         f"type={type(guards).__name__}, len={len(guards) if guards else 0}")
else:
    skip("B1: PHASE_GUARDS 尚未定义")

# B2: 守卫表包含 P0→P1 的守卫
if hasattr(sm, 'PHASE_GUARDS'):
    guards = sm.PHASE_GUARDS
    p0p1_key = ("P0_ready", "P1_planned")
    test("B2: PHASE_GUARDS 包含 P0→P1 守卫",
         p0p1_key in guards or "P0_ready" in str(guards),
         f"keys sample={list(guards.keys())[:3] if guards else 'empty'}")
else:
    skip("B2: PHASE_GUARDS 尚未定义")

# B3: 守卫表包含 P3→P4 的评分守卫（review_verdict >= 0.7）
if hasattr(sm, 'PHASE_GUARDS'):
    guards = sm.PHASE_GUARDS
    has_p3p4 = any(
        "P3" in str(k) and "P4" in str(k)
        for k in guards.keys()
    )
    test("B3: PHASE_GUARDS 包含 P3→P4 守卫",
         has_p3p4,
         f"found={has_p3p4}")
else:
    skip("B3: PHASE_GUARDS 尚未定义")

# B4: 单个守卫可被 _eval_guard 评估
if hasattr(sm, '_eval_guard'):
    # 测试 exists 操作符
    result = sm._eval_guard({"field": "test_field", "op": "exists"}, {"test_field": 1})
    test("B4a: _eval_guard exists=True",
         result == True,
         f"result={result}")
    result2 = sm._eval_guard({"field": "missing", "op": "exists"}, {})
    test("B4b: _eval_guard exists=False",
         result2 == False,
         f"result={result2}")
else:
    skip("B4: _eval_guard() 方法尚未实现")

# B5: 守卫表不可变（防止运行时篡改）
if hasattr(sm, 'PHASE_GUARDS'):
    try:
        sm.PHASE_GUARDS["fake"] = "malicious"
        test("B5: PHASE_GUARDS 不可变", False, "可以被修改")
    except (TypeError, AttributeError):
        test("B5: PHASE_GUARDS 不可变", True, "受保护")
else:
    skip("B5: PHASE_GUARDS 尚未定义")


# ═══════════════════════════════════════════════════════════
# 区域 C: PhaseStagnationGuard（Null-Transition 检测）
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("区域 C: PhaseStagnationGuard（阶段停滞检测）")

# C1: stagnation_events 字段存在且初始为 0
state_c = sm.p0_self_check()
stagnation = state_c.get("stagnation_events", None)
test("C1: stagnation_events 初始=0",
     stagnation == 0,
     f"stagnation_events={stagnation}")

# C2: 重复请求相同转换 → stagnation 计数上升
if hasattr(sm, '_check_stagnation') or hasattr(sm, '_evaluate_transitions'):
    # 连续多次无效转换尝试
    sm_c = StatesMachine(cfg)
    state_c2 = sm_c.p0_self_check()
    # 尝试非法转换 3 次（应该累积停滞计数）
    for _ in range(3):
        try:
            state_c2 = sm_c.transition(state_c2, "P4_delivered", context={})
        except PhaseTransitionError:
            pass
    test("C2: 3 次无效转换 → stagnation_events > 0",
         state_c2.get("stagnation_events", 0) > 0,
         f"stagnation_events={state_c2.get('stagnation_events', 0)}")
else:
    skip("C2: 停滞检测方法尚未实现")

# C3: 区分 PhaseStagnationGuard 与 executor.StagnationDetector
# (文档级检查 — 确保命名不冲突)
if hasattr(sm, '_check_stagnation'):
    # PhaseStagnationGuard 应该关注阶段级别，不是输出 hash
    import inspect
    src = inspect.getsource(sm._check_stagnation) if hasattr(sm, '_check_stagnation') else ""
    has_hash = "hash" in src.lower() or "md5" in src.lower()
    test("C3: PhaseStagnationGuard 不使用 hash 检测（与 executor 区分）",
         not has_hash,
         "使用了 hash 检测，与 executor.StagnationDetector 重叠" if has_hash else "正确使用阶段级检测")
else:
    skip("C3: _check_stagnation 尚未实现")

# C4: 停滞阈值达到 → 触发 replan 信号
if hasattr(sm, '_handle_stagnation'):
    test("C4: _handle_stagnation 方法存在",
         callable(sm._handle_stagnation),
         "方法存在")
else:
    skip("C4: _handle_stagnation() 方法尚未实现")


# ═══════════════════════════════════════════════════════════
# 区域 D: Saga 补偿回退 PHASE_COMPENSATIONS
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("区域 D: Saga 补偿回退")

# D1: PHASE_COMPENSATIONS 表存在
if hasattr(sm, 'PHASE_COMPENSATIONS'):
    comps = sm.PHASE_COMPENSATIONS
    test("D1: PHASE_COMPENSATIONS 补偿表存在",
         isinstance(comps, (dict, MappingProxyType)),
         f"type={type(comps).__name__}, len={len(comps)}")
else:
    skip("D1: PHASE_COMPENSATIONS 尚未定义")

# D2: P2_executed 有对应的补偿动作
if hasattr(sm, 'PHASE_COMPENSATIONS'):
    has_p2_comp = "P2_executed" in sm.PHASE_COMPENSATIONS
    test("D2: P2_executed 有补偿动作",
         has_p2_comp,
         f"compensations={list(sm.PHASE_COMPENSATIONS.keys())}")
else:
    skip("D2: PHASE_COMPENSATIONS 尚未定义")

# D3: rollback_to 方法存在
if hasattr(sm, 'rollback_to'):
    test("D3: rollback_to() 方法存在",
         callable(sm.rollback_to),
         "方法存在")
else:
    skip("D3: rollback_to() 方法尚未实现")

# D4: P3 失败 → 补偿回退到 P1
if hasattr(sm, 'rollback_to') and hasattr(sm, 'PHASE_COMPENSATIONS'):
    sm_d = StatesMachine(cfg)
    state_d = sm_d.p0_self_check()
    state_d["current_phase"] = "P3_failed"
    state_d["phase_regressions"] = 0
    # 执行回退
    state_d = sm_d.rollback_to(state_d, "P1_planned")
    test("D4a: P3→P1 回退后阶段 = P1_planned",
         state_d["current_phase"] == "P1_planned",
         f"phase={state_d['current_phase']}")
    test("D4b: 回退记录 phase_regressions++",
         state_d.get("phase_regressions", 0) >= 1,
         f"regressions={state_d.get('phase_regressions', 0)}")
else:
    skip("D4: rollback_to() 尚未实现")

# D5: 补偿逆序执行（P3→P1 应先补偿 P2 再 P1）
if hasattr(sm, '_compensate_p2'):
    test("D5: _compensate_p2 补偿方法存在",
         callable(sm._compensate_p2),
         "方法存在")
else:
    skip("D5: _compensate_p2 补偿方法尚未实现")


# ═══════════════════════════════════════════════════════════
# 区域 E: 检查点 Suspend/Resume + 崩溃恢复
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("区域 E: 检查点 + 崩溃恢复")

# E1: 关键阶段转换时自动保存检查点
if hasattr(sm, '_save_checkpoint'):
    sm_e = StatesMachine(cfg)
    state_e = sm_e.p0_self_check()
    state_e = sm_e.transition(state_e, "P1_planned", context=ctx())
    state_e["plan_status"] = "frozen"
    state_e = sm_e.transition(state_e, "P1_frozen", context={"task_dag": ctx()["task_dag"]})
    ckpt_path = artifacts / "checkpoints" / "ckpt_P1_frozen.json"
    test("E1: P1_frozen 后检查点已保存",
         ckpt_path.exists(),
         f"exists={ckpt_path.exists()}")
else:
    skip("E1: _save_checkpoint() 方法尚未实现")

# E2: 崩溃恢复 — 从检查点恢复
state_e2 = sm.p0_self_check()
state_e2["current_phase"] = "P2_executed"
state_e2["test_marker"] = "crash_test"
sm.save_state(state_e2)
recovered = sm.crash_recovery()
test("E2: crash_recovery 恢复已保存状态",
     recovered.get("test_marker") == "crash_test",
     f"test_marker={recovered.get('test_marker')}")

# E3: 如果检查点存在且比 agent_state 更新，优先用检查点
if hasattr(sm, '_load_checkpoint'):
    ckpt = sm._load_checkpoint("P1_frozen")
    test("E3: _load_checkpoint 能加载检查点",
         ckpt is not None,
         f"ckpt={'exists' if ckpt else 'None'}")
else:
    skip("E3: _load_checkpoint() 方法尚未实现")

# E4: 检查点目录存在
ckpt_dir = artifacts / "checkpoints"
test("E4: 检查点目录存在",
     ckpt_dir.is_dir(),
     f"dir={ckpt_dir}")

# E5: 恢复后验证状态合法性
if hasattr(sm, '_is_valid_state'):
    valid = sm._is_valid_state({"current_phase": "P1_planned"})
    invalid = sm._is_valid_state({"current_phase": "INVALID_PHASE"})
    test("E5a: _is_valid_state 合法状态 → True",
         valid == True,
         f"valid={valid}")
    test("E5b: _is_valid_state 非法状态 → False",
         invalid == False,
         f"invalid={invalid}")
else:
    skip("E5: _is_valid_state() 方法尚未实现")


# ═══════════════════════════════════════════════════════════
# 区域 F: 向后兼容（现有 8 项测试等价验证）
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("区域 F: 向后兼容验证")

sm_f = StatesMachine(cfg)

# F1: P0 自检
state_f = sm_f.p0_self_check()
test("F1: P0 自检完成 current_phase=P0_ready",
     state_f["current_phase"] == "P0_ready",
     f"phase={state_f['current_phase']}")

# F2: P0→P1 正常推进（需要 context）
state_f = sm_f.transition(state_f, "P1_planned", context=ctx())
test("F2: P0→P1 正常推进",
     state_f["current_phase"] == "P1_planned",
     f"phase={state_f['current_phase']}")

# F3: P1→P2 正常推进
state_f = sm_f.transition(state_f, "P2_executed", context={"task_dag": ctx()["task_dag"]})
test("F3: P1→P2 正常推进",
     state_f["current_phase"] == "P2_executed",
     f"phase={state_f['current_phase']}")

# F4: P2→P0 回退记录 regression
state_f = sm_f.transition(state_f, "P0_init")
test("F4: P2→P0 回退记录 phase_regression",
     state_f.get("phase_regressions", 0) >= 1,
     f"regressions={state_f.get('phase_regressions', 0)}")

# F5: 阶段历史记录
history = state_f.get("phase_history", [])
test("F5: phase_history 包含所有 transition 产生的记录",
     len(history) >= 3,
     f"len={len(history)}, entries={[h['phase'] for h in history]}")

# F6: save/load 往返
state_f6 = sm_f.p0_self_check()
state_f6["test_field"] = "roundtrip_value"
sm_f.save_state(state_f6)
recovered_f6 = sm_f.load_state()
test("F6: save_state → load_state 往返",
     recovered_f6.get("test_field") == "roundtrip_value",
     f"test_field={recovered_f6.get('test_field')}")

# F7: crash_recovery 等同于 load_state
state_f7 = sm_f.p0_self_check()
state_f7["crash_marker"] = "recovery_test"
sm_f.save_state(state_f7)
cr = sm_f.crash_recovery()
test("F7: crash_recovery 加载已保存状态",
     cr.get("crash_marker") == "recovery_test",
     f"marker={cr.get('crash_marker')}")

# F8: VALID_PHASES 和 PHASE_ORDER 导出正确
test("F8a: VALID_PHASES 包含 9 个阶段",
     len(VALID_PHASES) == 9,
     f"len={len(VALID_PHASES)}")
test("F8b: PHASE_ORDER P0_init=0, P4_delivered=8",
     PHASE_ORDER.get("P0_init") == 0 and PHASE_ORDER.get("P4_delivered") == 8,
     f"P0={PHASE_ORDER.get('P0_init')}, P4={PHASE_ORDER.get('P4_delivered')}")


# ═══════════════════════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("测试汇总 — test_states.py")
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
print(f"\n[RED] 红灯阶段: 预期多数 SKIP — 这些是 states.py v0.3.0 新功能")
print(f"[GREEN] 绿灯目标: 0 跳过, 0 失败 — 全部 PASS")
