"""Database models using SQLAlchemy ORM."""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Category(Base):
    """Category model for transaction categories."""
    
    __tablename__ = "categories"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationship
    transactions = relationship("Transaction", back_populates="category_rel")
    
    def __repr__(self):
        return f"<Category(id={self.id}, name='{self.name}')>"


class Transaction(Base):
    """Transaction model for financial transactions."""
    
    __tablename__ = "transactions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    amount = Column(Float, nullable=False)
    category = Column(String(100), nullable=False, index=True)
    description = Column(String(500), nullable=False)
    currency = Column(String(3), default="EUR")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Foreign key (optional - category can be free text or reference to categories table)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    
    # Relationship
    category_rel = relationship("Category", back_populates="transactions")
    
    def __repr__(self):
        return f"<Transaction(id={self.id}, date='{self.date}', amount={self.amount}, category='{self.category}')>"
    
    def to_dict(self):
        """Convert transaction to dictionary format.
        
        Returns:
            Dictionary representation of the transaction
        """
        return {
            "id": self.id,
            "date": self.date.isoformat() if self.date else None,
            "amount": self.amount,
            "category": self.category,
            "description": self.description,
            "currency": self.currency
        }
