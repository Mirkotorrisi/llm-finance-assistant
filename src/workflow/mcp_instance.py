"""Remote MCP client for the finance-assistant-api server."""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

MCP_SERVER_BASE_URL = os.getenv("MCP_SERVER_BASE_URL", "http://localhost:8000")

# Global MCP client instance
_mcp_client = None


class RemoteMCPClient:
    """HTTP client that delegates all finance actions to the remote MCP server."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def list_transactions(self, category=None, start_date=None, end_date=None):
        """List transactions from the remote server with optional filters."""
        params = {}
        if category:
            params["category"] = category
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        response = self.session.get(f"{self.base_url}/api/transactions", params=params)
        response.raise_for_status()
        return response.json()

    def add_transaction(self, amount, category, description, date=None, currency="EUR"):
        """Add a single transaction via the remote server."""
        data = {
            "amount": amount,
            "category": category,
            "description": description,
            "currency": currency,
        }
        if date:
            data["date"] = date
        response = self.session.post(f"{self.base_url}/api/transactions", json=data)
        response.raise_for_status()
        return response.json()

    def add_transactions_bulk(self, transactions):
        """Add multiple transactions in a single request to the remote server."""
        response = self.session.post(
            f"{self.base_url}/api/transactions/bulk", json=transactions
        )
        response.raise_for_status()
        return response.json()

    def delete_transaction(self, transaction_id):
        """Delete a transaction by ID via the remote server."""
        response = self.session.delete(
            f"{self.base_url}/api/transactions/{transaction_id}"
        )
        response.raise_for_status()
        return response.json()

    def get_balance(self):
        """Get the current total balance from the remote server."""
        response = self.session.get(f"{self.base_url}/api/balance")
        response.raise_for_status()
        return response.json()["balance"]

    def get_existing_categories(self):
        """Return a sorted list of unique transaction categories from the remote server."""
        transactions = self.list_transactions()
        categories = sorted(
            set(t["category"] for t in transactions if t.get("category"))
        )
        return categories

    def get_accounts(self):
        """List all financial accounts from the remote server."""
        response = self.session.get(f"{self.base_url}/api/accounts")
        response.raise_for_status()
        return response.json()

    def get_financial_data(self, year: int):
        """Get aggregated yearly financial data from the remote server."""
        response = self.session.get(f"{self.base_url}/api/financial-data/{year}")
        response.raise_for_status()
        return response.json()


def get_mcp_server():
    """Get the global remote MCP client instance.

    Returns:
        RemoteMCPClient connected to the configured MCP server URL
    """
    global _mcp_client

    if _mcp_client is None:
        _mcp_client = RemoteMCPClient(MCP_SERVER_BASE_URL)
        print(f"✓ Using remote MCP server at {MCP_SERVER_BASE_URL}")

    return _mcp_client


def reset_mcp_server():
    """Reset the MCP client, forcing re-creation on the next call to get_mcp_server."""
    global _mcp_client
    _mcp_client = RemoteMCPClient(MCP_SERVER_BASE_URL)
