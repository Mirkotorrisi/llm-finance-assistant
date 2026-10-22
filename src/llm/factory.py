"""LLM provider selection.

``FINANCE_LLM_PROVIDER`` wins; otherwise it follows ``FINANCE_BACKEND`` so a single
switch (claude | openai) moves both the chat and the batch jobs.
"""

from __future__ import annotations

import os

from src.llm.base import LLMClient

DEFAULT_PROVIDER = "claude"
_client: LLMClient | None = None


def create_llm_client(name: str | None = None) -> LLMClient:
    name = (name or os.getenv("FINANCE_LLM_PROVIDER") or os.getenv("FINANCE_BACKEND") or DEFAULT_PROVIDER)
    name = name.strip().lower()
    if name == "claude":
        from src.llm.claude_client import ClaudeClient

        return ClaudeClient()
    if name == "openai":
        from src.llm.openai_client import OpenAIClient

        return OpenAIClient()
    raise ValueError(f"Unknown LLM provider {name!r}. Use 'claude' or 'openai'.")


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = create_llm_client()
    return _client
