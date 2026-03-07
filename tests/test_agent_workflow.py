"""Unit tests for the LangGraph-based agent workflow."""

import pytest
from unittest.mock import MagicMock, patch

from src.models import Action, FinancialParameters, UserInput
from src.workflow.state import FinanceState
from src.workflow.graph import create_assistant_graph
from src.workflow.nodes import asr_node, nlu_node, query_node, generator_node


# ---------------------------------------------------------------------------
# Helper: build a minimal FinanceState for testing individual nodes
# ---------------------------------------------------------------------------

def _make_state(**overrides) -> FinanceState:
    base: FinanceState = {
        "input": UserInput(text="test input", is_audio=False),
        "transcription": None,
        "action": Action.UNKNOWN,
        "parameters": FinancialParameters(),
        "query_results": None,
        "response": None,
        "history": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Graph creation
# ---------------------------------------------------------------------------

class TestGraphCreation:
    """Tests for the agent workflow graph."""

    def test_create_assistant_graph_returns_graph(self):
        """create_assistant_graph should return a compiled LangGraph."""
        graph = create_assistant_graph()
        assert graph is not None

    def test_create_assistant_graph_has_invoke(self):
        """Compiled graph must expose an invoke() method."""
        graph = create_assistant_graph()
        assert callable(getattr(graph, "invoke", None))


# ---------------------------------------------------------------------------
# ASR node
# ---------------------------------------------------------------------------

class TestASRNode:
    """Tests for the ASR (Automatic Speech Recognition) node."""

    def test_asr_node_text_passthrough(self):
        """Text input should be returned as-is without any transcription."""
        state = _make_state(input=UserInput(text="show me my balance", is_audio=False))
        result = asr_node(state)
        assert result["transcription"] == "show me my balance"

    def test_asr_node_empty_text(self):
        """Empty text input should return empty transcription."""
        state = _make_state(input=UserInput(text="", is_audio=False))
        result = asr_node(state)
        assert result["transcription"] == ""

    def test_asr_node_audio_failure_returns_empty_string(self):
        """When audio recognition fails the node should return an empty string."""
        state = _make_state(input=UserInput(text="/nonexistent/audio.wav", is_audio=True))
        result = asr_node(state)
        # Should not raise; on any error it falls back to empty string
        assert result["transcription"] == ""


# ---------------------------------------------------------------------------
# NLU node
# ---------------------------------------------------------------------------

class TestNLUNode:
    """Tests for the NLU (Natural Language Understanding) node."""

    def test_nlu_node_empty_transcription_returns_unknown(self):
        """Empty transcription should yield Action.UNKNOWN without calling the LLM."""
        state = _make_state(transcription="")
        result = nlu_node(state)
        assert result["action"] == Action.UNKNOWN
        assert isinstance(result["parameters"], FinancialParameters)

    def test_nlu_node_balance_intent(self):
        """NLU node should correctly parse a balance query using a mocked LLM."""
        mock_response_json = '{"action": "balance", "parameters": {}}'

        mock_client = MagicMock()
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock(message=MagicMock(content=mock_response_json))]
        mock_client.chat.completions.create.return_value = mock_completion

        with patch("src.workflow.nodes.get_openai_client", return_value=mock_client):
            state = _make_state(transcription="What is my current balance?")
            result = nlu_node(state)

        assert result["action"] == Action.BALANCE

    def test_nlu_node_add_intent(self):
        """NLU node should correctly parse an add-transaction intent."""
        mock_response_json = (
            '{"action": "add", "parameters": '
            '{"amount": -50.0, "category": "food", "description": "Grocery shopping"}}'
        )

        mock_client = MagicMock()
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock(message=MagicMock(content=mock_response_json))]
        mock_client.chat.completions.create.return_value = mock_completion

        with patch("src.workflow.nodes.get_openai_client", return_value=mock_client):
            state = _make_state(transcription="Add a food expense of 50 euros for grocery shopping")
            result = nlu_node(state)

        assert result["action"] == Action.ADD
        assert result["parameters"].amount == -50.0
        assert result["parameters"].category == "food"

    def test_nlu_node_llm_error_falls_back_to_unknown(self):
        """An LLM error should not propagate; the node falls back to UNKNOWN."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API unavailable")

        with patch("src.workflow.nodes.get_openai_client", return_value=mock_client):
            state = _make_state(transcription="Some request")
            result = nlu_node(state)

        assert result["action"] == Action.UNKNOWN


# ---------------------------------------------------------------------------
# Query node
# ---------------------------------------------------------------------------

class TestQueryNode:
    """Tests for the query execution node."""

    def _mock_mcp(self):
        mock = MagicMock()
        mock.get_balance.return_value = 1920.0
        mock.list_transactions.return_value = [
            {"id": 1, "amount": -50.0, "category": "food", "description": "Grocery"}
        ]
        mock.add_transaction.return_value = {
            "id": 2, "amount": -30.0, "category": "transport", "description": "Bus"
        }
        mock.delete_transaction.return_value = True
        return mock

    def test_query_node_balance(self):
        """query_node should return balance for Action.BALANCE."""
        mock_mcp = self._mock_mcp()
        with patch("src.workflow.nodes.get_mcp_server", return_value=mock_mcp):
            state = _make_state(transcription="balance?", action=Action.BALANCE)
            result = query_node(state)
        assert result["query_results"] == 1920.0

    def test_query_node_list(self):
        """query_node should return transactions for Action.LIST."""
        mock_mcp = self._mock_mcp()
        with patch("src.workflow.nodes.get_mcp_server", return_value=mock_mcp):
            state = _make_state(transcription="list my transactions", action=Action.LIST)
            result = query_node(state)
        assert isinstance(result["query_results"], list)
        assert len(result["query_results"]) == 1

    def test_query_node_add_with_params(self):
        """query_node should add a transaction when all parameters are present."""
        mock_mcp = self._mock_mcp()
        params = FinancialParameters(amount=-30.0, category="transport", description="Bus")
        with patch("src.workflow.nodes.get_mcp_server", return_value=mock_mcp):
            state = _make_state(transcription="add bus ride", action=Action.ADD, parameters=params)
            result = query_node(state)
        assert result["query_results"]["category"] == "transport"

    def test_query_node_add_missing_params_returns_error(self):
        """query_node should return an error dict when add parameters are incomplete."""
        mock_mcp = self._mock_mcp()
        # Missing amount/category/description
        with patch("src.workflow.nodes.get_mcp_server", return_value=mock_mcp):
            state = _make_state(transcription="add something", action=Action.ADD)
            result = query_node(state)
        assert "error" in result["query_results"]

    def test_query_node_delete_with_id(self):
        """query_node should delete a transaction when transaction_id is provided."""
        mock_mcp = self._mock_mcp()
        params = FinancialParameters(transaction_id=1)
        with patch("src.workflow.nodes.get_mcp_server", return_value=mock_mcp):
            state = _make_state(transcription="delete transaction 1", action=Action.DELETE, parameters=params)
            result = query_node(state)
        assert result["query_results"] is True

    def test_query_node_delete_missing_id_returns_error(self):
        """query_node should return an error when transaction_id is missing for DELETE."""
        mock_mcp = self._mock_mcp()
        with patch("src.workflow.nodes.get_mcp_server", return_value=mock_mcp):
            state = _make_state(transcription="delete a transaction", action=Action.DELETE)
            result = query_node(state)
        assert "error" in result["query_results"]

    def test_query_node_unknown_action_returns_none(self):
        """query_node should return None for Action.UNKNOWN."""
        mock_mcp = self._mock_mcp()
        with patch("src.workflow.nodes.get_mcp_server", return_value=mock_mcp):
            state = _make_state(transcription="gibberish", action=Action.UNKNOWN)
            result = query_node(state)
        assert result["query_results"] is None


# ---------------------------------------------------------------------------
# Generator node
# ---------------------------------------------------------------------------

class TestGeneratorNode:
    """Tests for the response generator node."""

    def test_generator_node_produces_response(self):
        """generator_node should return a response string via the LLM."""
        mock_mcp = MagicMock()
        mock_mcp.get_balance.return_value = 1920.0

        mock_client = MagicMock()
        mock_completion = MagicMock()
        mock_completion.choices = [
            MagicMock(message=MagicMock(content="Your current balance is €1920."))
        ]
        mock_client.chat.completions.create.return_value = mock_completion

        with patch("src.workflow.nodes.get_mcp_server", return_value=mock_mcp), \
             patch("src.workflow.nodes.get_openai_client", return_value=mock_client):
            state = _make_state(
                transcription="What is my balance?",
                action=Action.BALANCE,
                query_results=1920.0,
            )
            result = generator_node(state)

        assert result["response"] == "Your current balance is €1920."
        assert "User: What is my balance?" in result["history"]
        assert "Assistant: Your current balance is €1920." in result["history"]

    def test_generator_node_updates_history(self):
        """generator_node should append the turn to the conversation history."""
        mock_mcp = MagicMock()
        mock_mcp.get_balance.return_value = 0.0

        mock_client = MagicMock()
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock(message=MagicMock(content="Done."))]
        mock_client.chat.completions.create.return_value = mock_completion

        existing_history = ["User: hello", "Assistant: hi"]

        with patch("src.workflow.nodes.get_mcp_server", return_value=mock_mcp), \
             patch("src.workflow.nodes.get_openai_client", return_value=mock_client):
            state = _make_state(
                transcription="list transactions",
                action=Action.LIST,
                query_results=[],
                history=existing_history,
            )
            result = generator_node(state)

        # Previous history entries must still be present
        assert "User: hello" in result["history"]
        assert "Assistant: hi" in result["history"]
        # New turn appended
        assert "User: list transactions" in result["history"]
        assert "Assistant: Done." in result["history"]
