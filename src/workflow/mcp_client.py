"""Remote MCP client facade for finance operations.

This module is the single data-entry gateway for finance operations in this
agent. It delegates all finance data actions to the remote MCP server and
keeps business logic out of this repository.
"""

import os
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

MCP_SERVER_BASE_URL = os.getenv("MCP_SERVER_BASE_URL", "http://localhost:8000")

_mcp_client = None


class RemoteMCPClient:
    """HTTP client facade that delegates finance actions to remote MCP server."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def list_transactions(
        self,
        category: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, str] = {}
        if category:
            params["category"] = category
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date

        response = self.session.get(f"{self.base_url}/api/transactions", params=params)
        response.raise_for_status()
        return response.json()

    def add_transaction(
        self,
        amount: float,
        category: str,
        description: str,
        date: Optional[str] = None,
        currency: str = "EUR",
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "amount": amount,
            "category": category,
            "description": description,
            "currency": currency,
        }
        if date:
            payload["date"] = date

        response = self.session.post(f"{self.base_url}/api/transactions", json=payload)
        response.raise_for_status()
        return response.json()

    def add_transactions_bulk(self, transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        response = self.session.post(f"{self.base_url}/api/transactions/bulk", json=transactions)
        response.raise_for_status()
        return response.json()

    def delete_transaction(self, transaction_id: int) -> Dict[str, Any]:
        response = self.session.delete(f"{self.base_url}/api/transactions/{transaction_id}")
        response.raise_for_status()
        return response.json()

    def get_balance(self) -> float:
        response = self.session.get(f"{self.base_url}/api/balance")
        response.raise_for_status()
        data = response.json()
        return data["balance"]

    def get_existing_categories(self) -> List[str]:
        transactions = self.list_transactions()
        categories = sorted(
            {transaction["category"] for transaction in transactions if transaction.get("category")}
        )
        return categories

    def get_accounts(self) -> List[Dict[str, Any]]:
        response = self.session.get(f"{self.base_url}/api/accounts")
        response.raise_for_status()
        return response.json()

    def get_financial_data(self, year: int) -> Dict[str, Any]:
        response = self.session.get(f"{self.base_url}/api/financial-data/{year}")
        response.raise_for_status()
        return response.json()


def get_mcp_server() -> RemoteMCPClient:
    """Return singleton remote MCP client instance."""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = RemoteMCPClient(MCP_SERVER_BASE_URL)
    return _mcp_client


def get_mcp_client() -> RemoteMCPClient:
    """Alias for MCP client accessor."""
    return get_mcp_server()


def reset_mcp_server() -> None:
    """Reset singleton MCP client instance."""
    global _mcp_client
    _mcp_client = RemoteMCPClient(MCP_SERVER_BASE_URL)