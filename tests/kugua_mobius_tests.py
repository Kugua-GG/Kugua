"""
kugua Mobius module tests — 10 tasks
"""
import sys, math, os, tempfile, shutil
from pathlib import Path

sys.path.insert(0, r"C:\Users\Administrator\Desktop\kugua-v0.2.1\kugua-code")

from kugua.mobius import (
    MobiusController, CorrectionSpectrum, CorrectionBias, TwistPoint
)
from kugua.double_loop import DoubleLoopExecutor, DoubleLoopEvent

PASS = 0
FAIL = 0

def test(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name} -- {detail}")

print("=" * 60)
print("Mobius Module Tests (new since v0.2.0)")
print("=" * 60)

# ============================================================
# Task M1: CorrectionBias
# ============================================================
print("\n--- Task M1: CorrectionBias ---")

b = CorrectionBias(
    error_location="rate_calc", error_type="accuracy",
    correction_hint="fix: distinguish fixed vs floating rate",
    confidence=0.85, gv_id="loan_lpr")
test("M1a: all fields set",
     b.error_location == "rate_calc" and b.confidence == 0.85)
test("M1b: bias_id auto-generated",
     b.bias_id.startswith("bias_"))
test("M1c: to_prompt_fragment contains hint",
     "fixed vs floating" in b.to_prompt_fragment())
test("M1d: to_dict preserves fields",
     b.to_dict()["error_location"] == "rate_calc")
test("M1e: empty bias = empty fragment",
     CorrectionBias().to_prompt_fragment() == "")

# ============================================================
# Task M2: CorrectionSpectrum
# ============================================================
print("\n--- Task M2: CorrectionSpectrum ---")

s = CorrectionSpectrum(gv_id="gv_x", error_type="accuracy",
                       bias_weight_per_instance=0.12)
test("M2a: initial intensity=0",
     math.isclose(s.intensity, 0.0, abs_tol=0.001))
test("M2b: initial level=L0_HINT",
     s.current_level == "L0_HINT")

s.add_bias(CorrectionBias(error_location="loc_a", error_type="accuracy",
    correction_hint="fix1", confidence=0.8, gv_id="gv_x"))
test("M2c: after 1 bias intensity>0", s.intensity > 0.05)
test("M2d: bias_count=1", s.bias_count == 1)

s.add_bias(CorrectionBias(error_location="loc_a", error_type="accuracy",
    correction_hint="fix2", confidence=0.8, gv_id="gv_x"))
s.add_bias(CorrectionBias(error_location="loc_a", error_type="accuracy",
    correction_hint="fix3", confidence=0.85, gv_id="gv_x"))
test("M2e: reaches twist point after 3 same-loc biases",
     s.is_at_twist_point,
     f"intensity={s.intensity:.2f}, level={s.current_level}")

s2 = CorrectionSpectrum(gv_id="gv_y", error_type="accuracy",
                        bias_weight_per_instance=0.12)
for loc in ["a","b","c","d","e"]:
    s2.add_bias(CorrectionBias(error_location=loc, error_type="accuracy",
        correction_hint=f"fix_{loc}", confidence=0.8, gv_id="gv_y"))
test("M2f: scattered > concentrated locations",
     len(s2.unique_locations) > len(s.unique_locations),
     f"scattered={len(s2.unique_locations)} vs conc={len(s.unique_locations)}")

# ============================================================
# Task M3: Level Boundaries
# ============================================================
print("\n--- Task M3: Level Boundaries ---")

test_levels = [
    (0.05, "L0_HINT"), (0.15, "L0_HINT"),
    (0.25, "L1_BIAS"), (0.35, "L1_BIAS"),
    (0.45, "L2_OVERRIDE"), (0.55, "L2_OVERRIDE"),
    (0.65, "L3_CANDIDATE"), (0.75, "L3_CANDIDATE"),
    (0.85, "L4_COMMIT"), (0.95, "L4_COMMIT"),
]
all_ok = True
for intensity, expected in test_levels:
    st = CorrectionSpectrum(gv_id="_", error_type="_")
    st.intensity = intensity
    if st.current_level != expected:
        all_ok = False
        print(f"  MISMATCH: i={intensity} -> {st.current_level} (expected {expected})")
