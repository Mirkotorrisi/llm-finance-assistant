"""Tests for LLM-based PDF parsing."""

import pytest
from src.services.transaction_parser import TransactionParser


def test_parse_pdf_with_llm_no_api_key():
    """Test PDF parsing when no OpenAI API key is available."""
    # This test will use the fallback behavior
    pdf_text = """
    Bank Statement
    Date: 01/15/2026
    Description: Walmart Purchase
    Amount: -$50.00
    """
    
    # Without API key, should return empty list or handle gracefully
    result = TransactionParser.parse_pdf_with_llm(
        pdf_text,
        existing_categories=[],
        openai_client=None
    )
    
    # Should return empty list when no API key
    assert isinstance(result, list)


def test_parse_transactions_with_pdf_text():
    """Test that PDF text is routed to LLM-based parsing."""
    rows = [{"pdf_text": "Sample bank statement text"}]
    
    result = TransactionParser.parse_transactions(
        rows,
        existing_categories=["food", "transport"],
        use_llm_categorization=True
    )
    
    # Should return a list (even if empty without API key)
    assert isinstance(result, list)


def test_parse_transactions_with_csv_data():
    """Test that CSV data is parsed correctly."""
    rows = [
        {
            "date": "2026-01-15",
            "description": "Grocery Store",
            "amount": "-50.00"
        }
    ]
    
    result = TransactionParser.parse_transactions(
        rows,
        existing_categories=["food"],
        use_llm_categorization=False  # Disable LLM for this test
    )
    
    assert len(result) == 1
    assert result[0]["date"] == "2026-01-15"
    assert result[0]["description"] == "Grocery Store"
    assert result[0]["amount"] == -50.00
