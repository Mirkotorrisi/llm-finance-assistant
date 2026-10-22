"""Tests must never reach a real LLM: no subscription calls, no API calls.

Anything that slips through (a module-level client that a test forgot to replace) gets a binary that does not exist
and no API key, so it fails fast with an LLMError instead of spending anything.
"""

import pytest


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    monkeypatch.setenv("FINANCE_CLAUDE_BIN", "/nonexistent/claude-for-tests")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.delenv("FINANCE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("FINANCE_BACKEND", raising=False)
    # the singleton may have been created by an earlier test with other settings
    import src.llm.factory as factory

    monkeypatch.setattr(factory, "_client", None)
