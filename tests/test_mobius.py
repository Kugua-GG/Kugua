"""Tests for kugua.mobius — correction spectrum and controller."""
import unittest
import tempfile
from pathlib import Path

from kugua.mobius import (
    CorrectionBias, CorrectionSpectrum, MobiusController,
    TwistPoint, LEVEL_L3_MAX,
)


class TestCorrectionBias(unittest.TestCase):
    def test_create_bias(self):
        bias = CorrectionBias(
            error_location="test.py:10",
            error_type="accuracy",
            correction_hint="Check for None before calling",
            confidence=0.8,
            gv_id="gv_null_check",
        )
        self.assertTrue(bias.bias_id.startswith("bias_"))
        self.assertTrue(bias.to_prompt_fragment())

    def test_empty_prompt_fragment(self):
        bias = CorrectionBias()
        self.assertEqual(bias.to_prompt_fragment(), "")

    def test_serialization(self):
        bias = CorrectionBias(
            error_location="x.py", error_type="completeness",
            correction_hint="Add validation", confidence=0.5, gv_id="gv1",
        )
        d = bias.to_dict()
        b2 = CorrectionBias.from_dict(d)
        self.assertEqual(b2.error_location, "x.py")
        self.assertEqual(b2.gv_id, "gv1")


class TestCorrectionSpectrum(unittest.TestCase):
    def test_empty_spectrum(self):
        s = CorrectionSpectrum(gv_id="gv1", error_type="accuracy")
        self.assertEqual(s.bias_count, 0)
        self.assertFalse(s.should_trigger_double_loop)

    def test_bias_accumulation(self):
        s = CorrectionSpectrum(gv_id="gv1", error_type="accuracy")
        for i in range(5):
            s.add_bias(CorrectionBias(
                error_location=f"loc{i}", error_type="accuracy",
                correction_hint=f"fix{i}", confidence=0.9, gv_id="gv1",
            ))
        self.assertEqual(s.bias_count, 5)
        self.assertGreater(s.intensity, 0.0)
        self.assertIn(s.current_level, ("L1_BIAS", "L2_OVERRIDE", "L3_CANDIDATE", "L4_COMMIT"))

    def test_decay(self):
        s = CorrectionSpectrum(gv_id="gv1", error_type="accuracy",
                              time_decay_gamma=0.01)  # fast decay
        for i in range(5):
            s.add_bias(CorrectionBias(
                error_location=f"loc{i}", error_type="accuracy",
                correction_hint=f"fix{i}", confidence=0.9, gv_id="gv1",
            ))
        old_intensity = s.intensity
        s.apply_decay()
        # After fast decay, intensity should drop
        self.assertLess(s.intensity, old_intensity)

    def test_reset(self):
        s = CorrectionSpectrum(gv_id="gv1", error_type="accuracy")
        s.add_bias(CorrectionBias(
            error_location="loc", error_type="accuracy",
            correction_hint="fix", confidence=0.9, gv_id="gv1",
        ))
        s.reset()
        self.assertEqual(s.bias_count, 0)


class TestMobiusController(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mc = MobiusController(artifact_dir=Path(self.tmp))

    def test_record_bias(self):
        bias = CorrectionBias(
            error_location="a.py", error_type="accuracy",
            correction_hint="fix", confidence=0.9, gv_id="gv1",
        )
        self.mc.record_bias(bias)
        intensity = self.mc.get_intensity("gv1", "accuracy")
        self.assertGreater(intensity, 0.0)

    def test_trigger_threshold(self):
        # Should not trigger with a single low-confidence bias
        self.mc.record_bias(CorrectionBias(
            error_location="a.py", error_type="accuracy",
            correction_hint="fix", confidence=0.3, gv_id="gv1",
        ))
        self.assertFalse(self.mc.should_trigger("gv1", "accuracy"))

    def test_dashboard(self):
        d = self.mc.dashboard()
        self.assertIn("total_spectra", d)

    def test_commit_and_reset(self):
        self.mc.record_bias(CorrectionBias(
            error_location="a.py", error_type="accuracy",
            correction_hint="fix", confidence=0.9, gv_id="gv1",
        ))
        self.mc.commit_and_reset("gv1", "accuracy")
        self.assertEqual(self.mc.get_intensity("gv1", "accuracy"), 0.0)

    def test_save_load(self):
        self.mc.record_bias(CorrectionBias(
            error_location="a.py", error_type="accuracy",
            correction_hint="fix", confidence=0.9, gv_id="gv1",
        ))
        mc2 = MobiusController(artifact_dir=Path(self.tmp))
        mc2.load_state()
        self.assertGreater(mc2.get_intensity("gv1", "accuracy"), 0.0)


if __name__ == "__main__":
    unittest.main()
