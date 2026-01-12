"""Service layer for managing monthly account snapshots.

This service implements the core business logic for the monthly-based personal finance model.
Snapshots are the source of truth for balances and totals.
"""

from typing import List, Optional, Dict
from datetime import date as date_type
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, desc

from src.database.models import Account, MonthlyAccountSnapshot, Transaction, Category


class SnapshotService:
    """
    Service for managing monthly account snapshots and accounts.
    
    This service implements the key queries required by the problem statement:
    - Total balance today (sum of ending_balance from most recent snapshot of each account)
    - Total balance last month (same query, but previous month)
    - Total expenses for a month (sum of total_expense across snapshots for that month)
    - Trend over time (using snapshots, not transactions)
    """
    
    def __init__(self, db_session: Session):
        """Initialize the service with a database session.
        
        Args:
            db_session: SQLAlchemy database session
        """
        self.db = db_session
    
    # =========================================================================
    # Account Management
    # =========================================================================
    
    def create_account(
        self, 
        name: str, 
        account_type: str, 
        currency: str = "EUR",
        is_active: bool = True
    ) -> Dict:
        """Create a new financial account.
        
        Args:
            name: Account name
            account_type: Type of account (checking, credit, cash, investment, etc.)
            currency: Currency code (default: EUR)
            is_active: Whether the account is active
            
        Returns:
            Dictionary representation of the created account
        """
        account = Account(
            name=name,
            type=account_type,
            currency=currency,
            is_active=is_active
        )
        self.db.add(account)
        self.db.commit()
        self.db.refresh(account)
        return account.to_dict()
    
    def get_account(self, account_id: int) -> Optional[Dict]:
        """Get an account by ID.
        
        Args:
            account_id: Account ID
            
        Returns:
            Dictionary representation of the account, or None if not found
        """
        account = self.db.query(Account).filter(Account.id == account_id).first()
        return account.to_dict() if account else None
    
    def list_accounts(self, active_only: bool = True) -> List[Dict]:
        """List all accounts.
        
        Args:
            active_only: If True, only return active accounts
            
        Returns:
            List of account dictionaries
        """
        query = self.db.query(Account)
        if active_only:
            query = query.filter(Account.is_active == True)
        
        accounts = query.order_by(Account.name).all()
        return [account.to_dict() for account in accounts]
    
    # =========================================================================
    # Monthly Snapshot Management
    # =========================================================================
    
    def create_snapshot(
        self,
        account_id: int,
        year: int,
        month: int,
        starting_balance: float,
        ending_balance: float,
        total_income: float = 0.0,
        total_expense: float = 0.0
    ) -> Dict:
        """Create a new monthly account snapshot.
        
        Args:
            account_id: Account ID
            year: Year (YYYY)
            month: Month (1-12)
            starting_balance: Starting balance for the month
            ending_balance: Ending balance for the month
            total_income: Total income for the month
            total_expense: Total expense for the month
            
        Returns:
            Dictionary representation of the created snapshot
            
        Raises:
            ValueError: If a snapshot already exists for this account/year/month
        """
        # Check if snapshot already exists
        existing = self.db.query(MonthlyAccountSnapshot).filter(
            and_(
                MonthlyAccountSnapshot.account_id == account_id,
                MonthlyAccountSnapshot.year == year,
                MonthlyAccountSnapshot.month == month
            )
        ).first()
        
        if existing:
            raise ValueError(
                f"Snapshot already exists for account {account_id}, "
                f"year {year}, month {month}"
            )
        
        snapshot = MonthlyAccountSnapshot(
            account_id=account_id,
            year=year,
            month=month,
            starting_balance=starting_balance,
            ending_balance=ending_balance,
            total_income=total_income,
            total_expense=total_expense
        )
        self.db.add(snapshot)
        self.db.commit()
        self.db.refresh(snapshot)
        return snapshot.to_dict()
    
    def update_snapshot(
        self,
        account_id: int,
        year: int,
        month: int,
        starting_balance: Optional[float] = None,
        ending_balance: Optional[float] = None,
        total_income: Optional[float] = None,
        total_expense: Optional[float] = None
    ) -> Optional[Dict]:
        """Update an existing monthly account snapshot.
        
        Args:
            account_id: Account ID
            year: Year (YYYY)
            month: Month (1-12)
            starting_balance: New starting balance (optional)
            ending_balance: New ending balance (optional)
            total_income: New total income (optional)
            total_expense: New total expense (optional)
            
        Returns:
            Dictionary representation of the updated snapshot, or None if not found
        """
        snapshot = self.db.query(MonthlyAccountSnapshot).filter(
            and_(
                MonthlyAccountSnapshot.account_id == account_id,
                MonthlyAccountSnapshot.year == year,
                MonthlyAccountSnapshot.month == month
            )
        ).first()
        
        if not snapshot:
            return None
        
        if starting_balance is not None:
            snapshot.starting_balance = starting_balance
        if ending_balance is not None:
            snapshot.ending_balance = ending_balance
        if total_income is not None:
            snapshot.total_income = total_income
        if total_expense is not None:
            snapshot.total_expense = total_expense
        
        self.db.commit()
        self.db.refresh(snapshot)
        return snapshot.to_dict()
    
    def get_snapshot(
        self, 
        account_id: int, 
        year: int, 
        month: int
    ) -> Optional[Dict]:
        """Get a specific monthly snapshot.
        
        Args:
            account_id: Account ID
            year: Year (YYYY)
            month: Month (1-12)
            
        Returns:
            Dictionary representation of the snapshot, or None if not found
        """
        snapshot = self.db.query(MonthlyAccountSnapshot).filter(
            and_(
                MonthlyAccountSnapshot.account_id == account_id,
                MonthlyAccountSnapshot.year == year,
                MonthlyAccountSnapshot.month == month
            )
        ).first()
        
        return snapshot.to_dict() if snapshot else None
    
    def list_snapshots_for_account(
        self,
        account_id: int,
        start_year: Optional[int] = None,
        start_month: Optional[int] = None,
        end_year: Optional[int] = None,
        end_month: Optional[int] = None
    ) -> List[Dict]:
        """List snapshots for a specific account with optional date range.
        
        Args:
            account_id: Account ID
            start_year: Filter by start year (optional)
            start_month: Filter by start month (optional)
            end_year: Filter by end year (optional)
            end_month: Filter by end month (optional)
            
        Returns:
            List of snapshot dictionaries, ordered by year and month descending
        """
        query = self.db.query(MonthlyAccountSnapshot).filter(
            MonthlyAccountSnapshot.account_id == account_id
        )
        
        # Apply date range filters
        if start_year is not None and start_month is not None:
            query = query.filter(
                (MonthlyAccountSnapshot.year > start_year) |
                (
                    (MonthlyAccountSnapshot.year == start_year) &
                    (MonthlyAccountSnapshot.month >= start_month)
                )
            )
        
        if end_year is not None and end_month is not None:
            query = query.filter(
                (MonthlyAccountSnapshot.year < end_year) |
                (
                    (MonthlyAccountSnapshot.year == end_year) &
                    (MonthlyAccountSnapshot.month <= end_month)
                )
            )
        
        snapshots = query.order_by(
            desc(MonthlyAccountSnapshot.year),
            desc(MonthlyAccountSnapshot.month)
        ).all()
        
        return [snapshot.to_dict() for snapshot in snapshots]
    
    # =========================================================================
    # Key Queries (As Specified in Problem Statement)
    # =========================================================================
    
    def get_total_balance_for_month(self, year: int, month: int) -> float:
        """Get total balance across all accounts for a specific month.
        
        This sums the ending_balance from snapshots for the given month.
        
        Args:
            year: Year (YYYY)
            month: Month (1-12)
            
        Returns:
            Total balance across all accounts
        """
        result = self.db.query(
            func.sum(MonthlyAccountSnapshot.ending_balance)
        ).filter(
            and_(
                MonthlyAccountSnapshot.year == year,
                MonthlyAccountSnapshot.month == month
            )
        ).scalar()
        
        return result or 0.0
    
    def get_current_total_balance(self) -> float:
        """Get current total balance across all accounts.
        
        This finds the most recent snapshot for each account and sums their ending_balance.
        This is one of the key queries mentioned in the problem statement.
        
        Returns:
            Current total balance
        """
        # Get the most recent snapshot for each account
        subquery = self.db.query(
            MonthlyAccountSnapshot.account_id,
            func.max(MonthlyAccountSnapshot.year * 100 + MonthlyAccountSnapshot.month).label('max_period')
        ).group_by(MonthlyAccountSnapshot.account_id).subquery()
        
        # Join with snapshots to get the actual ending balances
        result = self.db.query(
            func.sum(MonthlyAccountSnapshot.ending_balance)
        ).join(
            subquery,
            and_(
                MonthlyAccountSnapshot.account_id == subquery.c.account_id,
                MonthlyAccountSnapshot.year * 100 + MonthlyAccountSnapshot.month == subquery.c.max_period
            )
        ).scalar()
        
        return result or 0.0
    
    def get_total_expenses_for_month(self, year: int, month: int) -> float:
        """Get total expenses for a specific month across all accounts.
        
        This is one of the key queries mentioned in the problem statement.
        
        Args:
            year: Year (YYYY)
            month: Month (1-12)
            
        Returns:
            Total expenses for the month
        """
        result = self.db.query(
            func.sum(MonthlyAccountSnapshot.total_expense)
        ).filter(
            and_(
                MonthlyAccountSnapshot.year == year,
                MonthlyAccountSnapshot.month == month
            )
        ).scalar()
        
        return result or 0.0
    
    def get_total_income_for_month(self, year: int, month: int) -> float:
        """Get total income for a specific month across all accounts.
        
        Args:
            year: Year (YYYY)
            month: Month (1-12)
            
        Returns:
            Total income for the month
        """
        result = self.db.query(
            func.sum(MonthlyAccountSnapshot.total_income)
        ).filter(
            and_(
                MonthlyAccountSnapshot.year == year,
                MonthlyAccountSnapshot.month == month
            )
        ).scalar()
        
        return result or 0.0
    
    def get_balance_trend(
        self,
        account_id: Optional[int] = None,
        num_months: int = 12
    ) -> List[Dict]:
        """Get balance trend over time.
        
        This returns historical snapshots to show trends, which is one of the
        key queries mentioned in the problem statement.
        
        Args:
            account_id: Optional account ID to filter by specific account
            num_months: Number of months to retrieve (default: 12)
            
        Returns:
            List of snapshots showing the trend, ordered by date descending
        """
        query = self.db.query(MonthlyAccountSnapshot)
        
        if account_id is not None:
            query = query.filter(MonthlyAccountSnapshot.account_id == account_id)
        
        snapshots = query.order_by(
            desc(MonthlyAccountSnapshot.year),
            desc(MonthlyAccountSnapshot.month)
        ).limit(num_months).all()
        
        return [snapshot.to_dict() for snapshot in snapshots]
    
    # =========================================================================
    # Transaction Support (Optional)
    # =========================================================================
    
    def add_transaction(
        self,
        account_id: int,
        date: date_type,
        amount: float,
        category: str,
        description: str,
        category_id: Optional[int] = None
    ) -> Dict:
        """Add a transaction to an account.
        
        Note: Transactions are optional details and do NOT affect balances.
        Balances come from snapshots.
        
        Args:
            account_id: Account ID
            date: Transaction date
            amount: Amount (positive = income, negative = expense)
            category: Category name
            description: Transaction description
            category_id: Optional category ID reference
            
        Returns:
            Dictionary representation of the created transaction
        """
        transaction = Transaction(
            account_id=account_id,
            date=date,
            amount=amount,
            category=category,
            description=description,
            category_id=category_id
        )
        self.db.add(transaction)
        self.db.commit()
        self.db.refresh(transaction)
        return transaction.to_dict()
    
    def list_transactions_for_account(
        self,
        account_id: int,
        start_date: Optional[date_type] = None,
        end_date: Optional[date_type] = None
    ) -> List[Dict]:
        """List transactions for a specific account.
        
        Args:
            account_id: Account ID
            start_date: Filter by start date (optional)
            end_date: Filter by end date (optional)
            
        Returns:
            List of transaction dictionaries
        """
        query = self.db.query(Transaction).filter(
            Transaction.account_id == account_id
        )
        
        if start_date:
            query = query.filter(Transaction.date >= start_date)
        if end_date:
            query = query.filter(Transaction.date <= end_date)
        
        transactions = query.order_by(desc(Transaction.date)).all()
        return [t.to_dict() for t in transactions]
    
    # =========================================================================
    # Category Management
    # =========================================================================
    
    def create_category(
        self,
        name: str,
        category_type: str,
        color: Optional[str] = None
    ) -> Dict:
        """Create a new category.
        
        Args:
            name: Category name
            category_type: Type ('income' or 'expense')
            color: Hex color code (optional)
            
        Returns:
            Dictionary representation of the created category
        """
        category = Category(
            name=name,
            type=category_type,
            color=color
        )
        self.db.add(category)
        self.db.commit()
        self.db.refresh(category)
        return category.to_dict()
    
    def list_categories(self, category_type: Optional[str] = None) -> List[Dict]:
        """List all categories.
        
        Args:
            category_type: Optional filter by type ('income' or 'expense')
            
        Returns:
            List of category dictionaries
        """
        query = self.db.query(Category)
        
        if category_type:
            query = query.filter(Category.type == category_type)
        
        categories = query.order_by(Category.name).all()
        return [cat.to_dict() for cat in categories]
