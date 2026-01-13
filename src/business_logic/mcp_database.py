"""Database-backed Model Context Protocol (MCP) server for Personal Finance."""

import datetime
from typing import List, Optional
from datetime import date as date_type
from sqlalchemy.orm import Session
from sqlalchemy import and_
from sqlalchemy.exc import IntegrityError

from src.database.models import Transaction, Category, Account, MonthlyAccountSnapshot
from src.database.init import get_db_session


class FinanceMCPDatabase:
    """
    Database-backed Model Context Protocol (MCP) server for Personal Finance.
    Provides APIs for the LLM to interact with the financial data stored in PostgreSQL.
    """
    
    def __init__(self, db_session: Optional[Session] = None):
        """Initialize the MCP server with a database session.
        
        Args:
            db_session: SQLAlchemy database session. If None, creates a new session.
        """
        self.db = db_session if db_session else get_db_session()
        self.owns_session = db_session is None

    def close(self):
        """Close the database session if we own it."""
        if self.owns_session and self.db:
            self.db.close()
            self.owns_session = False

    def __del__(self):
        """Cleanup when object is destroyed."""
        # Close session if it wasn't closed explicitly
        if hasattr(self, 'owns_session') and self.owns_session and hasattr(self, 'db') and self.db:
            try:
                self.db.close()
            except Exception:
                # Ignore errors during cleanup
                pass

    def list_transactions(
        self, 
        category: str = None, 
        start_date: str = None, 
        end_date: str = None
    ) -> List[dict]:
        """List transactions with optional filters.
        
        Args:
            category: Filter by category
            start_date: Filter by start date (ISO format)
            end_date: Filter by end date (ISO format)
            
        Returns:
            List of transactions matching the filters
        """
        query = self.db.query(Transaction)
        
        # Apply filters
        if category:
            query = query.filter(Transaction.category.ilike(category))
        if start_date:
            query = query.filter(Transaction.date >= datetime.datetime.fromisoformat(start_date).date())
        if end_date:
            query = query.filter(Transaction.date <= datetime.datetime.fromisoformat(end_date).date())
        
        # Order by date descending
        query = query.order_by(Transaction.date.desc())
        
        # Convert to dict format
        transactions = query.all()
        return [t.to_dict() for t in transactions]

    def add_transaction(
        self, 
        amount: float, 
        category: str, 
        description: str, 
        date: str = None,
        currency: str = None
    ) -> dict:
        """Add a new transaction.
        
        Args:
            amount: Transaction amount (negative for expenses, positive for income)
            category: Transaction category
            description: Transaction description
            date: Transaction date (ISO format, defaults to today)
            currency: Transaction currency (optional, defaults to EUR)
            
        Returns:
            The newly created transaction as a dictionary
        """
        if not date:
            date = datetime.date.today().isoformat()
        
        # Parse date string to date object
        transaction_date = datetime.datetime.fromisoformat(date).date()
        
        # Create transaction
        new_transaction = Transaction(
            date=transaction_date,
            amount=amount,
            category=category,
            description=description,
            currency=currency or "EUR"
        )
        
        self.db.add(new_transaction)
        self.db.commit()
        self.db.refresh(new_transaction)
        
        # Ensure category exists in categories table
        self._ensure_category_exists(category)
        
        return new_transaction.to_dict()

    def delete_transaction(self, transaction_id: int) -> bool:
        """Delete a transaction by ID.
        
        Args:
            transaction_id: ID of the transaction to delete
            
        Returns:
            True if a transaction was deleted, False otherwise
        """
        transaction = self.db.query(Transaction).filter(Transaction.id == transaction_id).first()
        
        if transaction:
            self.db.delete(transaction)
            self.db.commit()
            return True
        
        return False

    def get_balance(self) -> float:
        """Get the current balance (sum of all transactions).
        
        Returns:
            Current balance
        """
        result = self.db.query(Transaction).all()
        return sum(t.amount for t in result)
    
    def add_transactions_bulk(self, transactions: List[dict]) -> List[dict]:
        """Add multiple transactions at once.
        
        Args:
            transactions: List of transaction dictionaries
            
        Returns:
            List of added transactions as dictionaries
        """
        added_transactions = []
        
        for transaction_data in transactions:
            date_str = transaction_data.get("date")
            if date_str:
                transaction_date = datetime.datetime.fromisoformat(date_str).date()
            else:
                transaction_date = datetime.date.today()
            
            new_transaction = Transaction(
                date=transaction_date,
                amount=transaction_data["amount"],
                category=transaction_data["category"],
                description=transaction_data["description"],
                currency=transaction_data.get("currency", "EUR")
            )
            
            self.db.add(new_transaction)
            added_transactions.append(new_transaction)
            
            # Ensure category exists
            self._ensure_category_exists(transaction_data["category"])
        
        self.db.commit()
        
        # Refresh all transactions to get their IDs
        for t in added_transactions:
            self.db.refresh(t)
        
        return [t.to_dict() for t in added_transactions]
    
    def get_existing_categories(self) -> List[str]:
        """Get a list of unique categories from existing transactions.
        
        Returns:
            List of unique category names
        """
        categories = self.db.query(Category.name).distinct().order_by(Category.name).all()
        return [cat[0] for cat in categories]
    
    def _ensure_category_exists(self, category_name: str) -> None:
        """Ensure a category exists in the categories table.
        
        Args:
            category_name: Name of the category
        """
        existing_category = self.db.query(Category).filter(
            Category.name.ilike(category_name)
        ).first()
        
        if not existing_category:
            new_category = Category(name=category_name.lower())
            self.db.add(new_category)
            try:
                self.db.commit()
            except IntegrityError:
                # Category was added by another process concurrently
                self.db.rollback()
            except Exception as e:
                # Other unexpected database errors
                self.db.rollback()
                # Log but don't fail - category creation is not critical
                print(f"Warning: Failed to create category '{category_name}': {e}")
    
    def get_monthly_snapshots(self, year: int) -> List[dict]:
        """Get all monthly account snapshots for a given year.
        
        Args:
            year: Year to fetch snapshots for (YYYY)
            
        Returns:
            List of monthly snapshots as dictionaries
        """
        snapshots = self.db.query(MonthlyAccountSnapshot).filter(
            MonthlyAccountSnapshot.year == year
        ).order_by(
            MonthlyAccountSnapshot.month,
            MonthlyAccountSnapshot.account_id
        ).all()
        
        return [snapshot.to_dict() for snapshot in snapshots]
    
    def get_accounts(self) -> List[dict]:
        """Get all active accounts.
        
        Returns:
            List of active accounts as dictionaries
        """
        accounts = self.db.query(Account).filter(
            Account.is_active
        ).order_by(Account.name).all()
        
        return [account.to_dict() for account in accounts]
