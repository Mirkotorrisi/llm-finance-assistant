"""Tests must never reach a real LLM: no subscription calls, no API calls.

Anything that slips through (a module-level client that a test forgot to replace) gets a binary that does not exist
and no API key, so it fails fast with an LLMError instead of spending anything.
"""

import asyncio
import inspect

import pytest


def pytest_pyfunc_call(pyfuncitem):
    """Run ``async def`` tests with asyncio.run (no pytest-asyncio here)."""
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        kwargs = {name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}
        asyncio.run(pyfuncitem.obj(**kwargs))
        return True
    return None


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch, tmp_path):
    monkeypatch.setenv("FINANCE_CLAUDE_BIN", "/nonexistent/claude-for-tests")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.delenv("FINANCE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("FINANCE_BACKEND", raising=False)
    # bank sync never reaches a real bank in tests: unconfigured, scheduler off, secrets in a temp folder
    monkeypatch.setenv("FINANCE_BANK_SYNC", "off")
    monkeypatch.delenv("ENABLE_BANKING_APP_ID", raising=False)
    monkeypatch.setenv("FINANCE_SECRETS_DIR", str(tmp_path / "secrets"))
    monkeypatch.setenv("ENABLE_BANKING_KEY_PATH", str(tmp_path / "missing.pem"))
    from src.routes import banking as banking_routes

    banking_routes.reset_banking()
    # the singleton may have been created by an earlier test with other settings
    import src.llm.factory as factory

    monkeypatch.setattr(factory, "_client", None)
