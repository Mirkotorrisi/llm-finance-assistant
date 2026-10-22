"""Agent backend wrapping the legacy OpenAI LangGraph pipeline (needs OPENAI_API_KEY).

Kept intact behind the common interface so the app can switch back with
``FINANCE_BACKEND=openai``. The graph is not streaming, so text arrives in one chunk.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from src.api_client import request_context
from src.agent.base import AgentBackend, Event, done, error, text_delta, ui_component


class OpenAIGraphBackend(AgentBackend):
    name = "openai"

    def __init__(self) -> None:
        self._graph = None
        self._history: dict[str, list[str]] = {}

    def _get_graph(self):
        if self._graph is None:
            from src.workflow import create_assistant_graph

            self._graph = create_assistant_graph()
        return self._graph

    async def run_turn(
        self, session_id: str, user_id: str, message: str, workspace_id: int | None = None
    ) -> AsyncIterator[Event]:
        from src.models import Action, FinancialParameters, UserInput

        history = self._history.setdefault(session_id, [])
        state: dict[str, Any] = {
            "input": UserInput(text=message),
            "transcription": None,
            "action": Action.UNKNOWN,
            "parameters": FinancialParameters(),
            "query_results": None,
            "ui_metadata": None,
            "response": None,
            "history": history,
            "user_id": user_id,
            "user_context": None,
            "needs_onboarding": False,
            "onboarding_result": None,
        }
        try:
            with request_context(workspace_id, user_id):
                result = await self._get_graph().ainvoke(state)
        except Exception as e:  # noqa: BLE001 - surfaced to the client as an error event
            yield error(f"OpenAI pipeline failed: {e}")
            return

        try:
            parsed = json.loads(result["response"])
            text, ui = parsed.get("text") or "", parsed.get("ui")
        except (json.JSONDecodeError, TypeError):
            text, ui = result.get("response") or "", None
        ui = ui or result.get("ui_metadata")

        self._history[session_id] = result.get("history", history)
        components = list((ui or {}).get("components", []))
        if text:
            yield text_delta(text)
        for comp in components:
            yield ui_component(comp)
        yield done(text, components)
