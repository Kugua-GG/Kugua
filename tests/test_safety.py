"""Tests for kugua.safety — SafetyManager, audit trail, kill switch."""
import unittest

from kugua.safety import (
    SafetyManager, AuditTrail, AuditEntry,
    TrustLevel, Incident, L1_TRUST, L5_TRUST, OPERATION_PERMISSIONS,
)


class TestAuditTrail(unittest.TestCase):
    def setUp(self):
        self.audit = AuditTrail(max_entries=100)

    def test_record_and_query(self):
        self.audit.record("read_file", True, "allowed", L1_TRUST)
        self.audit.record("rm_rf", False, "blocked", L5_TRUST)
        self.assertEqual(self.audit.to_dict()["total_checks"], 2)
        self.assertEqual(len(self.audit.recent_denials()), 1)

    def test_no_denials(self):
        self.audit.record("read_file", True, "allowed", L1_TRUST)
        self.assertEqual(len(self.audit.recent_denials()), 0)

    def test_max_entries(self):
        audit = AuditTrail(max_entries=5)
        for i in range(10):
            audit.record(f"op_{i}", True, "ok", L1_TRUST)
        self.assertLessEqual(audit.to_dict()["total_checks"], 5)

    def test_entry_to_dict(self):
        entry = AuditEntry(operation="test", allowed=True, reason="ok")
        d = entry.to_dict()
        self.assertEqual(d["operation"], "test")
        self.assertTrue(d["allowed"])


class TestSafetyManager(unittest.TestCase):
    def setUp(self):
        self.sf = SafetyManager()

    def test_read_allowed(self):
        ok, reason = self.sf.check_permission("read_file")
        self.assertTrue(ok)

    def test_l5_permanently_blocked(self):
        ok, reason = self.sf.check_permission("rm_rf")
        self.assertFalse(ok)
        self.assertIn("permanently prohibited", reason)

    def test_unknown_operation_allowed(self):
        ok, reason = self.sf.check_permission("some_custom_op")
        self.assertTrue(ok)

    def test_kill_switch_blocks_all(self):
        self.sf.kill_switch("test emergency")
        self.assertTrue(self.sf.emergency_stop_active)
        ok, reason = self.sf.check_permission("read_file")
        self.assertFalse(ok)
        self.assertIn("Emergency stop", reason)

    def test_log_and_query_incidents(self):
        inc = Incident(level="II", category="test", description="test inc")
        self.sf.log_incident(inc)
        results = self.sf.query_incidents(days=0)  # all
        self.assertEqual(len(results), 1)
        # Test time-filtered query
        filtered = self.sf.query_incidents(days=30)
        self.assertEqual(len(filtered), 1)

    def test_audit_trail_integrated(self):
        self.sf.check_permission("read_file")
        self.sf.check_permission("rm_rf")
        summary = self.sf.get_audit_summary()
        self.assertEqual(summary["total_checks"], 2)
        self.assertEqual(summary["total_denials"], 1)


if __name__ == "__main__":
    unittest.main()
