"""Integration tests for RAG-based semantic search."""

import io
import pytest
from fastapi.testclient import TestClient
from src.api.app import app

client = TestClient(app)


def test_rag_search_endpoint():
    """Test the RAG semantic search endpoint."""
    
    # First, upload some transactions
    csv_content = b"""date,description,amount,currency
2026-01-15,Walmart grocery shopping,-75.00,EUR
2026-01-16,Whole Foods organic produce,-120.00,EUR
2026-01-17,McDonald's lunch,-15.00,EUR
2026-01-18,Shell gas station,-50.00,EUR"""
    
    files = {"file": ("test_rag.csv", io.BytesIO(csv_content), "text/csv")}
    upload_response = client.post("/statements/upload", files=files)
    
    assert upload_response.status_code == 200
    upload_result = upload_response.json()
    assert upload_result["success"] is True
    
    # Now search for food-related transactions
    search_request = {
        "query": "food and grocery expenses",
        "top_k": 3
    }
    
    search_response = client.post("/api/transactions/search", json=search_request)
    
    # Should return results (may be empty if no OpenAI key)
    assert search_response.status_code == 200
    search_result = search_response.json()
    
    assert "query" in search_result
    assert "results" in search_result
    assert "total_in_store" in search_result
    assert search_result["query"] == "food and grocery expenses"
    assert isinstance(search_result["results"], list)


def test_rag_search_empty_query():
    """Test RAG search with empty results."""
    
    search_request = {
        "query": "very specific unusual transaction that does not exist",
        "top_k": 5
    }
    
    search_response = client.post("/api/transactions/search", json=search_request)
    
    assert search_response.status_code == 200
    search_result = search_response.json()
    assert isinstance(search_result["results"], list)


def test_root_endpoint_includes_search():
    """Test that the root endpoint lists the search endpoint."""
    
    response = client.get("/")
    assert response.status_code == 200
    
    data = response.json()
    assert "endpoints" in data
    assert "search_transactions" in data["endpoints"]
    assert "RAG" in data["endpoints"]["search_transactions"]
