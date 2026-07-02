"""Tests for kugua.kernel — KuguaKernel unified runtime."""
import unittest

from kugua.config import KuguaConfig
from kugua.kernel import KuguaKernel


class TestKuguaKernelMinimal(unittest.TestCase):
    """Tests for init_minimal (no LLM required)."""

    def setUp(self):
        self.kernel = KuguaKernel()
        self.kernel.init_minimal()

    def test_init_minimal(self):
        self.assertTrue(self.kernel.is_ready())
        self.assertIsNotNone(self.kernel.states_machine)
        self.assertIsNotNone(self.kernel.safety)
        self.assertIsNotNone(self.kernel.kb)
        self.assertIsNotNone(self.kernel.graph)

    def test_kb_has_entries(self):
        """KnowledgeBase should load constants on init."""
        self.assertGreaterEqual(len(self.kernel.kb.entries), 0)

    def test_dashboard(self):
        d = self.kernel.dashboard()
        self.assertIn("version", d)
        self.assertIn("kb", d)
        self.assertIn("graph", d)

    def test_kb_stats(self):
        d = self.kernel.dashboard()
        kb = d["kb"]
        self.assertIn("total", kb)
        self.assertIn("active", kb)

    def test_state_machine_ready(self):
        sm = self.kernel.states_machine
        self.assertIn(sm.get_current_phase(), ("P0_init", "P0_ready"))


class TestKuguaKernelUninitialized(unittest.TestCase):
    """Tests for kernel before any init."""

    def setUp(self):
        self.kernel = KuguaKernel()

    def test_not_ready_before_init(self):
        self.assertFalse(self.kernel.is_ready())

    def test_dashboard_empty(self):
        d = self.kernel.dashboard()
        self.assertEqual(d["version"], "0.2.1")
        # No subsystems initialized
        self.assertNotIn("kb", d)


class TestKuguaKernelFactory(unittest.TestCase):
    """Test KuguaKernel.from_env() factory."""

    def test_from_env_returns_kernel(self):
        kernel = KuguaKernel.from_env()
        self.assertIsInstance(kernel, KuguaKernel)
        self.assertTrue(kernel.is_ready())
        # from_env inits all 4 stages
        self.assertIsNotNone(kernel.main_loop)
        self.assertIsNotNone(kernel.ne)


if __name__ == "__main__":
    unittest.main()
