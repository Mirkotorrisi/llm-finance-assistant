"""Model Context Protocol (MCP) server for Personal Finance."""

import datetime
from typing import List


class FinanceMCP:
    """
    Simulates a Model Context Protocol (MCP) server for Personal Finance.
    Provides APIs for the LLM to interact with the financial data.
    """
    
    def __init__(self, initial_transactions: List[dict]):
        """Initialize the MCP server with initial transactions.
        
        Args:
            initial_transactions: List of transaction dictionaries
        """
        self.transactions = initial_transactions
        self._set_next_id()

    def _set_next_id(self):
        """Set the next ID for new transactions."""
        if not self.transactions:
            self.next_id = 1
        else:
            self.next_id = max(t.get("id", 0) for t in self.transactions) + 1

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
        results = self.transactions
        if category:
            results = [t for t in results if t["category"].lower() == category.lower()]
        if start_date:
            results = [t for t in results if t["date"] >= start_date]
        if end_date:
            results = [t for t in results if t["date"] <= end_date]
        return results

    def add_transaction(
        self, 
        amount: float, 
        category: str, 
        description: str, 
        date: str = None
    ) -> dict:
        """Add a new transaction.
        
        Args:
            amount: Transaction amount (negative for expenses, positive for income)
            category: Transaction category
            description: Transaction description
            date: Transaction date (ISO format, defaults to today)
            
        Returns:
            The newly created transaction
        """
        if not date:
            date = datetime.date.today().isoformat()
        new_entry = {
            "id": self.next_id,
            "date": date,
            "amount": amount,
            "category": category,
            "description": description
        }
        self.transactions.append(new_entry)
        self.next_id += 1
        return new_entry

    def delete_transaction(self, transaction_id: int) -> bool:
        """Delete a transaction by ID.
        
        Args:
            transaction_id: ID of the transaction to delete
            
        Returns:
            True if a transaction was deleted, False otherwise
        """
        original_count = len(self.transactions)
        self.transactions = [t for t in self.transactions if t.get("id") != transaction_id]
        return len(self.transactions) < original_count

    def get_balance(self) -> float:
        """Get the current balance (sum of all transactions).
        
        Returns:
            Current balance
        """
        return sum(t["amount"] for t in self.transactions)
