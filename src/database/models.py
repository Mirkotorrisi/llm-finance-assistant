"""Database models using SQLAlchemy ORM."""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey, Boolean, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Account(Base):
    """Account model representing a financial account.
    
    Represents a stable financial account over time (checking, credit, cash, investment, etc.)
    """
    
    __tablename__ = "accounts"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    type = Column(String(50), nullable=False)  # checking, credit, cash, investment, etc.
    currency = Column(String(3), nullable=False, default="EUR")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    snapshots = relationship("MonthlyAccountSnapshot", back_populates="account", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="account", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Account(id={self.id}, name='{self.name}', type='{self.type}')>"
    
    def to_dict(self):
        """Convert account to dictionary format.
        
        Returns:
            Dictionary representation of the account
        """
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "currency": self.currency,
            "is_active": self.is_active
        }


class MonthlyAccountSnapshot(Base):
    """CORE ENTITY: Monthly financial state of an account.
    
    This entity represents the monthly financial state of an account
    and maps 1:1 with a single row in a spreadsheet.
    
    Snapshots are the source of truth for balances and totals.
    Transactions do not define balances; they provide optional detail.
    """
    
    __tablename__ = "monthly_account_snapshots"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    year = Column(Integer, nullable=False, index=True)  # YYYY
    month = Column(Integer, nullable=False, index=True)  # 1-12
    starting_balance = Column(Float, nullable=False)
    ending_balance = Column(Float, nullable=False)
    total_income = Column(Float, nullable=False, default=0.0)
    total_expense = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Unique constraint: one snapshot per (account_id, year, month)
    __table_args__ = (
        UniqueConstraint('account_id', 'year', 'month', name='uix_account_year_month'),
    )
    
    # Relationship
    account = relationship("Account", back_populates="snapshots")
    
    def __repr__(self):
        return f"<MonthlyAccountSnapshot(id={self.id}, account_id={self.account_id}, year={self.year}, month={self.month}, ending_balance={self.ending_balance})>"
    
    def to_dict(self):
        """Convert snapshot to dictionary format.
        
        Returns:
            Dictionary representation of the snapshot
        """
        return {
            "id": self.id,
            "account_id": self.account_id,
            "year": self.year,
            "month": self.month,
            "starting_balance": self.starting_balance,
            "ending_balance": self.ending_balance,
            "total_income": self.total_income,
            "total_expense": self.total_expense
        }


class Category(Base):
    """Category model for transaction categories."""
    
    __tablename__ = "categories"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    type = Column(String(10), nullable=False)  # 'income' or 'expense'
    color = Column(String(7), nullable=True)  # Hex color code, e.g., '#FF5733'
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationship
    transactions = relationship("Transaction", back_populates="category_rel")
    
    def __repr__(self):
        return f"<Category(id={self.id}, name='{self.name}', type='{self.type}')>"
    
    def to_dict(self):
        """Convert category to dictionary format.
        
        Returns:
            Dictionary representation of the category
        """
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "color": self.color
        }


class Transaction(Base):
    """Transaction model for financial transactions.
    
    IMPORTANT: Transactions are optional granular details, NOT authoritative for totals.
    They are used for:
    - Detailed views
    - Manual expense entry
    - Fine-grained analytics
    
    Balances come from MonthlyAccountSnapshot, not from aggregating transactions.
    """
    
    __tablename__ = "transactions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    amount = Column(Float, nullable=False)  # positive = income, negative = expense
    category = Column(String(100), nullable=False, index=True)
    description = Column(String(500), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Foreign key (optional - category can be free text or reference to categories table)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    
    # Relationships
    category_rel = relationship("Category", back_populates="transactions")
    account = relationship("Account", back_populates="transactions")
    
    def __repr__(self):
        return f"<Transaction(id={self.id}, date='{self.date}', amount={self.amount}, category='{self.category}')>"
    
    def to_dict(self):
        """Convert transaction to dictionary format.
        
        Returns:
            Dictionary representation of the transaction
        """
        return {
            "id": self.id,
            "account_id": self.account_id,
            "date": self.date.isoformat() if self.date else None,
            "amount": self.amount,
            "category": self.category,
            "description": self.description
        }
