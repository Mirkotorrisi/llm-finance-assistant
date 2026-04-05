"""Unit tests for the POST /chat/plan endpoint and its helper functions."""

import json
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app import app
from src.routes.chat import _build_ui_plan, _extract_last_user_text
from src.models.chat import Message, MessagePart, UIPlan, UIPlanComponent

client = TestClient(app)


# ---------------------------------------------------------------------------
# _extract_last_user_text
# ---------------------------------------------------------------------------

class TestExtractLastUserText:
    """Tests for the _extract_last_user_text helper."""

    def test_returns_content_of_last_user_message(self):
        messages = [
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi there"),
            Message(role="user", content="What is my balance?"),
        ]
        assert _extract_last_user_text(messages) == "What is my balance?"

    def test_skips_assistant_messages(self):
        messages = [
            Message(role="user", content="First question"),
            Message(role="assistant", content="Answer"),
        ]
        assert _extract_last_user_text(messages) == "First question"

    def test_extracts_text_from_parts_when_no_content(self):
        messages = [
            Message(
                role="user",
                parts=[
                    MessagePart(type="text", text="Show me my transactions"),
                ],
            )
        ]
        assert _extract_last_user_text(messages) == "Show me my transactions"

    def test_joins_multiple_text_parts(self):
        messages = [
            Message(
                role="user",
                parts=[
                    MessagePart(type="text", text="Show"),
                    MessagePart(type="image", text=None),
                    MessagePart(type="text", text="transactions"),
                ],
            )
        ]
        assert _extract_last_user_text(messages) == "Show transactions"

    def test_prefers_content_over_parts(self):
        messages = [
            Message(
                role="user",
                content="Content wins",
                parts=[MessagePart(type="text", text="Parts lose")],
            )
        ]
        assert _extract_last_user_text(messages) == "Content wins"

    def test_returns_empty_string_when_no_user_message(self):
        messages = [
            Message(role="assistant", content="Hi"),
        ]
        assert _extract_last_user_text(messages) == ""

    def test_returns_empty_string_for_empty_list(self):
        assert _extract_last_user_text([]) == ""


# ---------------------------------------------------------------------------
# _build_ui_plan
# ---------------------------------------------------------------------------

class TestBuildUiPlan:
    """Tests for the _build_ui_plan helper."""

    def test_uses_component_key_as_type(self):
        ui_metadata = {
            "type": "table",
            "componentKey": "summary-table",
            "data": {},
            "metadata": {"title": "Transactions"},
        }
        plan = _build_ui_plan("Here are your transactions.", ui_metadata)
        assert plan.components[0].type == "summary-table"

    def test_falls_back_to_type_when_no_component_key(self):
        ui_metadata = {"type": "metric", "data": {"label": "Balance"}}
        plan = _build_ui_plan("Your balance.", ui_metadata)
        assert plan.components[0].type == "metric"

    def test_sets_title_from_metadata(self):
        ui_metadata = {
            "componentKey": "summary-table",
            "metadata": {"title": "Recent Transactions"},
        }
        plan = _build_ui_plan("Here are your transactions.", ui_metadata)
        assert plan.components[0].title == "Recent Transactions"

    def test_sets_title_from_data_label_when_no_metadata(self):
        ui_metadata = {
            "componentKey": "metric-card",
            "data": {"value": 1234.56, "label": "Total Balance"},
        }
        plan = _build_ui_plan("Your balance is shown below.", ui_metadata)
        assert plan.components[0].title == "Total Balance"

    def test_sets_text_on_plan(self):
        ui_metadata = {"componentKey": "metric-card", "data": {}}
        plan = _build_ui_plan("The text response.", ui_metadata)
        assert plan.text == "The text response."

    def test_component_order_is_zero(self):
        ui_metadata = {"componentKey": "summary-table", "data": {}}
        plan = _build_ui_plan("Result.", ui_metadata)
        assert plan.components[0].order == 0

    def test_returns_one_component(self):
        ui_metadata = {"componentKey": "summary-table", "data": {}}
        plan = _build_ui_plan("Result.", ui_metadata)
        assert len(plan.components) == 1


# ---------------------------------------------------------------------------
# POST /chat/plan – endpoint integration tests (graph mocked)
# ---------------------------------------------------------------------------

GRAPH_RESULT_WITH_UI = {
    "response": json.dumps({
        "text": "Here are your recent transactions.",
        "ui": {
            "type": "table",
            "componentKey": "summary-table",
            "data": {"columns": [], "rows": []},
            "metadata": {"title": "Recent Transactions", "description": "Showing 0 results"},
        },
    }),
    "action": type("Action", (), {"value": "list"})(),
    "parameters": type("Params", (), {"model_dump": lambda self, **kw: {}})(),
    "query_results": [],
    "transcription": None,
    "ui_metadata": None,
}

