"""Pluggable agent backends (Claude Code headless, legacy OpenAI graph)."""

from src.agent.base import AgentBackend, AgentError
from src.agent.factory import create_backend, get_backend, shutdown_backend

__all__ = ["AgentBackend", "AgentError", "create_backend", "get_backend", "shutdown_backend"]
