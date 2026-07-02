"""Tests for kugua.negentropy v0.3 — pure entropy functions, dimensions, history."""
import unittest
import tempfile
from pathlib import Path

from kugua.negentropy import (
    permutation_entropy, shannon_entropy, binary_entropy,
    _compute_process_order, _compute_intent_anchoring,
    _compute_knowledge_efficacy, _compute_information_fidelity,
    _compute_double_loop_efficacy,
    Negentropy, NegentropyHistory, NegentropySnapshot,
    generate_dashboard, generate_integrity_report, DEFAULT_WEIGHTS,
)


class TestPermutationEntropy(unittest.TestCase):
    def test_monotonic_sequence(self):
        """Perfectly monotonic → minimum entropy = 0."""
        H = permutation_entropy([1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(H, 0.0)

    def test_random_sequence(self):
        """Disordered sequence → positive entropy."""
        H = permutation_entropy([1, 5, 2, 8, 3, 7, 4, 6])
        self.assertGreater(H, 0.3)

    def test_string_sequence(self):
        """String sequence works via rank mapping."""
        H = permutation_entropy(["P0", "P1", "P2", "P3", "P4", "P5"])
        self.assertEqual(H, 0.0)

    def test_short_sequence(self):
        """Too-short sequence → 0."""
        H = permutation_entropy([1, 2])
        self.assertEqual(H, 0.0)

    def test_constant_sequence(self):
        """All same → 0 (no pattern variation)."""
        H = permutation_entropy([5, 5, 5, 5, 5])
        self.assertEqual(H, 0.0)


class TestShannonEntropy(unittest.TestCase):
    def test_uniform(self):
        H = shannon_entropy(["a", "b", "c", "a", "b", "c"])
        self.assertAlmostEqual(H, 1.0, places=1)

    def test_skewed(self):
        H = shannon_entropy(["a", "a", "a", "a", "b"])
        self.assertLess(H, 0.8)

    def test_single_category(self):
        H = shannon_entropy(["x", "x", "x"])
        self.assertEqual(H, 0.0)

    def test_empty(self):
        H = shannon_entropy([])
        self.assertEqual(H, 0.0)


class TestBinaryEntropy(unittest.TestCase):
    def test_max_uncertainty(self):
        self.assertAlmostEqual(binary_entropy(0.5), 1.0, places=1)

    def test_certain_success(self):
        self.assertEqual(binary_entropy(1.0), 0.0)

    def test_certain_failure(self):
        self.assertEqual(binary_entropy(0.0), 0.0)

    def test_moderate(self):
        H = binary_entropy(0.9)
        self.assertLess(H, 0.5)


class TestDimensions(unittest.TestCase):
    def test_process_order_ordered(self):
        h = [{"to": "P0"}, {"to": "P1"}, {"to": "P2"}, {"to": "P3"}]
        score = _compute_process_order(h)
        self.assertGreaterEqual(score, 95.0)

    def test_process_order_regression(self):
        h = [{"to": "P0"}, {"to": "P1"}, {"to": "P0"}, {"to": "P1"}, {"to": "P0"}]
        score = _compute_process_order(h)
        self.assertLess(score, 100.0)

    def test_intent_anchoring_no_changes(self):
        score = _compute_intent_anchoring(0, 10)
        self.assertEqual(score, 100.0)

    def test_intent_anchoring_frequent(self):
        score = _compute_intent_anchoring(5, 10)
        self.assertEqual(score, 50.0)

    def test_knowledge_efficacy_empty(self):
        score = _compute_knowledge_efficacy({})
        self.assertEqual(score, 100.0)

    def test_info_fidelity_efficient(self):
        score = _compute_information_fidelity(3, 20)
        self.assertEqual(score, 100.0)

    def test_info_fidelity_overused(self):
        score = _compute_information_fidelity(500, 20)
        self.assertLess(score, 40.0)

    def test_double_loop_perfect(self):
        score = _compute_double_loop_efficacy(10, 0)
        self.assertEqual(score, 100.0)

    def test_double_loop_awful(self):
        score = _compute_double_loop_efficacy(0, 10)
        self.assertEqual(score, 0.0)

    def test_double_loop_insufficient(self):
        score = _compute_double_loop_efficacy(1, 1)
        self.assertEqual(score, 50.0)


class TestNegentropy(unittest.TestCase):
    def setUp(self):
        self.state = {
            "phase_history": [
                {"to": "P0_ready"}, {"to": "P1_planned"},
                {"to": "P2_executed"}, {"to": "P3_reviewed"},
            ],
            "phase_regressions": 0, "phase_switches": 3,
            "anchor_changes": 1, "total_subtasks": 10,
            "stagnation_events": 0, "retrieve_calls": 15,
        }

    def test_composite_in_range(self):
        ne = Negentropy(self.state)
        c = ne.composite()
        self.assertGreaterEqual(c, 0.0)
        self.assertLessEqual(c, 100.0)

    def test_to_dict_keys(self):
        ne = Negentropy(self.state)
        d = ne.to_dict()
        for key in ("composite", "process_order", "intent_anchoring",
                     "knowledge_efficacy", "information_fidelity",
                     "double_loop_efficacy"):
            self.assertIn(key, d)

    def test_breakdown(self):
        ne = Negentropy(self.state)
        bd = ne.breakdown()
        self.assertIn("composite", bd)
        self.assertIn("dimensions", bd)
        self.assertEqual(len(bd["dimensions"]), 5)

    def test_custom_weights(self):
        w = {"process_order": 1.0, "intent_anchoring": 0.0,
             "knowledge_efficacy": 0.0, "information_fidelity": 0.0,
             "double_loop_efficacy": 0.0}
        ne = Negentropy(self.state, weights=w)
        self.assertEqual(ne.composite(), ne.process_order())

    def test_dashboard_html(self):
        ne = Negentropy(self.state)
        html = generate_dashboard(ne)
        self.assertIn("DOCTYPE", html)
        self.assertIn("<table>", html)
        self.assertGreater(len(html), 2000)

    def test_integrity_report(self):
        ne = Negentropy(self.state)
        report = generate_integrity_report(ne)
        self.assertIn("COMPOSITE:", report)
        self.assertIn("PO:", report)


class TestNegentropyHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.hist = NegentropyHistory(artifacts_dir=self.tmp)
        self.snap_dict = {
            "composite": 85.0,
            "process_order": 90.0,
            "intent_anchoring": 88.0,
            "knowledge_efficacy": 70.0,
            "information_fidelity": 95.0,
            "double_loop_efficacy": 60.0,
            "weights": dict(DEFAULT_WEIGHTS),
        }

    def test_record_and_query(self):
        self.hist.record(self.snap_dict)
        self.assertEqual(len(self.hist.recent()), 1)
        self.assertEqual(self.hist.latest.composite, 85.0)

    def test_delta(self):
        self.hist.record(self.snap_dict)
        self.hist.record({**self.snap_dict, "composite": 80.0})
        self.assertEqual(self.hist.delta(), -5.0)

    def test_no_degrading_flat(self):
        for _ in range(5):
            self.hist.record(self.snap_dict)
        self.assertFalse(self.hist.is_degrading())

    def test_degrading_detected(self):
        for c in [85, 82, 78, 73]:
            self.hist.record({**self.snap_dict, "composite": float(c)})
        self.assertTrue(self.hist.is_degrading())

    def test_trend_positive(self):
        for c in [80, 82, 85, 88, 90]:
            self.hist.record({**self.snap_dict, "composite": float(c)})
        self.assertGreater(self.hist.trend(), 0.0)

    def test_persistence(self):
        self.hist.record(self.snap_dict)
        hist2 = NegentropyHistory(artifacts_dir=self.tmp)
        self.assertEqual(len(hist2.recent()), 1)

    def test_to_dict(self):
        self.hist.record(self.snap_dict)
        d = self.hist.to_dict()
        self.assertIn("count", d)
        self.assertIn("latest_composite", d)


if __name__ == "__main__":
    unittest.main()
