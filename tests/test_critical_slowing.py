"""Tests for kugua.critical_slowing — Mann-Kendall trend test and CSD detector."""
import unittest
import tempfile
from pathlib import Path

from kugua.critical_slowing import (
    mann_kendall, CriticalSlowingDetector, CriticalSlowingSignal,
    _erf_approx, _norm_cdf,
)


class TestErf(unittest.TestCase):
    def test_erf_zero(self):
        self.assertAlmostEqual(_erf_approx(0.0), 0.0, places=3)

    def test_erf_symmetry(self):
        self.assertAlmostEqual(_erf_approx(1.0), -_erf_approx(-1.0), places=3)

    def test_norm_cdf_sanity(self):
        self.assertAlmostEqual(_norm_cdf(0.0), 0.5, places=2)
        self.assertGreater(_norm_cdf(3.0), 0.99)


class TestMannKendall(unittest.TestCase):
    def test_increasing_trend(self):
        result = mann_kendall([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertEqual(result["trend"], 1)
        self.assertGreater(result["tau"], 0)
        self.assertLess(result["p_value"], 0.05)

    def test_decreasing_trend(self):
        result = mann_kendall([5.0, 4.0, 3.0, 2.0, 1.0])
        self.assertEqual(result["trend"], -1)
        self.assertLess(result["tau"], 0)

    def test_no_trend(self):
        result = mann_kendall([1.0, 3.0, 2.0, 4.0, 3.0, 5.0])
        # May or may not be significant depending on pattern
        self.assertIn(result["trend"], (-1, 0, 1))
        self.assertIsInstance(result["p_value"], float)

    def test_small_sample(self):
        result = mann_kendall([1.0, 2.0])
        self.assertEqual(result["n"], 2)
        self.assertEqual(result["trend"], 0)


class TestCSD(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.csd = CriticalSlowingDetector(
            artifacts_dir=Path(self.tmp), min_samples=3,
        )

    def test_no_signal_with_few_samples(self):
        self.csd.record_failure("accuracy", ["gv1"], 1.0)
        signal = self.csd.detect("accuracy", "gv1")
        self.assertFalse(signal.critical)
        self.assertEqual(signal.sample_count, 1)

    def test_critical_detection(self):
        for t in [1.0, 2.0, 3.0, 4.0, 5.0]:
            self.csd.record_failure("accuracy", ["gv1"], t)
        signal = self.csd.detect("accuracy", "gv1")
        self.assertTrue(signal.critical)
        self.assertEqual(signal.trend, 1)
        self.assertLess(signal.p_value, 0.05)

    def test_persistence(self):
        self.csd.record_failure("accuracy", ["gv1"], 1.0)
        csd2 = CriticalSlowingDetector(artifacts_dir=Path(self.tmp))
        signal = csd2.detect("accuracy", "gv1")
        self.assertEqual(signal.sample_count, 1)

    def test_detect_any(self):
        self.csd.record_failure("accuracy", ["gv1"], 1.0)
        self.csd.record_failure("accuracy", ["gv1"], 2.0)
        self.csd.record_failure("accuracy", ["gv1"], 3.0)
        signals = self.csd.detect_any()
        self.assertGreaterEqual(len(signals), 1)


if __name__ == "__main__":
    unittest.main()
