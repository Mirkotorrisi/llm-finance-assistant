"""Backend-agnostic agent interface.

A backend turns one user message into a stream of events. The chat route and the
UI only know these events, so the engine behind them (Claude Code headless,
the legacy OpenAI graph, ...) can be swapped with ``FINANCE_BACKEND``.

Event shapes (all dicts, ``type`` discriminates):

    {"type": "status", "tool": "list_transactions"}      a tool call started
    {"type": "text_delta", "text": "..."}                incremental assistant text
    {"type": "ui_component", "component": {...}}         a UIPlanComponent to render
    {"type": "done", "text": "...", "ui": {...} | None}  turn finished; ui = {"text", "components"}
    {"type": "error", "message": "..."}                  turn failed (always last)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

Event = dict[str, Any]


class AgentError(Exception):
    """A turn could not be completed."""


def status(tool: str) -> Event:
    return {"type": "status", "tool": tool}


def text_delta(text: str) -> Event:
    return {"type": "text_delta", "text": text}


def ui_component(component: dict[str, Any]) -> Event:
    return {"type": "ui_component", "component": component}


def done(text: str, components: list[dict[str, Any]]) -> Event:
    ui = {"text": "", "components": components} if components else None
    return {"type": "done", "text": text, "ui": ui}


def error(message: str) -> Event:
    return {"type": "error", "message": message}


class AgentBackend(ABC):
    name: str = "abstract"

    @abstractmethod
    def run_turn(
        self, session_id: str, user_id: str, message: str, workspace_id: int | None = None
    ) -> AsyncIterator[Event]:
        """Run one conversational turn and yield events. Must end with ``done`` or ``error``.

        ``workspace_id`` is the project the user is working in (None: their default project).
        """

    async def warm(self, session_id: str, user_id: str, workspace_id: int | None = None) -> None:
        """Optionally prepare a session ahead of the first message."""

    async def aclose(self) -> None:
        """Release resources (processes, clients)."""

    def info(self) -> dict[str, Any]:
        return {"backend": self.name}
