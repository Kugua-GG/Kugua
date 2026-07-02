"""
LLM Executor — multi-provider chat, structured output, task execution with permission gating.

Key features:
  - Multi-provider LLM client with automatic fallback
  - Mimo fix: reads both 'content' and 'reasoning_content' from API responses
  - TaskExecutor with permission gate (safety check before execution)
  - Stagnation detection for identifying stuck loops

Pure Python stdlib + urllib for HTTP — no external LLM SDK dependencies.
"""

import json
import os
import ssl
import time
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════
# Result types
# ═══════════════════════════════════════════════════════════════

@dataclass
class TaskResult:
    """Result from TaskExecutor.execute()."""

    ok: bool = False
    subtask_id: str = ""
    output: str = ""
    usage: Dict[str, int] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    model: str = ""
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "subtask_id": self.subtask_id,
            "output": self.output,
            "usage": self.usage,
            "elapsed_ms": self.elapsed_ms,
            "model": self.model,
            "error": self.error,
        }


@dataclass
class ReviewResult:
    """Result from TaskExecutor.review()."""

    ok: bool = False
    subtask_id: str = ""
    verdict: str = "pending"   # pass | fail | pending
    issues: List[str] = field(default_factory=list)
    score: float = 0.0
    usage: Dict[str, int] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    model: str = ""
    # Mobius: CorrectionBias generated on FAIL verdict
    correction_bias: Optional[Any] = None
    suspected_gv_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "subtask_id": self.subtask_id,
            "verdict": self.verdict,
            "issues": self.issues,
            "score": self.score,
            "usage": self.usage,
            "elapsed_ms": self.elapsed_ms,
            "model": self.model,
            "has_correction_bias": self.correction_bias is not None,
            "suspected_gv_ids": self.suspected_gv_ids,
        }


# ═══════════════════════════════════════════════════════════════
# LLMClient — multi-provider with fallback
# ═══════════════════════════════════════════════════════════════

