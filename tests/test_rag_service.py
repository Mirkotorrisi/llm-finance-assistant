"""Tests for RAG service."""

import pytest
from src.services.vectorization import RAGService


def test_rag_service_initialization():
    """Test RAG service initialization."""
    rag = RAGService()
    assert rag.size() == 0


def test_add_transactions():
    """Test adding transactions to vector store."""
    rag = RAGService()
    
    transactions = [
        {
            "date": "2026-01-15",
            "description": "Grocery shopping at Walmart",
            "amount": -100.00,
            "currency": "EUR",
            "category": "food"
        },
        {
            "date": "2026-01-16",
            "description": "Gas station",
            "amount": -40.00,
            "currency": "EUR",
            "category": "transport"
        }
    ]
    
    # Add transactions (may fail if no OpenAI key)
    result = rag.add_transactions(transactions)
    
    # If OpenAI key is available, check size
    if result:
        assert rag.size() == 2
    else:
        # If no key, size should still be 0
        assert rag.size() == 0


def test_query_empty_store():
    """Test querying an empty vector store."""
    rag = RAGService()
    results = rag.query("food expenses")
    assert results == []


def test_clear():
    """Test clearing the vector store."""
    rag = RAGService()
    
    transactions = [
        {
            "date": "2026-01-15",
            "description": "Coffee shop",
            "amount": -5.00,
            "currency": "EUR",
            "category": "food"
        }
    ]
    
    rag.add_transactions(transactions)
    rag.clear()
    assert rag.size() == 0


def test_create_transaction_text():
    """Test transaction text creation."""
    transaction = {
        "date": "2026-01-15",
        "description": "Grocery shopping",
        "amount": -100.00,
        "currency": "USD",
        "category": "food"
    }
    
    text = RAGService.create_transaction_text(transaction)
    
    assert "2026-01-15" in text
    assert "Grocery shopping" in text
    assert "-100.0" in text
    assert "USD" in text
    assert "food" in text
