"""
test_negentropy.py — 五维负熵独立测试
═══════════════════════════════════════════════════════════

覆盖三维核心指标 + 二维辅助 + Dashboard生成:
  N1. 置信度均值 (来自 Guardian 会话)
  N2. 上下文化不确定性 (来自 Observer 统计)
  N3. 策略切换频率 (来自 StatesMachine)
  N4. 综合指数 + Dashboard + 向后兼容
"""
import sys, os
sys.path.insert(0, r"C:\Users\Administrator\Desktop\kugua-v0.2.1\kugua-code")
from kugua.negentropy import Negentropy, generate_dashboard, generate_integrity_report, DEFAULT_WEIGHTS
from kugua.guardian import GuardianSession

PASS = 0; FAIL = 0
def test(name, passed, detail=""):
    global PASS, FAIL
    if passed: PASS += 1; print(f"[PASS] {name}")
    else: FAIL += 1; print(f"[FAIL] {name} — {detail}")

print("=" * 50 + "\nNegentropy 独立测试")

# ── 准备测试数据 ──
state_clean = {
    "phase_regressions": 0, "phase_switches": 0,
    "anchor_changes": 0, "stagnation_events": 0,
    "retrieve_calls": 0, "total_subtasks": 1,
}
state_degraded = {
    "phase_regressions": 5, "phase_switches": 15,
    "anchor_changes": 3, "stagnation_events": 4,
    "retrieve_calls": 30, "total_subtasks": 5,
}

# Guardian 会话模拟
s_good = GuardianSession(session_id="good")
s_good.total_checks = 100; s_good.interventions = 5
s_bad = GuardianSession(session_id="bad")
s_bad.total_checks = 100; s_bad.interventions = 60

# Observer 统计
obs_good = {"total_gates": 100, "blocked_gates": 5}
obs_bad = {"total_gates": 100, "blocked_gates": 45}

# ═══════════════════════════════════════════════════════════
print("\nN1: 置信度均值")
ne_good = Negentropy(state_clean, guardian_sessions=[s_good])
ne_bad = Negentropy(state_clean, guardian_sessions=[s_bad])
test("N1a: 高置信度(低介入率)→高分",
     ne_good.confidence_mean() > 80, f"score={ne_good.confidence_mean()}")
test("N1b: 低置信度(高介入率)→低分",
     ne_bad.confidence_mean() < 50, f"score={ne_bad.confidence_mean()}")
test("N1c: 无会话→中性50",
     Negentropy(state_clean).confidence_mean() == 50.0, "")

# ═══════════════════════════════════════════════════════════
print("\nN2: 上下文化不确定性")
ne_obs_good = Negentropy(state_clean, observer_stats=obs_good)
ne_obs_bad = Negentropy(state_clean, observer_stats=obs_bad)
test("N2a: 低阻塞率→低不确定性", ne_obs_good.contextual_uncertainty() < 20,
     f"u={ne_obs_good.contextual_uncertainty()}")
test("N2b: 高阻塞率→高不确定性", ne_obs_bad.contextual_uncertainty() > 30,
     f"u={ne_obs_bad.contextual_uncertainty()}")
test("N2c: 无Observer→中性50",
     Negentropy(state_clean).contextual_uncertainty() == 50.0, "")

# ═══════════════════════════════════════════════════════════
print("\nN3: 策略切换频率")
ne_clean = Negentropy(state_clean)
ne_degraded = Negentropy(state_degraded)
test("N3a: 干净状态→低切换", ne_clean.strategy_switch_freq() < 20,
     f"freq={ne_clean.strategy_switch_freq()}")
test("N3b: 退化状态→高切换", ne_degraded.strategy_switch_freq() > 50,
     f"freq={ne_degraded.strategy_switch_freq()}")

# ═══════════════════════════════════════════════════════════
print("\nN4: 综合指数 + Dashboard")
test("N4a: 综合 0-100", 0 <= ne_clean.composite() <= 100,
     f"composite={ne_clean.composite()}")
test("N4b: 干净状态 > 退化状态",
     ne_clean.composite() > ne_degraded.composite(),
     f"clean={ne_clean.composite()}, degraded={ne_degraded.composite()}")

html = generate_dashboard(ne_clean)
test("N4c: Dashboard HTML 含标题", "苦瓜code" in html and "负熵仪表板" in html, "")
test("N4d: 新维度出现在 Dashboard", "置信度均值" in html, "")

report = generate_integrity_report(ne_clean)
test("N4e: 完整性报告含 COMPOSITE", report.startswith("COMPOSITE:"), f"report={report[:60]}")

# N5: 向后兼容 — 无新参数的旧调用
ne_old = Negentropy(state_clean)
test("N5a: 旧API process_order=100", ne_old.process_order() == 100, "")
test("N5b: 旧API intent_anchoring=100", ne_old.intent_anchoring() == 100, "")
test("N5c: 旧API to_dict 含所有维度", len(ne_old.to_dict()) >= 8, "")
test("N5d: DEFAULT_WEIGHTS 和为1", abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 0.01, "")

print("\n" + "=" * 50)
total = PASS + FAIL
print(f"通过: {PASS}/{total} | 失败: {FAIL}/{total}")