# Default provider configurations
DEFAULT_PROVIDERS = [
    {
        "name": "deepseek",
        "api_base": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    {
        "name": "mimo",
        "api_base": "https://api.xiaomimimo.com/v1",
        "models": ["mimo-v2.5-pro", "mimo-v2.5", "mimo-v2-flash"],
        "api_key_env": "MIMO_API_KEY",
    },
    {
        "name": "openai",
        "api_base": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini"],
        "api_key_env": "OPENAI_API_KEY",
    },
]


class LLMClient:
    """Multi-provider LLM client with automatic fallback.

    Args:
        config: Optional KuguaConfig-like object with providers list.
                If None, uses DEFAULT_PROVIDERS and environment variables.
        providers: Optional list of provider dicts (overrides config).
    """

    def __init__(self, config: Any = None, providers: Optional[List[Dict]] = None):
        self._providers: List[Dict] = []

        if providers:
            self._providers = providers
        elif config and hasattr(config, "providers") and config.providers:
            self._providers = list(config.providers)
        else:
            self._providers = [dict(p) for p in DEFAULT_PROVIDERS]

        # Resolve API keys from environment
        for p in self._providers:
            if "api_key" not in p:
                env_var = p.get("api_key_env", "")
                p["api_key"] = os.getenv(env_var, "")
            if "api_base" not in p:
                p["api_base"] = "https://api.openai.com/v1"
            if "models" not in p:
                p["models"] = ["gpt-4o-mini"]

    @property
    def has_providers(self) -> bool:
        """Check if any provider has a configured API key."""
        return any(p.get("api_key") for p in self._providers)

    def _get_provider_for_model(self, model: Optional[str] = None) -> Optional[Dict]:
        """Find a provider that supports the given model."""
        if model:
            for p in self._providers:
                if model in p.get("models", []):
                    if p.get("api_key"):
                        return p
        # Fallback: first provider with an API key
        for p in self._providers:
            if p.get("api_key"):
                return p
        return None

    def chat(
        self,
        messages: Optional[List[Dict[str, str]]] = None,
        model: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        """Send a chat completion request.

        Args:
            messages: List of {"role": "...", "content": "..."} dicts.
            model: Model name. Auto-detected from providers if None.
            temperature: Sampling temperature.
            max_tokens: Maximum output tokens.
            timeout: Request timeout in seconds.

        Returns:
            dict with keys: content, model, usage, finish_reason, ok, error
        """
        if messages is None:
            messages = []

        provider = self._get_provider_for_model(model)
        if provider is None:
            return {"content": "", "model": model or "unknown", "usage": {},
                    "finish_reason": "error", "ok": False,
                    "error": "No provider with API key configured"}

        actual_model = model or provider["models"][0]
        return self._call_api(provider, actual_model, messages, temperature,
                              max_tokens, timeout)

    def chat_structured(
        self,
        messages: Optional[List[Dict[str, str]]] = None,
        model: Optional[str] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        """Send a chat request and parse structured JSON output.

        Appends a JSON-format instruction to the system prompt.
        Falls back to extracting JSON from the text response if the
        provider doesn't support native structured output.

        Returns:
            dict with keys: content (str), data (parsed dict or None),
                   model, usage, ok, error
        """
        if messages is None:
            messages = []

        # Inject JSON output instruction
        json_instruction = "You must respond with valid JSON only. No markdown, no explanation."
        if output_schema:
            schema_str = json.dumps(output_schema, ensure_ascii=False)
            json_instruction += f" Output must conform to this schema: {schema_str}"

        augmented_messages = list(messages)
        # If there's a system message, append to it; otherwise prepend one
        if augmented_messages and augmented_messages[0].get("role") == "system":
            augmented_messages[0] = dict(augmented_messages[0])
            augmented_messages[0]["content"] = (
                augmented_messages[0]["content"] + "\n\n" + json_instruction
            )
        else:
            augmented_messages.insert(0, {"role": "system", "content": json_instruction})

        result = self.chat(messages=augmented_messages, model=model,
                           temperature=temperature, max_tokens=max_tokens,
                           timeout=timeout)

        # Try to parse JSON from content
        data = None
        if result.get("ok") and result.get("content"):
            content = result["content"]
            data = self._extract_json(content)

        result["data"] = data
        return result

    def _call_api(
        self,
        provider: Dict[str, Any],
        model: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> Dict[str, Any]:
        """Low-level API call to a specific provider.

        Implements the Mimo fix: reads msg.get("content", "") OR
        msg.get("reasoning_content", "") from the response.
        """
        api_base = provider["api_base"].rstrip("/")
        api_key = provider.get("api_key", "")
        url = f"{api_base}/chat/completions"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

        try:
            ctx = ssl.create_default_context()
            start = time.perf_counter()
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                elapsed = (time.perf_counter() - start) * 1000
                body = json.loads(resp.read().decode("utf-8"))

            choice = body.get("choices", [{}])[0]
            msg = choice.get("message", {})

            # Mimo fix: check both 'content' and 'reasoning_content'
            content = msg.get("content", "") or msg.get("reasoning_content", "") or ""

            usage_raw = body.get("usage", {})
            usage = {
                "prompt_tokens": usage_raw.get("prompt_tokens", 0),
                "completion_tokens": usage_raw.get("completion_tokens", 0),
                "total_tokens": usage_raw.get("total_tokens", 0),
            }

            finish_reason = choice.get("finish_reason", "stop")

            return {
                "content": content,
                "model": body.get("model", model),
                "usage": usage,
                "finish_reason": finish_reason,
                "ok": True,
                "error": "",
                "elapsed_ms": elapsed,
            }

        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")[:500]
            except Exception:
                pass
            return {
                "content": "",
                "model": model,
                "usage": {},
                "finish_reason": "error",
                "ok": False,
                "error": f"HTTP {e.code}: {error_body}",
                "elapsed_ms": 0,
            }
        except Exception as e:
            return {
                "content": "",
                "model": model,
                "usage": {},
                "finish_reason": "error",
                "ok": False,
                "error": str(e)[:500],
                "elapsed_ms": 0,
            }

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict[str, Any]]:
        """Extract JSON object from text, handling markdown code blocks."""
        if not text:
            return None

        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON in markdown code blocks
        import re
        # ```json ... ```
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try to find first { ... } pair
        start = text.find("{")
        if start >= 0:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            break

        return None


# ═══════════════════════════════════════════════════════════════
# Stagnation detection
# ═══════════════════════════════════════════════════════════════

@dataclass
class StagnationEvent:
    """Marks a detected stagnation point in task execution."""

    subtask_id: str = ""
    phase: str = ""
    retry_count: int = 0
    last_output_hash: str = ""
    detected_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subtask_id": self.subtask_id,
            "phase": self.phase,
            "retry_count": self.retry_count,
            "last_output_hash": self.last_output_hash,
            "detected_at": self.detected_at,
        }


class StagnationDetector:
    """Detects when task execution is stuck in a repetitive loop.

    Monitors output hashes and retry counts per subtask.
    """

    def __init__(self, max_retries: int = 5, hash_window: int = 3):
        self.max_retries = max_retries
        self.hash_window = hash_window
        self._history: Dict[str, List[str]] = {}       # subtask_id -> recent hashes
        self._retries: Dict[str, int] = {}             # subtask_id -> retry count
        self._events: List[StagnationEvent] = []

    def check(self, subtask_id: str, output: str, phase: str = "execute") -> Optional[StagnationEvent]:
        """Check if a subtask appears to be stagnating.

        Args:
            subtask_id: Task identifier.
            output: Current output content.
            phase: Current execution phase.

        Returns:
            StagnationEvent if stagnation detected, None otherwise.
        """
        import hashlib
        output_hash = hashlib.md5(output.encode()).hexdigest()

        if subtask_id not in self._history:
            self._history[subtask_id] = []
            self._retries[subtask_id] = 0

        hashes = self._history[subtask_id]

        # Check for repeated identical output
        if len(hashes) >= self.hash_window:
            recent = hashes[-self.hash_window:]
            if len(set(recent)) == 1 and recent[0] == output_hash:
                # Same output repeated — potential stagnation
                self._retries[subtask_id] += 1
                if self._retries[subtask_id] >= self.max_retries:
                    event = StagnationEvent(
                        subtask_id=subtask_id,
                        phase=phase,
                        retry_count=self._retries[subtask_id],
                        last_output_hash=output_hash,
                        detected_at=datetime.now(timezone.utc).isoformat(),
                    )
                    self._events.append(event)
                    return event

        hashes.append(output_hash)
        if len(hashes) > self.hash_window * 2:
            hashes[:] = hashes[-self.hash_window:]

        return None

    def reset(self, subtask_id: str) -> None:
        """Reset stagnation tracking for a subtask."""
        self._history.pop(subtask_id, None)
        self._retries.pop(subtask_id, None)

    @property
    def recent_events(self) -> List[StagnationEvent]:
        """Return recent stagnation events."""
        return list(self._events[-20:])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_events": len(self._events),
            "recent": [e.to_dict() for e in self._events[-10:]],
        }


# ═══════════════════════════════════════════════════════════════
# TaskExecutor — main execution + review with permission gating
# ═══════════════════════════════════════════════════════════════

class TaskExecutor:
    """Executes tasks via LLM with permission gating and review.

    Args:
        client: LLMClient instance for API calls.
        config: Optional KuguaConfig for safety/permission settings.
        permission_gate: Optional callable(action, context) -> (allowed: bool, reason: str).
                         If None, all actions are allowed.
        stagnation_detector: Optional StagnationDetector for loop detection.
    """

    def __init__(
        self,
        client: LLMClient,
        config: Any = None,
        permission_gate: Optional[Callable[[str, Dict], Tuple[bool, str]]] = None,
        stagnation_detector: Optional[StagnationDetector] = None,
        mobius: Optional[Any] = None,  # MobiusController for continuous correction tracking
    ):
        self.client = client
        self.config = config
        self.permission_gate = permission_gate or self._default_gate
        self.stagnation = stagnation_detector or StagnationDetector()
        self.mobius = mobius

    @staticmethod
    def _default_gate(action: str, context: Dict[str, Any]) -> Tuple[bool, str]:
        """Default permissive gate — allows everything."""
        return True, "default: allowed"

    # ── execute ──────────────────────────────────────────────

    def execute(
        self,
        subtask_id: str = "",
        task: str = "",
        context: str = "",
        model: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> TaskResult:
        """Execute a task via LLM with permission check.

        Args:
            subtask_id: Unique task identifier.
            task: The task description/prompt.
            context: Additional context to include.
            model: Model override.
            temperature: Sampling temperature.
            max_tokens: Max output tokens.

        Returns:
            TaskResult with output, usage, and timing.
        """
        if not subtask_id:
            subtask_id = f"task_{uuid.uuid4().hex[:8]}"

        # Permission gate check
        allowed, reason = self.permission_gate("execute", {
            "subtask_id": subtask_id,
            "task": task[:200],
        })
        if not allowed:
            return TaskResult(
                ok=False, subtask_id=subtask_id,
                error=f"Permission denied: {reason}",
            )

        # Build messages
        messages = self._build_execute_messages(task, context)

        # Call LLM
        start = time.perf_counter()
        result = self.client.chat(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        ok = result.get("ok", False)
        output = result.get("content", "")
        error = result.get("error", "")

        # Stagnation check
        if ok and output:
            stag = self.stagnation.check(subtask_id, output, phase="execute")
            if stag:
                error = f"Stagnation detected after {stag.retry_count} retries"

        return TaskResult(
            ok=ok and not error.startswith("Stagnation"),
            subtask_id=subtask_id,
            output=output,
            usage=result.get("usage", {}),
            elapsed_ms=elapsed_ms,
            model=result.get("model", model or ""),
            error=error,
        )

    def _build_execute_messages(self, task: str, context: str) -> List[Dict[str, str]]:
        """Build messages for task execution."""
        system_msg = (
            "You are a precise, careful AI task executor (kugua Worker). "
            "Complete the given task accurately and thoroughly. "
            "If unsure, state your uncertainty clearly. "
            "Output the result directly — no preamble, no self-praise."
        )
        user_msg = f"Task: {task}"
        if context:
            user_msg += f"\n\nContext:\n{context}"

        return [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]

    def execute_and_review(
        self,
        task_dag: list,
        model: Optional[str] = None,
    ) -> list:
        """Execute all tasks in a DAG and review each result.

        This is the batch entry point used by MainLoop.
        Each task dict should have: id, task, context (optional), requirements (optional).

        Args:
            task_dag: List of task descriptors [{id, task, context?, requirements?}, ...]
            model: Optional model override for all tasks.

        Returns:
            List of (TaskResult, ReviewResult) tuples.
        """
        results = []
        default_req = "准确性、完整性、合规性"
        for task_desc in (task_dag or []):
            tid = task_desc.get("id", "")
            task_text = task_desc.get("task", "")
            ctx = task_desc.get("context", "")
            req = task_desc.get("requirements", default_req)

            exec_result = self.execute(
                subtask_id=tid, task=task_text, context=ctx, model=model
            )
            if exec_result.ok:
                review_result = self.review(
                    subtask_id=tid,
                    worker_output=exec_result.output,
                    requirements=req,
                    model=model,
                )
                results.append((exec_result, review_result))
            else:
                results.append((
                    exec_result,
                    ReviewResult(
                        ok=False, subtask_id=tid, verdict="fail",
                        issues=[exec_result.error],
                    ),
                ))
        return results

    # ── review ───────────────────────────────────────────────

    def review(
        self,
        subtask_id: str = "",
        worker_output: str = "",
        requirements: str = "准确性、完整性、合规性",
        model: Optional[str] = None,
    ) -> ReviewResult:
        """Review a worker's output via LLM with permission check.

        Args:
            subtask_id: Task identifier being reviewed.
            worker_output: The worker's output to review.
            requirements: Review criteria.
            model: Model override.

        Returns:
            ReviewResult with verdict, issues, and score.
        """
        if not subtask_id:
            subtask_id = f"review_{uuid.uuid4().hex[:8]}"

        # Permission gate check
        allowed, reason = self.permission_gate("review", {
            "subtask_id": subtask_id,
            "requirements": requirements,
        })
        if not allowed:
            return ReviewResult(
                ok=False, subtask_id=subtask_id,
                verdict="pending",
                issues=[f"Permission denied: {reason}"],
            )

        # Build review messages
        messages = self._build_review_messages(worker_output, requirements)

        # Call LLM with mobius-enhanced schema
        start = time.perf_counter()
        output_schema = {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["pass", "fail", "pending"]},
                "score": {"type": "number", "minimum": 0, "maximum": 100},
                "issues": {"type": "array", "items": {"type": "string"}},
                "correction_hint": {
                    "type": "string",
                    "description": "If verdict=fail: specific fix suggestion. If pass: 'none'."
                },
                "error_location": {
                    "type": "string",
                    "description": "If verdict=fail: where error occurred. If pass: 'none'."
                },
                "suspected_gv_ids": {
                    "type": "array", "items": {"type": "string"},
                    "description": "If verdict=fail: KB entry keys causing the error."
                },
            },
            "required": ["verdict", "score", "issues"],
        }
        result = self.client.chat_structured(
            model=model,
            messages=messages,
            output_schema=output_schema,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        if result.get("ok") and result.get("data"):
            data = result["data"]
            verdict = data.get("verdict", "pending")
            score = float(data.get("score", 0))
            issues = data.get("issues", [])
            correction_hint = data.get("correction_hint", "")
            error_location = data.get("error_location", "")
            suspected_gv_ids = data.get("suspected_gv_ids", [])

            # Mobius: generate CorrectionBias on FAIL
            correction_bias = None
            if verdict in ("fail", "pending") and correction_hint and correction_hint.lower() != "none":
                if suspected_gv_ids:
                    from kugua.mobius import CorrectionBias
                    correction_bias = CorrectionBias(
                        error_location=error_location or "未指定",
                        error_type="准确性",
                        correction_hint=correction_hint,
                        confidence=score / 100.0,
                        gv_id=suspected_gv_ids[0] if isinstance(suspected_gv_ids, list) else str(suspected_gv_ids),
                    )

            return ReviewResult(
                ok=verdict == "pass",
                subtask_id=subtask_id,
                verdict=verdict,
                issues=issues,
                score=score,
                usage=result.get("usage", {}),
                elapsed_ms=elapsed_ms,
                model=result.get("model", model or ""),
                correction_bias=correction_bias,
                suspected_gv_ids=suspected_gv_ids if isinstance(suspected_gv_ids, list) else [],
            )

        return ReviewResult(
            ok=False,
            subtask_id=subtask_id,
            verdict="pending",
            issues=[result.get("error", "Review failed")],
            usage=result.get("usage", {}),
            elapsed_ms=elapsed_ms,
            model=result.get("model", model or ""),
        )

    def _build_review_messages(
        self, worker_output: str, requirements: str
    ) -> List[Dict[str, str]]:
        """Build messages for task review."""
        system_msg = (
            "You are a strict but fair AI code reviewer (kugua Checker). "
            "Review the worker output against the stated requirements. "
            "Return a JSON object with: verdict (pass/fail/pending), "
            "score (0-100), and issues (list of strings)."
        )
        user_msg = (
            f"Requirements: {requirements}\n\n"
            f"Worker Output:\n{worker_output}\n\n"
            f"Review the output against the requirements. "
            f"Be specific about any issues found."
        )

        return [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]
