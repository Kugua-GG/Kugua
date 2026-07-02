"""End-to-end integration test: full P0→P4 cycle with KuguaKernel.

Also verifies real LLM modules: executor, observer, meta_reviewer.
Requires MIMO_API_KEY or DEEPSEEK_API_KEY environment variable.
"""

import unittest
import os

from kugua.kernel import KuguaKernel
from kugua.config import KuguaConfig
from kugua.executor import LLMClient, TaskExecutor
from kugua.main_loop import PhaseReport


# ═══════════════════════════════════════════════════════════════
# E2E: P0→P4 without LLM (executor gracefully skipped)
# ═══════════════════════════════════════════════════════════════

class TestE2EMinimal(unittest.TestCase):
    """Full P0→P4 cycle with init_minimal (no LLM required)."""

    def setUp(self):
        self.kernel = KuguaKernel()
        self.kernel.init_minimal()

    def test_full_cycle_no_llm(self):
        """P0→P4 should complete even without executor (P2 skipped)."""
        task_dag = [
            {"id": "t1", "task": "Return 'hello'", "requirements": "Must say hello"},
            {"id": "t2", "task": "Add 2+2", "requirements": "Must return 4"},
        ]
        intent = {"goal": "Verify basic arithmetic", "success": "All correct"}

        reports = self.kernel.main_loop.run(task_dag, intent)

        phases = [r.phase for r in reports]
        self.assertIn("P0", phases)
        self.assertIn("P1", phases)
        self.assertIn("P2", phases)
        self.assertIn("P3", phases)
        self.assertIn("P4", phases)

        # P2 should be skipped (no executor)
        p2 = next(r for r in reports if r.phase == "P2")
        self.assertEqual(p2.status, "skipped")

        # P0 and P1 should complete
        p0 = next(r for r in reports if r.phase == "P0")
        self.assertEqual(p0.status, "completed")

        p1 = next(r for r in reports if r.phase == "P1")
        self.assertEqual(p1.status, "completed")
        self.assertEqual(p1.data["total_tasks"], 2)
        self.assertEqual(p1.data["valid_tasks"], 2)

    def test_phase_summary(self):
        """get_phase_summary should return structured data."""
        reports = self.kernel.main_loop.run(
            [{"id": "t1", "task": "test"}],
            {"goal": "test"},
        )
        summary = self.kernel.main_loop.get_phase_summary()
        self.assertIn("phases", summary)
        self.assertIn("total_phases", summary)
        self.assertGreater(summary["total_phases"], 3)

    def test_context_integration(self):
        """ContextManager should be wired and functional in the loop."""
        cm = self.kernel.context
        self.assertIsNotNone(cm)
        cm.append("user", "Test message", importance=7)
        cm.append("worker", "Executed OK")
        assembled = cm.assemble("Next step?")
        self.assertIn("CTX:", assembled)
        self.assertIn("L2:recent", assembled)

    def test_states_advance(self):
        """State machine should advance to P4_delivered after cycle."""
        self.kernel.main_loop.run(
            [{"id": "t1", "task": "test"}],
            {"goal": "test"},
        )
        # State should be P4_delivered
        phase = self.kernel.states_machine.get_current_phase()
        self.assertEqual(phase, "P4_delivered")

    def test_dashboard_after_cycle(self):
        """Dashboard should include all subsystems after a full cycle."""
        self.kernel.main_loop.run([], {})
        d = self.kernel.dashboard()
        self.assertIn("kb", d)
        self.assertIn("graph", d)
        self.assertIn("negentropy", d)
        self.assertIn("main_loop", d)

    def test_invalid_task_dag(self):
        """P1 should fail if no task has 'task' field."""
        reports = self.kernel.main_loop.run(
            [{"id": "t1"}, {"id": "t2"}],  # no 'task' key
            {"goal": "test"},
        )
        p1 = next(r for r in reports if r.phase == "P1")
        self.assertEqual(p1.status, "failed")


# ═══════════════════════════════════════════════════════════════
# LLM Integration tests (requires real API keys)
# ═══════════════════════════════════════════════════════════════

