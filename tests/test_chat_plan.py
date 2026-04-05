"""Unit tests for the POST /chat/plan endpoint."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_graph_result(response_text: str = "Hello!", ui_metadata: dict | None = None):
    """Build a fake graph result dict as the workflow would return."""
    from src.models import Action, FinancialParameters

    return {
        "response": json.dumps({"text": response_text, "ui": ui_metadata}),
        "action": Action.UNKNOWN,
        "parameters": FinancialParameters(),
        "query_results": None,
        "ui_metadata": ui_metadata,
        "transcription": None,
        "history": [],
        "input": None,
    }


def _patch_graph(result: dict):
    """Patch create_assistant_graph so ainvoke returns *result* immediately."""
    mock_graph = AsyncMock()
    mock_graph.ainvoke = AsyncMock(return_value=result)

    graph_patcher = patch(
        "src.routes.chat.create_assistant_graph", return_value=mock_graph
    )
    return graph_patcher


# ---------------------------------------------------------------------------
# Validation tests (no graph needed)
# ---------------------------------------------------------------------------


def test_chat_plan_missing_messages_field():
    response = client.post("/chat/plan", json={})
    assert response.status_code == 422  # Pydantic validation error


def test_chat_plan_empty_messages_list():
    response = client.post("/chat/plan", json={"messages": []})
    assert response.status_code == 400
    assert "non-empty" in response.json()["detail"].lower()


def test_chat_plan_no_user_message():
    response = client.post(
        "/chat/plan",
        json={"messages": [{"role": "assistant", "content": "Hi there"}]},
    )
    assert response.status_code == 400
    assert "no user message" in response.json()["detail"].lower()


def test_chat_plan_user_message_empty_content():
    """A user message with an empty string content counts as no user text."""
    response = client.post(
        "/chat/plan",
        json={"messages": [{"role": "user", "content": ""}]},
    )
    assert response.status_code == 400


def test_chat_plan_user_message_parts_no_text():
    """Parts with no text should also result in a 400."""
    response = client.post(
        "/chat/plan",
        json={
            "messages": [
                {
                    "role": "user",
                    "parts": [{"type": "image"}],
                }
            ]
        },
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Success-path tests (graph is mocked)
# ---------------------------------------------------------------------------


def test_chat_plan_content_field():
    result = _mock_graph_result("Balance is $100")
    with _patch_graph(result):
        response = client.post(
            "/chat/plan",
            json={"messages": [{"role": "user", "content": "What is my balance?"}]},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "Balance is $100"
    assert body.get("plan") is None


def test_chat_plan_parts_field():
    result = _mock_graph_result("Balance is $200")
    with _patch_graph(result):
        response = client.post(
            "/chat/plan",
            json={
                "messages": [
                    {
                        "role": "user",
                        "parts": [
                            {"type": "text", "text": "What is my balance?"}
                        ],
                    }
                ]
            },
        )
    assert response.status_code == 200
    assert response.json()["text"] == "Balance is $200"


def test_chat_plan_plan_from_ui_metadata():
    plan_data = {"component": "metric-card", "value": 42}
    result = _mock_graph_result("Here is your data", ui_metadata=plan_data)
    with _patch_graph(result):
        response = client.post(
            "/chat/plan",
            json={"messages": [{"role": "user", "content": "Show me data"}]},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["plan"] == plan_data


def test_chat_plan_plan_from_parsed_response_ui_key():
    """When ui_metadata is None but response JSON has 'ui' key, use that as plan."""
    from src.models import Action, FinancialParameters

    ui_data = {"component": "chart"}
    result = {
        "response": json.dumps({"text": "Here is a chart", "ui": ui_data}),
        "action": Action.UNKNOWN,
        "parameters": FinancialParameters(),
        "query_results": None,
        "ui_metadata": None,  # not set at top level
        "transcription": None,
        "history": [],
        "input": None,
    }
    with _patch_graph(result):
        response = client.post(
            "/chat/plan",
            json={"messages": [{"role": "user", "content": "Chart please"}]},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["plan"] == ui_data


def test_chat_plan_last_user_message_is_used():
    """When there are multiple messages, the LAST user message text is extracted."""
    result = _mock_graph_result("ok")
    with _patch_graph(result) as mock_create:
        response = client.post(
            "/chat/plan",
            json={
                "messages": [
                    {"role": "user", "content": "first message"},
                    {"role": "assistant", "content": "response"},
                    {"role": "user", "content": "last message"},
                ]
            },
        )
    assert response.status_code == 200
    # Verify the graph was called (i.e., the endpoint didn't bail out early)
    assert mock_create.return_value.ainvoke.called
    # Retrieve the state that was passed to ainvoke
    call_args = mock_create.return_value.ainvoke.call_args
    state = call_args[0][0]
    assert state["input"].text == "last message"


def test_chat_plan_non_json_response_uses_raw_text():
    """If the graph returns a non-JSON string, the raw string is used as text."""
    from src.models import Action, FinancialParameters

    result = {
        "response": "plain text response",
        "action": Action.UNKNOWN,
        "parameters": FinancialParameters(),
        "query_results": None,
        "ui_metadata": None,
        "transcription": None,
        "history": [],
        "input": None,
    }
    with _patch_graph(result):
        response = client.post(
            "/chat/plan",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 200
    assert response.json()["text"] == "plain text response"
