"""
test_guardian.py — Guardian 认知监护独立测试
═══════════════════════════════════════════════════════════

覆盖: 四层检查、会话管理、性能基准、判决JSON、SDK集成
"""
import sys, os, json
from pathlib import Path
sys.path.insert(0, r"C:\Users\Administrator\Desktop\kugua-v0.2.1\kugua-code")
from kugua.config import KuguaConfig
from kugua.safety import SafetyManager
from kugua.guardian import Guardian, GuardianConfig, GuardianVerdict, GuardianSession
from kugua.client import GuardianClient, GuardianDecision

PASS = 0; FAIL = 0; results = []
def test(name, passed, detail=""):
    global PASS, FAIL
    if passed: PASS += 1; results.append(f"[PASS] {name}")
    else: FAIL += 1; results.append(f"[FAIL] {name} — {detail}")
    print(results[-1])

cfg = KuguaConfig(); safety = SafetyManager(cfg)
for _ in range(10): safety.record_safe_operation()
guardian = Guardian(GuardianConfig(confidence_threshold=0.7), safety_manager=safety)

# G1: 置信度门控
print("=" * 50 + "\nG1: 置信度门控")
v = guardian.check(operation="read_file", confidence=0.5)
test("G1a: 低置信度 → intervene", v.intervene and v.action == "retry", f"action={v.action}")
v2 = guardian.check(operation="read_file", confidence=0.95)
test("G1b: 高置信度 → 通过", not v2.intervene, f"intervene={v2.intervene}")

# G2: 权限门控
print("\nG2: 权限门控")
v3 = guardian.check(operation="rm_rf", confidence=0.95)
test("G2a: L5操作 → block", v3.intervene and v3.action == "block", f"action={v3.action}")
v4 = guardian.check(operation="read_file", confidence=0.9)
test("G2b: L1操作 → 通过", not v4.intervene, f"intervene={v4.intervene}")

# G3: 会话管理
print("\nG3: 会话管理")
for i in range(5):
    guardian.check(operation="read_file", confidence=0.9, session_id="sess-a")
s = guardian.get_session("sess-a")
test("G3a: 会话存在", s is not None, "")
test("G3b: total_checks=5", s.total_checks == 5, f"checks={s.total_checks}")
test("G3c: P50/P95 延迟可计算", s.latency_p50 >= 0 and s.latency_p95 >= 0, "")

# G4: 性能基准
print("\nG4: 性能基准")
report = guardian.benchmark_report()
test("G4a: latency 字段", "latency" in report and "p50_ms" in report["latency"], "")
test("G4b: throughput 字段", "throughput" in report, "")
test("G4c: memory 字段", "memory" in report, "")

# G5: 判决 JSON
print("\nG5: 判决序列化")
json_str = v.to_json()
d = json.loads(json_str)
test("G5a: JSON 包含 intervene", "intervene" in d, "")
test("G5b: JSON 包含 action", d.get("action") in ("retry", "block", "warn", "none", "slow_down"), f"action={d.get('action')}")

# G6: GuardianClient SDK
print("\nG6: SDK 集成")
gc = GuardianClient(confidence_threshold=0.7)
dec = gc.check(action="read_file", confidence=0.5)
test("G6a: low conf → not allowed", not dec.allowed and dec.action == "retry", f"action={dec.action}")
dec2 = gc.check(action="rm_rf", confidence=0.95)
test("G6b: L5 → blocked", not dec2.allowed, f"allowed={dec2.allowed}")
test("G6c: is_safe 快捷方法", gc.is_safe("read_file", 0.95) == True, "")

print("\n" + "=" * 50)
total = PASS + FAIL
print(f"通过: {PASS}/{total} | 失败: {FAIL}/{total}")