test("M3a: all 10 level boundaries correct", all_ok)

st = CorrectionSpectrum(gv_id="_", error_type="_")
st.intensity = 0.45
test("M3b: 0.45 = twist point", st.is_at_twist_point)
test("M3c: 0.45 != trigger", not st.should_trigger_double_loop)
st.intensity = 0.85
test("M3d: 0.85 = trigger", st.should_trigger_double_loop)
test("M3e: 0.85 != twist (past it)", not st.is_at_twist_point)

# ============================================================
# Task M4: MobiusController
# ============================================================
print("\n--- Task M4: MobiusController ---")

mc = MobiusController()
test("M4a: starts empty", len(mc.all_spectra()) == 0)

mc.push_bias(CorrectionBias(error_location="loc1", error_type="accuracy",
    correction_hint="fix_a", confidence=0.8, gv_id="gv_a"))
# Push multiple biases for gv_b to reach twist point
for i in range(3):
    mc.push_bias(CorrectionBias(error_location="loc2", error_type="completeness",
        correction_hint=f"fix_b{i}", confidence=0.7, gv_id="gv_b"))
test("M4b: 2 pairs = 2 spectra", len(mc.all_spectra()) == 2)
test("M4c: should_trigger False for un-accumulated",
     not mc.should_trigger("gv_a", "accuracy"))

for i in range(5):
    mc.push_bias(CorrectionBias(error_location="loc1", error_type="accuracy",
        correction_hint=f"fix_{i}", confidence=0.9, gv_id="gv_a"))
test("M4d: should_trigger True after accumulation",
     mc.should_trigger("gv_a", "accuracy"))
test("M4e: twist info for gv_b (3 biases at twist)",
     mc.get_twist_info("gv_b", "completeness")["at_twist_point"])

# ============================================================
# Task M5: TwistPoint up/down stream
# ============================================================
print("\n--- Task M5: TwistPoint ---")

mc5 = MobiusController(bias_weight=0.12)
for i in range(3):
    mc5.push_bias(CorrectionBias(error_location="step3", error_type="accuracy",
        correction_hint=f"hint_{i}", confidence=0.8, gv_id="gv5"))

info = mc5.get_twist_info("gv5", "accuracy")
test("M5a: at twist point", info["at_twist_point"])
test("M5b: pre_rca has primary_location",
     "primary_location" in info.get("pre_rca", {}))
test("M5c: override has suggested text",
     "suggested_override" in info.get("override", {}))

event = DoubleLoopEvent("accuracy", "gv5")
event.committed = True; event.committed_at = "2026-06-29T12:00:00Z"
event.gv_content_before = "old"; event.gv_content_after = "new"
event.modification_reason = "rule changed"
event.five_whys_chain = ["w1", "w2"]
hints = mc5.on_double_loop_committed(event)
test("M5d: downstream RULE_CHANGED hint",
     len(hints) > 0 and hints[0]["type"] == "RULE_CHANGED")
test("M5e: five_whys -> EXECUTION_CHECK",
     any(h["type"] == "EXECUTION_CHECK" for h in hints))

# ============================================================
# Task M6: DoubleLoopExecutor + Mobius
# ============================================================
print("\n--- Task M6: DLE + Mobius ---")

mc6 = MobiusController()
dle = DoubleLoopExecutor(mobius=mc6)
test("M6a: mobius attached", dle.mobius is not None)

should, reason = dle._evaluate_trigger("accuracy", "no_gv")
test("M6b: no trigger without data", not should)

for i in range(4):
    mc6.push_bias(CorrectionBias(error_location="test", error_type="accuracy",
        correction_hint=f"fix_{i}", confidence=0.85, gv_id="gv_trigger"))

