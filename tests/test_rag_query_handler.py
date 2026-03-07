"""Tests for RAGQueryHandler."""

import pytest
from unittest.mock import MagicMock, patch

from src.services.rag_query_handler import RAGQueryHandler


@pytest.fixture
def mock_narrative_rag():
    """Create a mock NarrativeRAGService."""
    return MagicMock()


@pytest.fixture
def query_handler(mock_narrative_rag):
    """Create a RAGQueryHandler with mocked NarrativeRAGService."""
    with patch('src.services.rag_query_handler.NarrativeRAGService', return_value=mock_narrative_rag):
        handler = RAGQueryHandler(narrative_rag_service=mock_narrative_rag)
        yield handler
        handler.close()


def test_query_handler_initialization():
    """Test RAGQueryHandler initialization."""
    with patch('src.services.rag_query_handler.NarrativeRAGService'):
        handler = RAGQueryHandler()
        assert handler.narrative_rag is not None
        handler.close()


def test_answer_query_no_client(query_handler):
    """Test answer_query when OpenAI client is not initialized."""
    query_handler.client = None
    
    result = query_handler.answer_query("test query")
    
    assert result["confidence"] == "none"
    assert "error" in result


def test_answer_query_no_documents(query_handler, mock_narrative_rag):
    """Test answer_query when no relevant documents found."""
    # Mock OpenAI client
    mock_client = MagicMock()
    query_handler.client = mock_client
    
    # Mock empty query results
    mock_narrative_rag.query.return_value = []
    
    result = query_handler.answer_query("test query")
    
    assert result["confidence"] == "none"
    assert len(result["sources"]) == 0
    assert "don't have enough information" in result["answer"]


def test_answer_query_with_documents(query_handler, mock_narrative_rag):
    """Test answer_query with relevant documents."""
    # Mock OpenAI client
    mock_client = MagicMock()
    mock_chat_response = MagicMock()
    mock_chat_response.choices = [
        MagicMock(message=MagicMock(content="Based on the data, expenses were high."))
    ]
    mock_client.chat.completions.create.return_value = mock_chat_response
    query_handler.client = mock_client
    
    # Mock query results
    mock_narrative_rag.query.return_value = [
        {
            "text": "In March 2026, expenses were €3000.",
            "type": "monthly_summary",
            "metadata": {"year": 2026, "month": 3},
            "similarity": 0.9
        }
    ]
    
    result = query_handler.answer_query("Why were expenses high?")
    
    assert result["confidence"] in ["low", "medium", "high"]
    assert len(result["sources"]) == 1
    assert result["sources"][0]["type"] == "monthly_summary"
    assert "Based on the data" in result["answer"]


def test_answer_query_high_confidence(query_handler, mock_narrative_rag):
    """Test answer_query with high confidence results."""
    mock_client = MagicMock()
    mock_chat_response = MagicMock()
    mock_chat_response.choices = [
        MagicMock(message=MagicMock(content="Test answer"))
    ]
    mock_client.chat.completions.create.return_value = mock_chat_response
    query_handler.client = mock_client
    
    # Mock high similarity results
    mock_narrative_rag.query.return_value = [
        {
            "text": "High similarity document",
            "type": "monthly_summary",
            "metadata": {},
            "similarity": 0.95
        }
    ]
    
    result = query_handler.answer_query("test")
    
    assert result["confidence"] == "high"


def test_answer_query_medium_confidence(query_handler, mock_narrative_rag):
    """Test answer_query with medium confidence results."""
    mock_client = MagicMock()
    mock_chat_response = MagicMock()
    mock_chat_response.choices = [
        MagicMock(message=MagicMock(content="Test answer"))
    ]
    mock_client.chat.completions.create.return_value = mock_chat_response
    query_handler.client = mock_client
    
    # Mock medium similarity results
    mock_narrative_rag.query.return_value = [
        {
            "text": "Medium similarity document",
            "type": "monthly_summary",
            "metadata": {},
            "similarity": 0.7
        }
    ]
    
    result = query_handler.answer_query("test")
    
    assert result["confidence"] == "medium"


def test_answer_query_low_confidence(query_handler, mock_narrative_rag):
    """Test answer_query with low confidence results."""
    mock_client = MagicMock()
    mock_chat_response = MagicMock()
    mock_chat_response.choices = [
        MagicMock(message=MagicMock(content="Test answer"))
    ]
    mock_client.chat.completions.create.return_value = mock_chat_response
    query_handler.client = mock_client
    
    # Mock low similarity results
    mock_narrative_rag.query.return_value = [
        {
            "text": "Low similarity document",
            "type": "monthly_summary",
            "metadata": {},
            "similarity": 0.5
        }
    ]
    
    result = query_handler.answer_query("test")
    
    assert result["confidence"] == "low"


def test_get_service_status(query_handler, mock_narrative_rag):
    """Test get_service_status."""
    mock_narrative_rag.get_stats.return_value = {
        "total_documents": 10,
        "by_type": {"monthly_summary": 5, "category_summary": 5},
        "service_ready": True
    }
    
    # Mock client
    query_handler.client = MagicMock()
    
    status = query_handler.get_service_status()
    
    assert status["query_handler_ready"] is True
    assert status["narrative_rag_ready"] is True
    assert status["total_documents"] == 10
    assert "monthly_summary" in status["documents_by_type"]


def test_answer_query_with_top_k(query_handler, mock_narrative_rag):
    """Test answer_query with custom top_k parameter."""
    mock_client = MagicMock()
    mock_chat_response = MagicMock()
    mock_chat_response.choices = [
        MagicMock(message=MagicMock(content="Test answer"))
    ]
    mock_client.chat.completions.create.return_value = mock_chat_response
    query_handler.client = mock_client
    
    mock_narrative_rag.query.return_value = [
        {
            "text": f"Document {i}",
            "type": "monthly_summary",
            "metadata": {},
            "similarity": 0.8
        }
        for i in range(10)
    ]
    
    result = query_handler.answer_query("test", top_k=10)
    
    # Verify query was called with top_k=10
    mock_narrative_rag.query.assert_called_once()
    call_kwargs = mock_narrative_rag.query.call_args[1]
    assert call_kwargs.get("top_k") == 10 or mock_narrative_rag.query.call_args[0][1] == 10


def test_answer_query_error_handling(query_handler, mock_narrative_rag):
    """Test answer_query error handling."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = Exception("API Error")
    query_handler.client = mock_client
    
    mock_narrative_rag.query.return_value = [
        {
            "text": "Test document",
            "type": "monthly_summary",
            "metadata": {},
            "similarity": 0.8
        }
    ]
    
    result = query_handler.answer_query("test")
    
    assert result["confidence"] == "none"
    assert "error" in result
    assert "API Error" in result["error"]


def test_close_is_noop(query_handler):
    """Test that close() does not raise."""
    query_handler.close()

