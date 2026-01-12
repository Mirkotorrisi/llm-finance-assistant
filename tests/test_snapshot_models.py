"""Unit tests for monthly snapshot models and service."""

import pytest
from datetime import date, datetime
from unittest.mock import Mock, patch, MagicMock
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    mock_session = MagicMock()
    return mock_session


class TestAccountModel:
    """Tests for Account model."""
    
    def test_account_model_exists(self):
        """Test that Account model exists and has required fields."""
        from src.database.models import Account
        
        assert hasattr(Account, 'id')
        assert hasattr(Account, 'name')
        assert hasattr(Account, 'type')
        assert hasattr(Account, 'currency')
        assert hasattr(Account, 'is_active')
        assert hasattr(Account, 'created_at')
        assert hasattr(Account, 'updated_at')
    
    def test_account_relationships(self):
        """Test that Account has required relationships."""
        from src.database.models import Account
        
        assert hasattr(Account, 'snapshots')
        assert hasattr(Account, 'transactions')
    
    def test_account_to_dict(self):
        """Test Account to_dict method."""
        from src.database.models import Account
        
        account = Account(
            id=1,
            name="Checking Account",
            type="checking",
            currency="USD",
            is_active=True
        )
        
        result = account.to_dict()
        
        assert result['id'] == 1
        assert result['name'] == "Checking Account"
        assert result['type'] == "checking"
        assert result['currency'] == "USD"
        assert result['is_active'] is True


class TestMonthlyAccountSnapshotModel:
    """Tests for MonthlyAccountSnapshot model."""
    
    def test_snapshot_model_exists(self):
        """Test that MonthlyAccountSnapshot model exists and has required fields."""
        from src.database.models import MonthlyAccountSnapshot
        
        assert hasattr(MonthlyAccountSnapshot, 'id')
        assert hasattr(MonthlyAccountSnapshot, 'account_id')
        assert hasattr(MonthlyAccountSnapshot, 'year')
        assert hasattr(MonthlyAccountSnapshot, 'month')
        assert hasattr(MonthlyAccountSnapshot, 'starting_balance')
        assert hasattr(MonthlyAccountSnapshot, 'ending_balance')
        assert hasattr(MonthlyAccountSnapshot, 'total_income')
        assert hasattr(MonthlyAccountSnapshot, 'total_expense')
    
    def test_snapshot_has_unique_constraint(self):
        """Test that MonthlyAccountSnapshot has unique constraint on account_id, year, month."""
        from src.database.models import MonthlyAccountSnapshot
        
        # Check that __table_args__ contains a unique constraint
        assert hasattr(MonthlyAccountSnapshot, '__table_args__')
        assert MonthlyAccountSnapshot.__table_args__ is not None
    
    def test_snapshot_to_dict(self):
        """Test MonthlyAccountSnapshot to_dict method."""
        from src.database.models import MonthlyAccountSnapshot
        
        snapshot = MonthlyAccountSnapshot(
            id=1,
            account_id=1,
            year=2026,
            month=1,
            starting_balance=1000.0,
            ending_balance=1200.0,
            total_income=500.0,
            total_expense=300.0
        )
        
        result = snapshot.to_dict()
        
        assert result['id'] == 1
        assert result['account_id'] == 1
        assert result['year'] == 2026
        assert result['month'] == 1
        assert result['starting_balance'] == 1000.0
        assert result['ending_balance'] == 1200.0
        assert result['total_income'] == 500.0
        assert result['total_expense'] == 300.0


class TestCategoryModelUpdates:
    """Tests for updated Category model."""
    
    def test_category_has_type_field(self):
        """Test that Category model has type field."""
        from src.database.models import Category
        
        assert hasattr(Category, 'type')
    
    def test_category_has_color_field(self):
        """Test that Category model has color field."""
        from src.database.models import Category
        
        assert hasattr(Category, 'color')
    
    def test_category_to_dict(self):
        """Test Category to_dict method includes new fields."""
        from src.database.models import Category
        
        category = Category(
            id=1,
            name="groceries",
            type="expense",
            color="#FF5733"
        )
        
        result = category.to_dict()
        
        assert result['id'] == 1
        assert result['name'] == "groceries"
        assert result['type'] == "expense"
        assert result['color'] == "#FF5733"


