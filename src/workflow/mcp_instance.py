"""Global MCP server instance management."""

import datetime
from typing import List, Optional

# Global MCP instance
_mcp_server = None


class FinanceMCP:
    """Simple in-memory MCP server for personal finance transactions.

    Acts as a lightweight client-side store until all persistence is
    delegated to finance-assistant-api.
    """

    def __init__(self):
        self.transactions: List[dict] = []
        self.next_id: int = 1

    def list_transactions(
        self,
        category: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[dict]:
        """List transactions with optional filters."""
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
        date: Optional[str] = None,
        currency: Optional[str] = None,
    ) -> dict:
        """Add a new transaction."""
        if not date:
            date = datetime.date.today().isoformat()
        new_entry: dict = {
            "id": self.next_id,
            "date": date,
            "amount": amount,
            "category": category,
            "description": description,
        }
        if currency:
            new_entry["currency"] = currency
        self.transactions.append(new_entry)
        self.next_id += 1
        return new_entry

    def add_transactions_bulk(self, transactions: List[dict]) -> List[dict]:
        """Add multiple transactions at once."""
        return [
            self.add_transaction(
                amount=t["amount"],
                category=t["category"],
                description=t["description"],
                date=t.get("date"),
                currency=t.get("currency"),
            )
            for t in transactions
        ]

    def delete_transaction(self, transaction_id: int) -> bool:
        """Delete a transaction by ID."""
        original_count = len(self.transactions)
        self.transactions = [t for t in self.transactions if t.get("id") != transaction_id]
        return len(self.transactions) < original_count

    def get_balance(self) -> float:
        """Get the current balance (sum of all transaction amounts)."""
        return sum(t["amount"] for t in self.transactions)

    def get_existing_categories(self) -> List[str]:
        """Get a sorted list of unique category names."""
        return sorted({t["category"] for t in self.transactions if t.get("category")})


def get_mcp_server() -> FinanceMCP:
    """Get the global MCP server instance."""
    global _mcp_server
    if _mcp_server is None:
        _mcp_server = FinanceMCP()
        print("✓ Using in-memory transaction storage")
    return _mcp_server


def reset_mcp_server() -> None:
    """Reset the MCP server, discarding all in-memory transactions."""
    global _mcp_server
    _mcp_server = FinanceMCP()
