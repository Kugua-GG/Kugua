"""Tests for kugua.config — KuguaConfig."""
import unittest
import os
from pathlib import Path

from kugua.config import KuguaConfig


class TestKuguaConfig(unittest.TestCase):
    def test_default_config(self):
        cfg = KuguaConfig()
        self.assertIsInstance(cfg.artifacts_dir, Path)
        self.assertFalse(cfg.has_providers)

    def test_get_artifacts_path(self):
        cfg = KuguaConfig()
        p = cfg.get_artifacts_path("test.json")
        self.assertTrue(str(p).endswith("test.json"))

    def test_get_observer_provider_empty(self):
        cfg = KuguaConfig()
        self.assertIsNone(cfg.get_observer_provider())

    def test_get_observer_provider_with_mimo(self):
        cfg = KuguaConfig()
        cfg.providers = [
            {"name": "mimo", "api_key": "test_key", "models": ["mimo-v2-flash"]},
        ]
        obs = cfg.get_observer_provider()
        self.assertIsNotNone(obs)
        self.assertEqual(obs["name"], "mimo")
        self.assertEqual(obs["api_key"], "test_key")

    def test_get_observer_provider_fallback(self):
        cfg = KuguaConfig()
        cfg.providers = [
            {"name": "deepseek", "api_key": "test_key", "models": ["deepseek-chat"]},
        ]
        obs = cfg.get_observer_provider()
        self.assertIsNotNone(obs)
        self.assertEqual(obs["name"], "deepseek")

    def test_has_providers_with_key(self):
        cfg = KuguaConfig()
        cfg.providers = [{"name": "test", "api_key": "key123"}]
        self.assertTrue(cfg.has_providers)

    def test_has_providers_empty_key(self):
        cfg = KuguaConfig()
        cfg.providers = [{"name": "test", "api_key": ""}]
        self.assertFalse(cfg.has_providers)

    def test_from_env_empty(self):
        """from_env should work even without env vars (returns empty config)."""
        cfg = KuguaConfig.from_env()
        self.assertIsInstance(cfg, KuguaConfig)


if __name__ == "__main__":
    unittest.main()
