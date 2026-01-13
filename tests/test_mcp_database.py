"""Unit tests for database-backed MCP service."""

import pytest
import os
from datetime import date
from unittest.mock import Mock, patch, MagicMock

# Mock the database imports to allow tests to run without actual database
@pytest.fixture
def mock_db():
    """Create a mock database session."""
    mock_session = MagicMock()
    return mock_session


@pytest.fixture
def mcp_database(mock_db):
    """Create a FinanceMCPDatabase instance with mocked database."""
    with patch('src.business_logic.mcp_database.get_db_session', return_value=mock_db):
        from src.business_logic.mcp_database import FinanceMCPDatabase
        mcp = FinanceMCPDatabase(db_session=mock_db)
        return mcp, mock_db


class TestDatabaseMCPInterface:
    """Tests to verify FinanceMCPDatabase has same interface as FinanceMCP."""
    
    def test_has_list_transactions_method(self, mcp_database):
        """Test that list_transactions method exists."""
        mcp, _ = mcp_database
        assert hasattr(mcp, 'list_transactions')
        assert callable(mcp.list_transactions)
    
    def test_has_add_transaction_method(self, mcp_database):
        """Test that add_transaction method exists."""
        mcp, _ = mcp_database
        assert hasattr(mcp, 'add_transaction')
        assert callable(mcp.add_transaction)
    
    def test_has_delete_transaction_method(self, mcp_database):
        """Test that delete_transaction method exists."""
        mcp, _ = mcp_database
        assert hasattr(mcp, 'delete_transaction')
        assert callable(mcp.delete_transaction)
    
    def test_has_get_balance_method(self, mcp_database):
        """Test that get_balance method exists."""
        mcp, _ = mcp_database
        assert hasattr(mcp, 'get_balance')
        assert callable(mcp.get_balance)
    
    def test_has_add_transactions_bulk_method(self, mcp_database):
        """Test that add_transactions_bulk method exists."""
        mcp, _ = mcp_database
        assert hasattr(mcp, 'add_transactions_bulk')
        assert callable(mcp.add_transactions_bulk)
    
    def test_has_get_existing_categories_method(self, mcp_database):
        """Test that get_existing_categories method exists."""
        mcp, _ = mcp_database
        assert hasattr(mcp, 'get_existing_categories')
        assert callable(mcp.get_existing_categories)
    
    def test_has_get_monthly_snapshots_method(self, mcp_database):
        """Test that get_monthly_snapshots method exists."""
        mcp, _ = mcp_database
        assert hasattr(mcp, 'get_monthly_snapshots')
        assert callable(mcp.get_monthly_snapshots)
    
    def test_has_get_accounts_method(self, mcp_database):
        """Test that get_accounts method exists."""
        mcp, _ = mcp_database
        assert hasattr(mcp, 'get_accounts')
        assert callable(mcp.get_accounts)


class TestDatabaseMCPCompatibility:
    """Tests to verify database MCP works with existing workflow."""
    
    def test_mcp_instance_module_imports(self):
        """Test that mcp_instance module can be imported."""
        from src.workflow import mcp_instance
        
        assert hasattr(mcp_instance, 'get_mcp_server')
        assert hasattr(mcp_instance, 'reset_mcp_server')


class TestDatabaseConfiguration:
    """Tests for database configuration."""
    
    def test_database_config_get_url(self):
        """Test that database URL is correctly formatted."""
        with patch.dict(os.environ, {'DB_PASSWORD': 'test_password'}):
            from src.config.database import DatabaseConfig
            
            url = DatabaseConfig.get_database_url()
            
            assert 'postgresql://' in url
            assert 'avnadmin:test_password' in url
            assert 'ai-financial-assistant-bollette.e.aivencloud.com' in url
            assert '22782' in url
            assert 'defaultdb' in url
            assert 'sslmode=require' in url
    
    def test_is_development_mode(self):
        """Test development mode detection."""
        with patch.dict(os.environ, {'ENVIRONMENT': 'development'}):
            from src.config.database import DatabaseConfig
            
            assert DatabaseConfig.is_development() is True
    
    def test_is_production_mode(self):
        """Test production mode detection."""
        with patch.dict(os.environ, {'ENVIRONMENT': 'production'}):
            from src.config.database import DatabaseConfig
            
            assert DatabaseConfig.is_development() is False


class TestDatabaseModels:
    """Tests for database models."""
    
    def test_transaction_model_exists(self):
        """Test that Transaction model exists and has required fields."""
        from src.database.models import Transaction
        
        # Check that model has required columns
        assert hasattr(Transaction, 'id')
        assert hasattr(Transaction, 'date')
        assert hasattr(Transaction, 'amount')
        assert hasattr(Transaction, 'category')
        assert hasattr(Transaction, 'description')
        assert hasattr(Transaction, 'currency')
    
    def test_category_model_exists(self):
        """Test that Category model exists and has required fields."""
        from src.database.models import Category
        
        # Check that model has required columns
        assert hasattr(Category, 'id')
        assert hasattr(Category, 'name')
    
    def test_transaction_to_dict(self):
        """Test Transaction to_dict method."""
        from src.database.models import Transaction
        from datetime import date as date_class, timedelta
        
        # Use a date relative to today to avoid future issues
        test_date = date_class.today() - timedelta(days=1)
        
        transaction = Transaction(
            id=1,
            date=test_date,
            amount=-50.0,
            category="food",
            description="Test",
            currency="EUR"
        )
        
        result = transaction.to_dict()
        
        assert result['id'] == 1
        assert result['date'] == test_date.isoformat()
        assert result['amount'] == -50.0
        assert result['category'] == 'food'
        assert result['description'] == 'Test'
        assert result['currency'] == 'EUR'
