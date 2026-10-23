"""Pluggable agent backends (Claude Code headless, OpenAI function calling)."""

from src.agent.base import AgentBackend, AgentError
from src.agent.factory import create_backend, get_backend, shutdown_backend

__all__ = ["AgentBackend", "AgentError", "create_backend", "get_backend", "shutdown_backend"]
