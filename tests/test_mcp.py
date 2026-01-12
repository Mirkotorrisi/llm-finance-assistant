"""Unit tests for MCP service."""

import pytest
from src.business_logic.mcp import FinanceMCP


class TestAddTransaction:
    """Tests for adding transactions."""
    
    def test_add_transaction_basic(self):
        """Test adding a basic transaction."""
        mcp = FinanceMCP([])
        
        result = mcp.add_transaction(
            amount=-50.0,
            category="food",
            description="Grocery shopping",
            date="2026-01-11"
        )
        
        assert result["id"] == 1
        assert result["amount"] == -50.0
        assert result["category"] == "food"
        assert result["description"] == "Grocery shopping"
        assert result["date"] == "2026-01-11"
    
    def test_add_transaction_with_currency(self):
        """Test adding a transaction with currency."""
        mcp = FinanceMCP([])
        
        result = mcp.add_transaction(
            amount=-50.0,
            category="food",
            description="Grocery shopping",
            date="2026-01-11",
            currency="USD"
        )
        
        assert result["currency"] == "USD"
    
    def test_add_transactions_bulk(self):
        """Test adding multiple transactions at once."""
        mcp = FinanceMCP([])
        
        transactions = [
            {
                "amount": -50.0,
                "category": "food",
                "description": "Grocery",
                "date": "2026-01-11",
                "currency": "EUR"
            },
            {
                "amount": -30.0,
                "category": "transport",
                "description": "Gas",
                "date": "2026-01-12",
                "currency": "EUR"
            }
        ]
        
        result = mcp.add_transactions_bulk(transactions)
        
        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[1]["id"] == 2
        assert len(mcp.transactions) == 2


class TestListTransactions:
    """Tests for listing transactions."""
    
    def test_list_all_transactions(self):
        """Test listing all transactions."""
        initial_transactions = [
            {"id": 1, "date": "2026-01-11", "amount": -50.0, "category": "food", "description": "Grocery"},
            {"id": 2, "date": "2026-01-12", "amount": -30.0, "category": "transport", "description": "Gas"}
        ]
        
        mcp = FinanceMCP(initial_transactions)
        result = mcp.list_transactions()
        
        assert len(result) == 2
    
    def test_list_transactions_by_category(self):
        """Test filtering transactions by category."""
        initial_transactions = [
            {"id": 1, "date": "2026-01-11", "amount": -50.0, "category": "food", "description": "Grocery"},
            {"id": 2, "date": "2026-01-12", "amount": -30.0, "category": "transport", "description": "Gas"}
        ]
        
        mcp = FinanceMCP(initial_transactions)
        result = mcp.list_transactions(category="food")
        
        assert len(result) == 1
        assert result[0]["category"] == "food"


class TestGetBalance:
    """Tests for getting balance."""
    
    def test_get_balance(self):
        """Test calculating balance."""
        initial_transactions = [
            {"id": 1, "date": "2026-01-11", "amount": 2000.0, "category": "income", "description": "Salary"},
            {"id": 2, "date": "2026-01-12", "amount": -50.0, "category": "food", "description": "Grocery"},
            {"id": 3, "date": "2026-01-13", "amount": -30.0, "category": "transport", "description": "Gas"}
        ]
        
        mcp = FinanceMCP(initial_transactions)
        balance = mcp.get_balance()
        
        assert balance == 1920.0


class TestGetExistingCategories:
    """Tests for getting existing categories."""
    
    def test_get_existing_categories(self):
        """Test getting unique categories from transactions."""
        initial_transactions = [
            {"id": 1, "date": "2026-01-11", "amount": -50.0, "category": "food", "description": "Grocery"},
            {"id": 2, "date": "2026-01-12", "amount": -30.0, "category": "transport", "description": "Gas"},
            {"id": 3, "date": "2026-01-13", "amount": -25.0, "category": "food", "description": "Lunch"},
            {"id": 4, "date": "2026-01-14", "amount": 2000.0, "category": "income", "description": "Salary"}
        ]
        
        mcp = FinanceMCP(initial_transactions)
        categories = mcp.get_existing_categories()
        
        assert len(categories) == 3
        assert "food" in categories
        assert "transport" in categories
        assert "income" in categories
    
    def test_get_existing_categories_empty(self):
        """Test getting categories when there are no transactions."""
        mcp = FinanceMCP([])
        categories = mcp.get_existing_categories()
        
        assert len(categories) == 0
        assert categories == []
    
    def test_get_existing_categories_sorted(self):
        """Test that categories are returned sorted."""
        initial_transactions = [
            {"id": 1, "date": "2026-01-11", "amount": -50.0, "category": "zzz", "description": "Test"},
            {"id": 2, "date": "2026-01-12", "amount": -30.0, "category": "aaa", "description": "Test"},
            {"id": 3, "date": "2026-01-13", "amount": -25.0, "category": "mmm", "description": "Test"}
        ]
        
        mcp = FinanceMCP(initial_transactions)
        categories = mcp.get_existing_categories()
        
        assert categories == ["aaa", "mmm", "zzz"]
