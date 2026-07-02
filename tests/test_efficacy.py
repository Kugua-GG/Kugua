"""Tests for kugua.efficacy — double-loop efficacy tracker."""
import unittest
import tempfile
from pathlib import Path

from kugua.efficacy import DoubleLoopEfficacyTracker


class TestEfficacyTracker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tracker = DoubleLoopEfficacyTracker(artifacts_dir=Path(self.tmp))

    def test_start_baseline(self):
        eid = self.tracker.start_baseline("accuracy", "gv1")
        self.assertTrue(eid)
        self.assertEqual(self.tracker.pending_count, 1)

    def test_full_lifecycle(self):
        eid = self.tracker.start_baseline("accuracy", "gv1")
        self.assertTrue(self.tracker.mark_modified(eid))
        self.assertTrue(self.tracker.record_outcome(
            eid, success=True, entropy_delta=0.3,
        ))
        self.assertEqual(self.tracker.verified_count, 1)
        self.assertEqual(self.tracker.pending_count, 0)

    def test_reverted_event(self):
        eid = self.tracker.start_baseline("accuracy", "gv1")
        self.tracker.mark_modified(eid)
        self.tracker.record_outcome(eid, success=False, entropy_delta=0.0)
        self.assertEqual(self.tracker.reverted_count, 1)
        self.assertEqual(self.tracker.verified_count, 0)

    def test_multiple_events(self):
        for i in range(3):
            eid = self.tracker.start_baseline("accuracy", f"gv{i}")
            self.tracker.mark_modified(eid)
            self.tracker.record_outcome(eid, success=True, entropy_delta=0.1)
        self.assertEqual(self.tracker.verified_count, 3)

    def test_entropy_reduction(self):
        eid = self.tracker.start_baseline("accuracy", "gv1")
        self.tracker.mark_modified(eid)
        self.tracker.record_outcome(eid, success=True, entropy_delta=0.5)
        self.assertGreater(self.tracker.total_entropy_reduction, 0.0)

    def test_dashboard_dict(self):
        d = self.tracker.to_dict()
        self.assertIn("total_events", d)

    def test_persistence(self):
        self.tracker.start_baseline("accuracy", "gv1")
        t2 = DoubleLoopEfficacyTracker(artifacts_dir=Path(self.tmp))
        self.assertEqual(t2.pending_count, 1)


if __name__ == "__main__":
    unittest.main()
