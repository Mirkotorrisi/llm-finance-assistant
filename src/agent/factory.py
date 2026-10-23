"""Backend selection, driven by ``FINANCE_BACKEND`` (claude | openai)."""

from __future__ import annotations

import os

from src.agent.base import AgentBackend

DEFAULT_BACKEND = "claude"
_backend: AgentBackend | None = None


def create_backend(name: str | None = None) -> AgentBackend:
    name = (name or os.getenv("FINANCE_BACKEND") or DEFAULT_BACKEND).strip().lower()
    if name == "claude":
        from src.agent.claude_code import ClaudeCodeBackend

        return ClaudeCodeBackend()
    if name == "openai":
        from src.agent.openai_tools import OpenAIToolsBackend

        return OpenAIToolsBackend()
    raise ValueError(f"Unknown FINANCE_BACKEND {name!r}. Use 'claude' or 'openai'.")


def get_backend() -> AgentBackend:
    global _backend
    if _backend is None:
        _backend = create_backend()
    return _backend


async def shutdown_backend() -> None:
    global _backend
    if _backend is not None:
        await _backend.aclose()
        _backend = None
