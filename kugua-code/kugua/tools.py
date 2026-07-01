"""
kugua — Tool registry (OpenAI function-calling compatible)
v0.2.1
"""
from __future__ import annotations
from typing import Callable, Any


def tool(func: Callable = None, *, name: str = "", description: str = ""):
    """Decorator: register a function as an LLM-callable tool."""
    def decorator(fn):
        fn._tool_name = name or fn.__name__
        fn._tool_description = description or fn.__doc__ or ""
        fn._tool_params = {}
        return fn
    if func is not None:
        return decorator(func)
    return decorator


class ToolRegistry:
    """Registry of LLM-callable tools with schema generation.

    Usage:
        reg = ToolRegistry()
        @reg.register(name="search", description="Search knowledge base")
        def search(query: str) -> str: ...
        schemas = reg.schemas()  # OpenAI-compatible function definitions
    """

    def __init__(self):
        self._tools: dict[str, Callable] = {}

    def register(self, func: Callable = None, *, name: str = "", description: str = ""):
        def decorator(fn):
            n = name or fn.__name__
            self._tools[n] = fn
            fn._tool_name = n
            fn._tool_description = description or fn.__doc__ or ""
            return fn
        if func is not None:
            return decorator(func)
        return decorator

    def schemas(self) -> list[dict]:
        """Return OpenAI-compatible function definitions."""
        return [
            {
                "type": "function",
                "function": {
                    "name": getattr(fn, "_tool_name", name),
                    "description": getattr(fn, "_tool_description", ""),
                    "parameters": {
                        "type": "object",
                        "properties": getattr(fn, "_tool_params", {}),
                    },
                },
            }
            for name, fn in self._tools.items()
        ]

    def dispatch_safe(self, name: str, args: dict) -> dict:
        """Dispatch a tool call safely, returning result or error dict."""
        fn = self._tools.get(name)
        if fn is None:
            return {"error": f"Unknown tool: {name}"}
        try:
            result = fn(**args)
            return {"result": str(result)}
        except Exception as e:
            return {"error": str(e)}