GRAPH_RESULT_WITHOUT_UI = {
    "response": json.dumps({"text": "I'm not sure how to help with that.", "ui": None}),
    "action": type("Action", (), {"value": "unknown"})(),
    "parameters": type("Params", (), {"model_dump": lambda self, **kw: {}})(),
    "query_results": None,
    "transcription": None,
    "ui_metadata": None,
}


def _patch_graph(result):
    """Return a context manager that patches create_assistant_graph."""
    mock_graph = AsyncMock()
    mock_graph.ainvoke = AsyncMock(return_value=result)
    return patch("src.routes.chat.create_assistant_graph", return_value=mock_graph)


class TestChatPlanEndpoint:
    """Integration tests for POST /chat/plan."""

    def test_returns_200_with_valid_messages(self):
        with _patch_graph(GRAPH_RESULT_WITH_UI):
            response = client.post(
                "/chat/plan",
                json={"messages": [{"role": "user", "content": "Show my transactions"}]},
            )
        assert response.status_code == 200

    def test_response_contains_text_field(self):
        with _patch_graph(GRAPH_RESULT_WITH_UI):
            response = client.post(
                "/chat/plan",
                json={"messages": [{"role": "user", "content": "Show my transactions"}]},
            )
        data = response.json()
        assert "text" in data
        assert data["text"] == "Here are your recent transactions."

    def test_response_contains_plan_when_ui_present(self):
        with _patch_graph(GRAPH_RESULT_WITH_UI):
            response = client.post(
                "/chat/plan",
                json={"messages": [{"role": "user", "content": "Show my transactions"}]},
            )
        data = response.json()
        assert data["plan"] is not None
        assert data["plan"]["components"][0]["type"] == "summary-table"

    def test_plan_is_none_when_no_ui_metadata(self):
        with _patch_graph(GRAPH_RESULT_WITHOUT_UI):
            response = client.post(
                "/chat/plan",
                json={"messages": [{"role": "user", "content": "Hello"}]},
            )
        data = response.json()
        assert data["plan"] is None

    def test_returns_400_when_no_user_message(self):
        with _patch_graph(GRAPH_RESULT_WITH_UI):
            response = client.post(
                "/chat/plan",
                json={"messages": [{"role": "assistant", "content": "Hi"}]},
            )
        assert response.status_code == 400

    def test_returns_400_for_empty_messages(self):
        with _patch_graph(GRAPH_RESULT_WITH_UI):
            response = client.post("/chat/plan", json={"messages": []})
        assert response.status_code == 400

    def test_accepts_messages_with_parts(self):
        with _patch_graph(GRAPH_RESULT_WITH_UI):
            response = client.post(
                "/chat/plan",
                json={
                    "messages": [
                        {
                            "role": "user",
                            "parts": [{"type": "text", "text": "Show my transactions"}],
                        }
                    ]
                },
            )
        assert response.status_code == 200

    def test_extracts_last_user_message_in_thread(self):
        """Graph should be called with the last user message, not an earlier one."""
        captured_state = {}

        async def fake_ainvoke(state):
            captured_state["input_text"] = state["input"].text
            return GRAPH_RESULT_WITHOUT_UI

        mock_graph = AsyncMock()
        mock_graph.ainvoke = fake_ainvoke

        with patch("src.routes.chat.create_assistant_graph", return_value=mock_graph):
            client.post(
                "/chat/plan",
                json={
                    "messages": [
                        {"role": "user", "content": "First question"},
                        {"role": "assistant", "content": "First answer"},
                        {"role": "user", "content": "Second question"},
                    ]
                },
            )

        assert captured_state["input_text"] == "Second question"

    def test_plan_component_title_set_correctly(self):
        with _patch_graph(GRAPH_RESULT_WITH_UI):
            response = client.post(
                "/chat/plan",
                json={"messages": [{"role": "user", "content": "Show transactions"}]},
            )
        data = response.json()
        assert data["plan"]["components"][0]["title"] == "Recent Transactions"

    def test_existing_chat_endpoint_unchanged(self):
        """POST /chat must continue to accept ChatRequest and return the existing schema."""
        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(
            return_value={
                "response": json.dumps({"text": "Hello!", "ui": None}),
                "action": type("A", (), {"value": "unknown"})(),
                "parameters": type("P", (), {"model_dump": lambda self, **kw: {}})(),
                "query_results": None,
                "transcription": None,
                "ui_metadata": None,
            }
        )
        with patch("src.routes.chat.create_assistant_graph", return_value=mock_graph):
            response = client.post("/chat", json={"message": "Hello"})

        assert response.status_code == 200
        data = response.json()
        assert "response" in data
        assert "action" in data
        assert "parameters" in data
