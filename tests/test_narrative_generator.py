"""Tests for NarrativeGenerator."""

import pytest
from src.services.narrative_generator import NarrativeGenerator


@pytest.fixture
def generator():
    """Create a NarrativeGenerator instance."""
    return NarrativeGenerator()


def test_narrative_generator_initialization(generator):
    """Test NarrativeGenerator can be instantiated without arguments."""
    assert generator is not None


def test_generate_monthly_summary_with_data(generator):
    """Test generate_monthly_summary with valid pre-computed data."""
    data = {
        "total_income": 5000.0,
        "total_expense": 3000.0,
        "net_savings": 2000.0,
        "net_worth": 50000.0,
    }

    result = generator.generate_monthly_summary(2026, 3, data=data)

    assert result is not None
    assert result["type"] == "monthly_summary"
    assert "March 2026" in result["text"]
    assert result["metadata"]["year"] == 2026
    assert result["metadata"]["month"] == 3
    assert result["metadata"]["total_income"] == 5000.0
    assert result["metadata"]["total_expense"] == 3000.0


def test_generate_monthly_summary_no_data(generator):
    """Test generate_monthly_summary returns None when no data provided."""
    result = generator.generate_monthly_summary(2026, 3)
    assert result is None


def test_generate_monthly_summary_zero_data(generator):
    """Test generate_monthly_summary returns None when income and expense are zero."""
    data = {"total_income": 0.0, "total_expense": 0.0, "net_savings": 0.0, "net_worth": 0.0}
    result = generator.generate_monthly_summary(2026, 3, data=data)
    assert result is None


def test_generate_all_documents_returns_empty(generator):
    """Test generate_all_documents returns an empty list (no data source)."""
    result = generator.generate_all_documents(2026)
    assert result == []


def test_close_is_noop(generator):
    """Test that close() does not raise."""
    generator.close()

