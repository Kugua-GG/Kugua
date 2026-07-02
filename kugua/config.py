"""KuguaConfig — minimal stub for package imports."""
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class KuguaConfig:
    """Configuration for kugua kernel."""
    artifacts_dir: Path = field(default_factory=lambda: Path(
        os.getenv("KUGUA_ARTIFACTS_DIR", str(Path.home() / ".claude" / ".codex" / "artifacts"))
    ))
    providers: List[Dict[str, Any]] = field(default_factory=list)
    debug: bool = False

    @property
    def has_providers(self) -> bool:
        return bool(self.providers and any(p.get("api_key") for p in self.providers))

    def get_artifacts_path(self, filename: str) -> Path:
        """Get full path for a file in the artifacts directory."""
        p = Path(self.artifacts_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p / filename

    def get_observer_provider(self) -> "Optional[Dict[str, Any]]":
        """Return a provider config suitable for the FreshObserver (prefers Mimo).

        Used by observer.create_observer_from_config().
        Returns None if no suitable provider is configured.
        """
        for p in self.providers:
            if p.get("name") == "mimo" and p.get("api_key"):
                return dict(p)
        # Fallback: any provider with an API key
        for p in self.providers:
            if p.get("api_key"):
                return dict(p)
        return None

    @classmethod
    def from_env(cls) -> "KuguaConfig":
        cfg = cls()
        deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
        if deepseek_key:
            cfg.providers.append({
                "name": "deepseek", "api_base": "https://api.deepseek.com/v1",
                "api_key": deepseek_key, "models": ["deepseek-chat", "deepseek-reasoner"],
            })
        mimo_key = os.getenv("MIMO_API_KEY", "")
        if mimo_key:
            cfg.providers.append({
                "name": "mimo",
                "api_base": os.getenv("MIMO_API_BASE", "https://api.xiaomimimo.com/v1"),
                "api_key": mimo_key, "models": ["mimo-v2-flash", "mimo-v2-pro"],
            })
        return cfg