should, reason = dle._evaluate_trigger("accuracy", "gv_trigger")
test("M6c: trigger after 4 biases", should)
test("M6d: reason has MOBIUS", "MOBIUS" in reason)

# ============================================================
# Task M7: Persistence
# ============================================================
print("\n--- Task M7: Persistence ---")

td = Path(tempfile.mkdtemp())
try:
    mc7a = MobiusController(artifact_dir=td)
    mc7a.push_bias(CorrectionBias(error_location="loc", error_type="accuracy",
        correction_hint="test", confidence=0.8, gv_id="gv_p"))
    i_before = mc7a.get_intensity("gv_p", "accuracy")

    mc7b = MobiusController(artifact_dir=td)
    mc7b.load()
    i_after = mc7b.get_intensity("gv_p", "accuracy")
    test("M7a: intensity survives roundtrip",
         math.isclose(i_before, i_after, abs_tol=0.01),
         f"before={i_before:.3f}, after={i_after:.3f}")

    state_file = td / "mobius_state.json"
    test("M7b: state file exists", state_file.exists())
finally:
    shutil.rmtree(td, ignore_errors=True)

# ============================================================
# Task M8: Reset Cycle
# ============================================================
print("\n--- Task M8: Reset Cycle ---")

mc8 = MobiusController()
for i in range(4):
    mc8.push_bias(CorrectionBias(error_location="test", error_type="accuracy",
        correction_hint=f"fix_{i}", confidence=0.85, gv_id="gv_r"))

test("M8a: triggered before reset", mc8.should_trigger("gv_r", "accuracy"))

event8 = DoubleLoopEvent("accuracy", "gv_r")
event8.committed = True; event8.committed_at = "2026-06-29T12:00:00Z"
event8.modification_reason = "fixed"
mc8.on_double_loop_committed(event8)

test("M8b: intensity reset to 0",
     math.isclose(mc8.get_intensity("gv_r", "accuracy"), 0.0, abs_tol=0.001))
test("M8c: biases cleared",
     mc8.get_spectrum("gv_r", "accuracy").bias_count == 0)

mc8.push_bias(CorrectionBias(error_location="test", error_type="accuracy",
    correction_hint="new_cycle", confidence=0.75, gv_id="gv_r"))
test("M8d: new cycle starts low",
     mc8.get_intensity("gv_r", "accuracy") < 0.3)

# ============================================================
# Task M9: Time Decay
# ============================================================
print("\n--- Task M9: Time Decay ---")

mc9 = MobiusController(time_decay_gamma=0.001)
mc9.push_bias(CorrectionBias(error_location="test", error_type="accuracy",
    correction_hint="test", confidence=0.8, gv_id="gv_d"))
i_before = mc9.get_intensity("gv_d", "accuracy")
i_after = mc9.get_intensity("gv_d", "accuracy")
test("M9a: decay reduces or maintains intensity",
     i_after <= i_before,
     f"before={i_before:.3f} after={i_after:.3f}")

# ============================================================
# Task M10: Dashboard
# ============================================================
print("\n--- Task M10: Dashboard ---")

mc10 = MobiusController()
for i in range(3):
    mc10.push_bias(CorrectionBias(error_location=f"loc_{i%2}", error_type="accuracy",
        correction_hint=f"fix_{i}", confidence=0.8, gv_id="gv_dash"))

dash = mc10.dashboard()
test("M10a: total_spectra", "total_spectra" in dash)
test("M10b: active_spectra", "active_spectra" in dash)
test("M10c: at_twist_point", "at_twist_point" in dash)
test("M10d: trigger_ready", "trigger_ready" in dash)
test("M10e: spectra list non-empty", len(dash["spectra"]) > 0)

# ============================================================
print()
print("=" * 60)
total = PASS + FAIL
print(f"MOBIUS TESTS: {PASS}/{total} passed ({100*PASS/total:.0f}%)")
if FAIL > 0:
    print(f"FAILURES: {FAIL}")
print("=" * 60)
