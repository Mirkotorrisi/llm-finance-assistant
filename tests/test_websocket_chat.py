"""Tests for the WebSocket chat endpoint."""

import json
import pytest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.api.app import app


client = TestClient(app)


# ---------------------------------------------------------------------------
# Helper: build a mock graph result that the endpoint expects
# ---------------------------------------------------------------------------

def _mock_graph_result(response_text: str = "OK", action_value: str = "balance",
                       query_results=None, history=None):
    mock_action = MagicMock()
    mock_action.value = action_value

    mock_params = MagicMock()
    mock_params.model_dump.return_value = {}

    return {
        "response": response_text,
        "action": mock_action,
        "parameters": mock_params,
        "query_results": query_results,
        "history": history or [
            "User: request",
            f"Assistant: {response_text}",
        ],
    }


def _make_mock_graph(result):
    """Return a mock compiled graph whose invoke() returns *result*."""
    mock_compiled = MagicMock()
    mock_compiled.invoke.return_value = result
    return mock_compiled


# ---------------------------------------------------------------------------
# Connection tests
# ---------------------------------------------------------------------------

class TestWebSocketConnection:
    """Tests for WebSocket connection lifecycle."""

    def test_websocket_accepts_connection(self):
        """The /ws/chat endpoint should accept a new WebSocket connection."""
        with patch("src.api.app.create_assistant_graph",
                   return_value=_make_mock_graph(_mock_graph_result())):
            with client.websocket_connect("/ws/chat") as ws:
                assert ws is not None


# ---------------------------------------------------------------------------
# Text message tests
# ---------------------------------------------------------------------------

class TestWebSocketTextMessages:
    """Tests for sending and receiving text messages over WebSocket."""

    def test_text_message_returns_response(self):
        """A well-formed text message should produce a JSON response."""
        result = _mock_graph_result(
            response_text="Your balance is €1920.",
            action_value="balance",
            query_results=1920.0,
        )
        with patch("src.api.app.create_assistant_graph",
                   return_value=_make_mock_graph(result)):
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_text(json.dumps({"message": "What is my balance?", "is_audio": False}))
                data = ws.receive_json()

        assert data["response"] == "Your balance is €1920."
        assert data["action"] == "balance"
        assert data["query_results"] == 1920.0

    def test_text_message_response_has_required_keys(self):
        """The response JSON must contain response, action, parameters, query_results."""
        result = _mock_graph_result()
        with patch("src.api.app.create_assistant_graph",
                   return_value=_make_mock_graph(result)):
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_text(json.dumps({"message": "hello", "is_audio": False}))
                data = ws.receive_json()

        for key in ("response", "action", "parameters", "query_results"):
            assert key in data, f"Missing key: {key}"

    def test_multiple_messages_in_same_session(self):
        """Multiple messages in one session should each produce a response."""
        result = _mock_graph_result(response_text="Response", history=["User: q", "Assistant: Response"])
        with patch("src.api.app.create_assistant_graph",
                   return_value=_make_mock_graph(result)):
            with client.websocket_connect("/ws/chat") as ws:
                for _ in range(3):
                    ws.send_text(json.dumps({"message": "ping", "is_audio": False}))
                    data = ws.receive_json()
                    assert "response" in data

    def test_empty_message_field_is_handled(self):
        """An empty 'message' field should not crash the server."""
        result = _mock_graph_result(response_text="Sorry, I didn't understand.")
        with patch("src.api.app.create_assistant_graph",
                   return_value=_make_mock_graph(result)):
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_text(json.dumps({"message": "", "is_audio": False}))
                data = ws.receive_json()
        # Should get either a response or an error key — not a crash
        assert "response" in data or "error" in data


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------

class TestWebSocketErrorHandling:
    """Tests for WebSocket error and edge-case handling."""

    def test_invalid_json_returns_error(self):
        """Non-JSON payload should receive an error response, not a crash."""
        with patch("src.api.app.create_assistant_graph",
                   return_value=_make_mock_graph(_mock_graph_result())):
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_text("this is not json at all")
                data = ws.receive_json()
        assert "error" in data
        assert "JSON" in data["error"] or "json" in data["error"].lower()

    def test_graph_exception_returns_error(self):
        """If the agent graph raises, the WS should send an error message instead of crashing."""
        mock_compiled = MagicMock()
        mock_compiled.invoke.side_effect = Exception("LLM unavailable")

        with patch("src.api.app.create_assistant_graph", return_value=mock_compiled):
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_text(json.dumps({"message": "balance?", "is_audio": False}))
                data = ws.receive_json()
        assert "error" in data

    def test_invalid_audio_data_returns_error(self):
        """Invalid base64 audio data should produce an error response, not a crash."""
        with patch("src.api.app.create_assistant_graph",
                   return_value=_make_mock_graph(_mock_graph_result())):
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_text(json.dumps({
                    "message": "",
                    "is_audio": True,
                    "audio_data": "!!!not-valid-base64!!!"
                }))
                data = ws.receive_json()
        assert "error" in data