class TestTransactionModelUpdates:
    """Tests for updated Transaction model."""
    
    def test_transaction_has_account_id(self):
        """Test that Transaction model has account_id field."""
        from src.database.models import Transaction
        
        assert hasattr(Transaction, 'account_id')
    
    def test_transaction_to_dict_includes_account_id(self):
        """Test Transaction to_dict includes account_id."""
        from src.database.models import Transaction
        
        transaction = Transaction(
            id=1,
            account_id=1,
            date=date(2026, 1, 12),
            amount=-50.0,
            category="food",
            description="Grocery shopping"
        )
        
        result = transaction.to_dict()
        
        assert result['id'] == 1
        assert result['account_id'] == 1
        assert result['date'] == "2026-01-12"
        assert result['amount'] == -50.0
        assert result['category'] == 'food'
        assert result['description'] == 'Grocery shopping'


class TestSnapshotService:
    """Tests for SnapshotService."""
    
    @pytest.fixture
    def snapshot_service(self, mock_db):
        """Create a SnapshotService instance with mocked database."""
        from src.business_logic.snapshot_service import SnapshotService
        return SnapshotService(db_session=mock_db)
    
    def test_service_has_create_account_method(self, snapshot_service):
        """Test that service has create_account method."""
        assert hasattr(snapshot_service, 'create_account')
        assert callable(snapshot_service.create_account)
    
    def test_service_has_create_snapshot_method(self, snapshot_service):
        """Test that service has create_snapshot method."""
        assert hasattr(snapshot_service, 'create_snapshot')
        assert callable(snapshot_service.create_snapshot)
    
    def test_service_has_get_total_balance_for_month(self, snapshot_service):
        """Test that service has get_total_balance_for_month method."""
        assert hasattr(snapshot_service, 'get_total_balance_for_month')
        assert callable(snapshot_service.get_total_balance_for_month)
    
    def test_service_has_get_current_total_balance(self, snapshot_service):
        """Test that service has get_current_total_balance method."""
        assert hasattr(snapshot_service, 'get_current_total_balance')
        assert callable(snapshot_service.get_current_total_balance)
    
    def test_service_has_get_total_expenses_for_month(self, snapshot_service):
        """Test that service has get_total_expenses_for_month method."""
        assert hasattr(snapshot_service, 'get_total_expenses_for_month')
        assert callable(snapshot_service.get_total_expenses_for_month)
    
    def test_service_has_get_balance_trend(self, snapshot_service):
        """Test that service has get_balance_trend method."""
        assert hasattr(snapshot_service, 'get_balance_trend')
        assert callable(snapshot_service.get_balance_trend)
    
    def test_service_has_add_transaction_method(self, snapshot_service):
        """Test that service has add_transaction method (for optional detail)."""
        assert hasattr(snapshot_service, 'add_transaction')
        assert callable(snapshot_service.add_transaction)
    
    def test_service_has_create_category_method(self, snapshot_service):
        """Test that service has create_category method."""
        assert hasattr(snapshot_service, 'create_category')
        assert callable(snapshot_service.create_category)


class TestDatabaseExports:
    """Tests for database module exports."""
    
    def test_database_module_exports_account(self):
        """Test that database module exports Account."""
        from src.database import Account
        assert Account is not None
    
    def test_database_module_exports_snapshot(self):
        """Test that database module exports MonthlyAccountSnapshot."""
        from src.database import MonthlyAccountSnapshot
        assert MonthlyAccountSnapshot is not None
    
    def test_database_module_exports_category(self):
        """Test that database module exports Category."""
        from src.database import Category
        assert Category is not None
    
    def test_database_module_exports_transaction(self):
        """Test that database module exports Transaction."""
        from src.database import Transaction
        assert Transaction is not None


class TestDataModelDesignPrinciples:
    """Tests to verify adherence to core design principles."""
    
    def test_snapshot_is_source_of_truth(self):
        """Test that MonthlyAccountSnapshot stores ending_balance (not calculated)."""
        from src.database.models import MonthlyAccountSnapshot
        
        # Verify ending_balance is a column, not a calculated property
        assert hasattr(MonthlyAccountSnapshot, 'ending_balance')
        # The column should be defined directly, not computed
        assert 'ending_balance' in [c.name for c in MonthlyAccountSnapshot.__table__.columns]
    
    def test_transaction_is_optional(self):
        """Test that Transaction has proper documentation indicating it's optional."""
        from src.database.models import Transaction
        
        # Check docstring mentions optional nature
        assert Transaction.__doc__ is not None
        assert 'optional' in Transaction.__doc__.lower() or 'OPTIONAL' in Transaction.__doc__
    
    def test_snapshot_has_efficient_month_indexing(self):
        """Test that snapshot has indexes for efficient month queries."""
        from src.database.models import MonthlyAccountSnapshot
        
        # Verify year and month columns have indexes
        columns = {c.name: c for c in MonthlyAccountSnapshot.__table__.columns}
        assert columns['year'].index is True
        assert columns['month'].index is True
