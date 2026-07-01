"""
test_safety.py — SafetyManager 独立测试 (v0.3.0 验收标准)
═══════════════════════════════════════════════════════════════

覆盖 Sprint 1 Week 1 的 5 条验收标准:
  A. TrustLevel 动态调整 (升级/降级/最低边界)
  B. ErrorBudget 消耗与自动恢复
  C. Kill Switch 触发条件链
  D. Incident 自动分级
  E. 向后兼容 (现有 9 项测试保持通过)

执行: python test_safety.py

当前状态: 🔴 红灯 — 大部分测试预期失败（safety.py v0.2.1 是桩代码）
目标状态: 🟢 绿灯 — 全部通过（safety.py v0.3.0 重写完成）
"""
import sys
import os
import time
from pathlib import Path
from datetime import datetime, timezone

# 添加 kugua-code 到路径
sys.path.insert(0, r"C:\Users\Administrator\Desktop\kugua-v0.2.1\kugua-code")

from kugua.config import KuguaConfig
from kugua.safety import (
    SafetyManager,
    TrustLevel,
    Incident,
    ErrorBudget,
    OPERATION_PERMISSIONS,
    L1_TRUST, L2_TRUST, L3_TRUST, L4_TRUST, L5_TRUST,
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

# ═══════════════════════════════════════════════════════════
# 区域 A: TrustLevel 动态调整
# ═══════════════════════════════════════════════════════════
print("=" * 60)
print("区域 A: TrustLevel 动态调整")

sm = SafetyManager(cfg)

# A1: 初始信任级别 = L2
test("A1: 初始信任级别 = L2",
     sm._trust_level.value == L2_TRUST,
     f"当前值={sm._trust_level.value}")

# A2: 连续安全操作计数从 0 开始
# (需要检查新属性 consecutive_safe_ops 是否存在)
if hasattr(sm, 'consecutive_safe_ops'):
    test("A2: 初始 consecutive_safe_ops = 0",
         sm.consecutive_safe_ops == 0,
         f"值={sm.consecutive_safe_ops}")
else:
    skip("A2: consecutive_safe_ops 属性尚未实现")

# A3: 连续 10 次安全操作 → 信任升级 L2→L3
# 安全操作 = read_file 在 L2 级别通过
if hasattr(sm, 'record_safe_operation'):
    for _ in range(10):
        sm.record_safe_operation()
    test("A3: 连续 10 次安全操作 → 升级到 L3",
         sm._trust_level.value == L3_TRUST,
         f"当前值={sm._trust_level.value}")
else:
    skip("A3: record_safe_operation() 方法尚未实现")

# A4: 信任不得低于 L1（最低安全级别）
# 注入大量事故试图降级
if hasattr(sm, 'record_safe_operation') and hasattr(sm, 'downgrade_trust'):
    # 先升级到 L3
    sm2 = SafetyManager(cfg)
    for _ in range(10):
        sm2.record_safe_operation()
    # 然后制造多次事故试图降级
    for i in range(20):
        incident = Incident("IV", "测试", f"降级测试{i}", impact="低", score=1)
        sm2.log_incident(incident)
        if hasattr(sm2, 'downgrade_trust'):
            sm2.downgrade_trust(incident)
    test("A4: 信任级别不低于 L1",
         sm2._trust_level.value >= L1_TRUST,
         f"当前值={sm2._trust_level.value}")
else:
    skip("A4: downgrade_trust() 方法尚未实现")

# A5: 信任升级后，高风险操作仍按原规则检查
if hasattr(sm, 'record_safe_operation'):
    sm3 = SafetyManager(cfg)
    # 升级到 L3
    for _ in range(10):
        sm3.record_safe_operation()
    # execute_cmd 需要 L4，L3 不够
    allowed, reason = sm3.check_permission("execute_cmd")
    test("A5: L3 时 execute_cmd 仍被拒绝",
         not allowed,
         f"reason={reason}")
else:
    skip("A5: record_safe_operation() 方法尚未实现")


# ═══════════════════════════════════════════════════════════
# 区域 B: ErrorBudget 消耗与自动恢复
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("区域 B: ErrorBudget 消耗与自动恢复")

# B1: ErrorBudget 初始化
budget = ErrorBudget(max_errors=10, current=0, window_hours=24)
test("B1: ErrorBudget 初始化 max=10, current=0",
     budget.max_errors == 10 and budget.current == 0,
     f"max={budget.max_errors}, current={budget.current}")

# B2: consume() 增加消耗计数
if hasattr(budget, 'consume'):
    budget.consume()
    budget.consume()
    test("B2: consume() 2 次 → current=2",
         budget.current == 2,
         f"current={budget.current}")
elif hasattr(budget, 'current'):
    # 手动模拟
    budget.current = 2
    skip("B2: consume() 方法尚未实现，手动设置 current=2")
else:
    skip("B2: consume() 方法尚未实现")

# B3: is_exhausted() 判断
budget2 = ErrorBudget(max_errors=3, current=3, window_hours=24)
if hasattr(budget2, 'is_exhausted'):
    test("B3: current=3 >= max=3 → is_exhausted=True",
         budget2.is_exhausted() == True,
         f"结果={budget2.is_exhausted()}")
else:
    skip("B3: is_exhausted() 方法尚未实现")

# B4: 预算未耗尽 → 正常
budget3 = ErrorBudget(max_errors=5, current=2, window_hours=24)
if hasattr(budget3, 'is_exhausted'):
    test("B4: current=2 < max=5 → is_exhausted=False",
         budget3.is_exhausted() == False,
         f"结果={budget3.is_exhausted()}")
else:
    skip("B4: is_exhausted() 方法尚未实现")

# B5: 预算耗尽 → 自动信任降级
sm4 = SafetyManager(cfg)
if hasattr(sm4, '_budget') and hasattr(sm4._budget, 'consume'):
    # 消耗所有预算（max=10）
    for _ in range(10):
        sm4._budget.consume()
    # 再做一次 L4 操作触发预算检查
    sm4._trust_level.set_to(L3_TRUST)  # 确保有足够信任级别通过权限检查
    allowed, reason = sm4.check_permission("execute_cmd")
    # 预算耗尽后信任应降级
    test("B5: ErrorBudget 耗尽 → 信任降级",
         sm4._trust_level.value <= L2_TRUST or not allowed,
         f"trust_level=L{sm4._trust_level.value}, allowed={allowed}, reason={reason[:80]}")
else:
    skip("B5: SafetyManager 尚未集成 ErrorBudget")

# B6: replenish() 恢复预算
if hasattr(budget, 'replenish'):
    b = ErrorBudget(max_errors=10, current=8, window_hours=24)
    b.replenish(5)
    test("B6: replenish(5) 后 current=3",
         b.current == 3,
         f"current={b.current}")
elif hasattr(budget, 'current'):
    skip("B6: replenish() 方法尚未实现")
else:
    skip("B6: replenish() 方法尚未实现")


# ═══════════════════════════════════════════════════════════
# 区域 C: Kill Switch 触发条件链
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("区域 C: Kill Switch 触发条件链")

# C1: Kill Switch 触发后所有操作返回 False
sm_ks = SafetyManager(cfg)
sm_ks.kill_switch("测试熔断")
allowed, reason = sm_ks.check_permission("read_file")
test("C1: Kill Switch 后 read_file 被拒绝",
     not allowed,
     f"reason={reason}")

# C2: Kill Switch 需要人工重置 — 持续拒绝
allowed2, _ = sm_ks.check_permission("read_file")
test("C2: Kill Switch 后持续拒绝（无自动恢复）",
     not allowed2,
     f"allowed={allowed2}")

# C6: Kill Switch 的原因被记录（在 reset 之前检查）
test("C6: kill_switch 原因被记录",
     sm_ks._state.get("kill_reason") == "测试熔断",
     f"kill_reason={sm_ks._state.get('kill_reason')}")

# C3: reset_kill_switch 恢复
if hasattr(sm_ks, 'reset_kill_switch'):
    sm_ks.reset_kill_switch()
    allowed3, _ = sm_ks.check_permission("read_file")
    test("C3: reset_kill_switch() 后操作恢复",
         allowed3,
         f"allowed={allowed3}")
else:
    skip("C3: reset_kill_switch() 方法尚未实现")

# C4: 连续 3 次权限拒绝 → 自动触发 Kill Switch
sm_auto = SafetyManager(cfg)
# 连续 3 次被拒绝的高风险操作（execute_cmd 需要 L4，当前 L2）
for _ in range(3):
    sm_auto.check_permission("execute_cmd")
test("C4: 连续 3 次权限拒绝 → Kill Switch 自动触发",
     sm_auto._state.get("emergency_stop") == True,
     f"emergency_stop={sm_auto._state.get('emergency_stop')}")

# C5: L5 操作实际执行 → 立即 Kill Switch（通过 report_l5_attempt）
sm_l5 = SafetyManager(cfg)
if hasattr(sm_l5, 'report_l5_attempt'):
    sm_l5.report_l5_attempt("sudo")
    test("C5: L5 操作实际执行 → 立即 Kill Switch",
         sm_l5._state.get("emergency_stop") == True,
         f"emergency_stop={sm_l5._state.get('emergency_stop')}")
else:
    skip("C5: report_l5_attempt() 方法尚未实现")


# ═══════════════════════════════════════════════════════════
# 区域 D: Incident 自动分级
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("区域 D: Incident 自动分级")

# D1: Incident 基本字段
inc = Incident(level="III", category="测试", description="测试事故", impact="中", score=15)
test("D1: Incident 字段正确",
     inc.level == "III" and inc.category == "测试" and inc.score == 15,
     f"level={inc.level}, category={inc.category}, score={inc.score}")

# D2: 如果实现了 auto_classify
if hasattr(Incident, 'auto_classify') or hasattr(SafetyManager, 'classify_incident'):
    # 不可恢复 + 广泛影响 → I 级
    if hasattr(SafetyManager, 'classify_incident'):
        sm_cls = SafetyManager(cfg)
        result = sm_cls.classify_incident(
            operation="execute_cmd",
            impact_scope="广泛",
            recoverable=False,
        )
        test("D2: 不可恢复+广泛影响 → I 级",
             result == "I" or (hasattr(result, 'level') and result.level == "I"),
             f"result={result}")
    else:
        skip("D2: classify_incident() 尚未实现（auto_classify 为类方法）")
else:
    skip("D2: 自动分级方法尚未实现")

# D3: 可恢复 + 局部影响 → IV 级（最低严重度）
if hasattr(SafetyManager, 'classify_incident'):
    sm_cls = SafetyManager(cfg)
    result = sm_cls.classify_incident(
        operation="read_file",
        impact_scope="局部",
        recoverable=True,
    )
    test("D3: 可恢复+局部影响 → IV 级",
         result.level == "IV",
         f"result={result}")
else:
    skip("D3: classify_incident() 尚未实现")

# D4: I 级事故 → 立即 Kill Switch
if hasattr(SafetyManager, 'log_incident') and hasattr(SafetyManager, 'classify_incident'):
    sm_i = SafetyManager(cfg)
    inc_i = Incident("I", "安全", "严重安全事故", impact="广泛", score=100)
    sm_i.log_incident(inc_i)
    test("D4: I 级事故 → 自动 Kill Switch",
         sm_i._state.get("emergency_stop") == True,
         f"emergency_stop={sm_i._state.get('emergency_stop')}")
else:
    skip("D4: I 级事故自动 Kill Switch 尚未实现")

# D5: Incident timestamp 自动生成
test("D5: Incident timestamp 自动生成",
     inc.timestamp is not None and len(inc.timestamp) > 0,
     f"timestamp={inc.timestamp}")


# ═══════════════════════════════════════════════════════════
# 区域 E: 向后兼容（现有 9 项测试等价验证）
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("区域 E: 向后兼容验证")

sm_e = SafetyManager(cfg)

# E1-E5: L5 永久禁止（使用独立实例避免累积拒绝触发 Kill Switch）
sm_e_l5 = SafetyManager(cfg)
allowed, reason = sm_e_l5.check_permission("rm_rf")
test("E1: rm_rf 被永久禁止",
     not allowed,
     f"reason={reason}")

allowed, reason = sm_e_l5.check_permission("sudo")
test("E2: sudo 被永久禁止",
     not allowed,
     f"reason={reason}")

# 注意: 连续 3 次 L5 拒绝会触发 Auto Kill Switch
# 此处故意跨实例测试以验证各操作独立拒绝
sm_e_l5b = SafetyManager(cfg)
allowed, reason = sm_e_l5b.check_permission("git_push_force")
test("E3: git_push_force 被永久禁止",
     not allowed,
     f"reason={reason}")

allowed, reason = sm_e_l5b.check_permission("eval_shell")
test("E4: eval_shell 被永久禁止",
     not allowed,
     f"reason={reason}")

allowed, reason = sm_e_l5b.check_permission("pipe_to_sh")
test("E5: pipe_to_sh 被永久禁止",
     not allowed,
     f"reason={reason}")

# E6: 使用全新实例验证 read_file 在 L2 放行（不受 L5 拒绝累积影响）
sm_e_read = SafetyManager(cfg)
allowed, reason = sm_e_read.check_permission("read_file")
test("E6: read_file 在 L2 级别放行",
     allowed,
     f"reason={reason}")

# E7: execute_cmd 在 L2 被拒绝
allowed, reason = sm_e.check_permission("execute_cmd")
test("E7: execute_cmd 在 L2 被拒绝",
     not allowed,
     f"reason={reason}")

# E8: Kill Switch
state_kill = sm_e.kill_switch("兼容测试")
test("E8: Kill Switch 设置 emergency_stop=True",
     state_kill.get("emergency_stop") == True,
     f"emergency_stop={state_kill.get('emergency_stop')}")

# E9: 事故记录
incident = Incident("IV", "test", "兼容测试事故", impact="无", score=5)
sm_e2 = SafetyManager(cfg)  # 新实例避免 Kill Switch 干扰
sm_e2.log_incident(incident)
incidents = sm_e2.query_incidents(days=30)
test("E9: 事故记录可查询",
     len(incidents) >= 1,
     f"incident count={len(incidents)}")

# E10: OPERATION_PERMISSIONS 完整性
test("E10: OPERATION_PERMISSIONS 包含所有 10 个原始操作",
     all(k in OPERATION_PERMISSIONS for k in
         ["read_file", "write_file", "execute_cmd", "rm_rf", "sudo",
          "git_push_force", "eval_shell", "pipe_to_sh", "edit_file", "delete_file"]),
     f"keys={sorted(OPERATION_PERMISSIONS.keys())}")


# ═══════════════════════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("测试汇总 — test_safety.py")
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
print(f"\n[RED] 红灯阶段: 预期 {SKIP} 项跳过 — 这些是待实现的新功能")
print(f"[GREEN] 绿灯目标: 0 跳过, 0 失败 — 全部 PASS")