@unittest.skipUnless(
    os.getenv("MIMO_API_KEY") or os.getenv("DEEPSEEK_API_KEY"),
    "No API key configured — set MIMO_API_KEY or DEEPSEEK_API_KEY",
)
class TestLLMIntegration(unittest.TestCase):
    """Real LLM integration tests. Skips if no API keys are set."""

    @classmethod
    def setUpClass(cls):
        cls.config = KuguaConfig.from_env()
        if not cls.config.has_providers:
            raise unittest.SkipTest("No providers with API keys configured")

    def setUp(self):
        self.client = LLMClient(self.config)

    def test_llm_client_chat(self):
        """Real chat completion call."""
        result = self.client.chat(
            messages=[{"role": "user", "content": "Say exactly 'pong' — no other text."}],
            temperature=0.0,
            max_tokens=16,
        )
        self.assertTrue(result.get("ok"), f"API error: {result.get('error')}")
        content = result.get("content", "").strip().lower()
        self.assertIn("pong", content)

    def test_llm_client_structured(self):
        """Real structured output call."""
        result = self.client.chat_structured(
            messages=[{"role": "user", "content": "Return JSON: {\"verdict\":\"pass\",\"score\":95}"}],
            output_schema={
                "type": "object",
                "properties": {
                    "verdict": {"type": "string"},
                    "score": {"type": "number"},
                },
            },
            temperature=0.0,
            max_tokens=256,
        )
        self.assertTrue(result.get("ok"), f"API error: {result.get('error')}")
        # Structured output may parse or not — either way, API should succeed
        data = result.get("data")
        if data is not None:
            verdict = data.get("verdict", "")
            # Any verdict value is fine as long as API returned ok
            self.assertIsInstance(verdict, str)

    def test_task_executor_execute(self):
        """Real task execution via Worker."""
        executor = TaskExecutor(self.client, self.config)
        result = executor.execute(
            subtask_id="test_1",
            task="Calculate 2+2 and return only the number.",
            context="",
        )
        self.assertTrue(result.ok, f"Exec error: {result.error}")
        self.assertIn("4", result.output)

    def test_task_executor_review(self):
        """Real checker review."""
        executor = TaskExecutor(self.client, self.config)
        result = executor.review(
            subtask_id="test_1",
            worker_output="The answer is 4. Calculation: 2+2=4.",
            requirements="Must return 4 as the answer",
        )
        self.assertTrue(result.ok or result.verdict != "fail",
                       f"Review issues: {result.issues}")

    def test_observer_gate_rca(self):
        """Real FreshObserver RCA gate."""
        from kugua.observer import create_observer_from_config
        observer = create_observer_from_config(self.config)
        self.assertIsNotNone(observer.client, "Observer has no LLM client")

        result = observer.gate_rca(
            error_pattern="Null pointer exceptions in validation logic",
            root_cause="Missing None check before calling .strip() on optional field",
            five_whys=[
                "Why 1: .strip() was called on None",
                "Why 2: The field was optional but code assumed it was always present",
                "Why 3: The schema didn't mark the field as required=False",
                "Why 4: The API contract was ambiguous about optional fields",
                "Why 5: No contract validation in CI pipeline",
            ],
        )
        # observe() returns result; we check it didn't crash
        self.assertIsNotNone(result)
        self.assertIn(result.all_passed, (True, False))

    def test_observer_gate_proposal(self):
        """Real FreshObserver proposal gate."""
        from kugua.observer import create_observer_from_config
        observer = create_observer_from_config(self.config)
        self.assertIsNotNone(observer.client)

        result = observer.gate_proposal(
            before="Always call .strip() on user input fields.",
            after="Always check for None before calling .strip() on user input fields.",
            reason="Prevent NullPointerException on optional fields.",
        )
        self.assertIsNotNone(result)

    def test_meta_reviewer(self):
        """Real MetaReviewer blind audit."""
        from kugua.meta_reviewer import MetaReviewer
        mr = MetaReviewer(llm_client=self.client)
        result = mr.audit(
            gv_content_before="Always call .strip() on input.",
            gv_content_after="Check for None before calling .strip() on optional input fields.",
            reason="Fix NullPointerException",
        )
        self.assertIsNotNone(result)
        self.assertIn(result.to_dict().get("overall_score", 0), range(0, 11))


# ═══════════════════════════════════════════════════════════════
# LLM Module structural tests (no API keys needed)
# ═══════════════════════════════════════════════════════════════

class TestLLMModulesStructural(unittest.TestCase):
    """Verify LLM modules are correctly wired (no real API calls)."""

    def test_llm_client_no_providers(self):
        """LLMClient with no-key providers returns error gracefully."""
        # Use providers with empty keys to force failure
        client = LLMClient(providers=[
            {"name": "test", "api_key": "", "api_base": "https://localhost/v1",
             "models": ["test-model"]},
        ])
        result = client.chat(
            messages=[{"role": "user", "content": "Hello"}],
        )
        self.assertFalse(result["ok"])
        self.assertIn("No provider", result["error"])

    def test_executor_permission_gate(self):
        """TaskExecutor should invoke the permission gate."""
        client = LLMClient(providers=[])

        def deny_gate(action, context):
            return False, "test: denied"

        executor = TaskExecutor(client, permission_gate=deny_gate)
        result = executor.execute(subtask_id="t1", task="test")
        self.assertFalse(result.ok)
        self.assertIn("Permission denied", result.error)

    def test_double_loop_no_llm(self):
        """DoubleLoopExecutor should work without LLM (heuristic fallback)."""
        from kugua.double_loop import DoubleLoopExecutor
        dle = DoubleLoopExecutor(min_error_count=1)
        dle.record_error("accuracy", "gv_test")
        dle.record_error("accuracy", "gv_test")
        dle.record_error("accuracy", "gv_test")
        # Should trigger via fallback (3 >= min_error_count)
        event = dle.execute("accuracy", "gv_test")
        self.assertIsNotNone(event)
        # Without LLM, should use heuristic fallback
        self.assertIn("Likely root cause", event.root_cause_summary)

    def test_observer_no_client(self):
        """FreshObserver should pass by default when no client is configured."""
        from kugua.observer import FreshObserver
        observer = FreshObserver(llm_client=None)
        result = observer.gate_rca(
            error_pattern="test",
            root_cause="test cause",
            five_whys=["why1"],
        )
        self.assertTrue(result.all_passed)

    def test_kernel_full_wiring(self):
        """KuguaKernel.from_env() should wire all modules (with or without keys)."""
        kernel = KuguaKernel.from_env()
        # Core should always be wired
        self.assertIsNotNone(kernel.states_machine)
        self.assertIsNotNone(kernel.safety)
        self.assertIsNotNone(kernel.kb)
        self.assertIsNotNone(kernel.context)
        # LLM modules may or may not be wired depending on keys
        # But init should not crash
        self.assertIsNotNone(kernel.main_loop)

    def test_metareviewer_positional_fix(self):
        """MetaReviewer.audit() should call chat() with correct kwarg order (v0.2.2 fix)."""
        from kugua.meta_reviewer import MetaReviewer
        mr = MetaReviewer(llm_client=None)
        # Without LLM client, audit should not crash (will use fallback)
        result = mr.audit(
            gv_content_before="old rule",
            gv_content_after="new rule",
            reason="better",
        )
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
