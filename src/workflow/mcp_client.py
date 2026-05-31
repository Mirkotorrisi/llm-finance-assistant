"""
API client for the finance-assistant-api service.

Previously used the MCP-over-SSE protocol, replaced with direct async HTTP calls
because the fastapi-mcp SSE handshake was incompatible with the mcp client library
version, causing the initialize() call to hang indefinitely.
"""

import os
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

FINANCE_API_URL = os.getenv("FINANCE_API_URL", "http://localhost:8080")

# Legacy env vars kept for backward compatibility with tests
MCP_SERVER_BASE_URL = os.getenv("MCP_SERVER_BASE_URL", "http://localhost:8080")


class RemoteMCPClient:
    """Backward-compatible synchronous HTTP client used by legacy imports/tests."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.session = None

    def list_transactions(self, category=None, start_date=None, end_date=None):
        params = {}
        if category is not None:
            params["category"] = category
        if start_date is not None:
            params["start_date"] = start_date
        if end_date is not None:
            params["end_date"] = end_date
        response = self.session.get(f"{self.base_url}/api/transactions", params=params)
        response.raise_for_status()
        return response.json()

    def add_transaction(self, amount, category, description, date=None, currency="EUR"):
        payload = {
            "amount": amount,
            "category": category,
            "description": description,
            "currency": currency,
        }
        if date is not None:
            payload["date"] = date
        response = self.session.post(f"{self.base_url}/api/transactions", json=payload)
        response.raise_for_status()
        return response.json()

    def add_transactions_bulk(self, transactions):
        response = self.session.post(
            f"{self.base_url}/api/transactions/bulk", json=transactions
        )
        response.raise_for_status()
        return response.json()

    def delete_transaction(self, transaction_id):
        response = self.session.delete(
            f"{self.base_url}/api/transactions/{transaction_id}"
        )
        response.raise_for_status()
        return response.json()

    def get_balance(self):
        response = self.session.get(f"{self.base_url}/api/transactions/balance")
        response.raise_for_status()
        return response.json().get("balance")

    def get_existing_categories(self):
        transactions = self.list_transactions()
        categories = {t.get("category") for t in transactions if t.get("category")}
        return sorted(categories)

    def get_accounts(self):
        response = self.session.get(f"{self.base_url}/api/accounts")
        response.raise_for_status()
        return response.json()

    def get_financial_data(self, year):
        response = self.session.get(f"{self.base_url}/api/financial-data/{year}")
        response.raise_for_status()
        return response.json()


class DirectAPIClient:
    """Async HTTP client that talks directly to the finance-assistant-api REST endpoints.

    Exposes a call_tool() interface identical to the old MCPClientManager so that
    nodes.py and statements.py require no changes.
    """

    # Maps MCP-style tool names to (method, path_template) pairs.
    # Path templates may contain {key} placeholders resolved from arguments.
    _TOOL_MAP: Dict[str, tuple] = {
        "list_transactions":        ("GET",    "/api/transactions"),
        "get_distinct_categories":  ("GET",    "/api/transactions/categories"),
        "add_transaction":          ("POST",   "/api/transactions"),
        "add_transactions_bulk":    ("POST",   "/api/transactions/bulk"),
        "delete_transaction":       ("DELETE", "/api/transactions/{transaction_id}"),
        "get_balance":              ("GET",    "/api/transactions/balance"),
    }

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=30.0
            )
        return self._client

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        if tool_name not in self._TOOL_MAP:
            raise ValueError(f"Unknown tool: {tool_name}")

        method, path_template = self._TOOL_MAP[tool_name]
        client = self._get_client()

        # Resolve path placeholders (e.g. {transaction_id}) from arguments
        path_args = {}
        body_args = dict(arguments)
        for key in _path_keys(path_template):
            if key in body_args:
                path_args[key] = body_args.pop(key)
        path = path_template.format(**path_args)

        if method == "GET":
            resp = await client.get(path, params=body_args or None)
        elif method == "POST":
            # add_transactions_bulk expects a list at the root; everything else is a dict
            json_body = body_args.get("transactions", body_args) if "transactions" in body_args else body_args
            resp = await client.post(path, json=json_body)
        elif method == "DELETE":
            resp = await client.delete(path)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        resp.raise_for_status()
        return resp.json()

    async def disconnect(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


def _path_keys(template: str) -> List[str]:
    """Return all {key} names in a path template."""
    import re
    return re.findall(r"\{(\w+)\}", template)


# ── Singletons ────────────────────────────────────────────────────────────────

_api_client: Optional[DirectAPIClient] = None
_mcp_client: Optional[RemoteMCPClient] = None


async def get_mcp_client() -> DirectAPIClient:
    """Return the shared async API client (previously the MCP client)."""
    global _api_client
    if _api_client is None:
        _api_client = DirectAPIClient(FINANCE_API_URL)
    return _api_client


def reset_mcp_client() -> None:
    """Reset the async client singleton."""
    global _api_client
    _api_client = None


def get_mcp_server() -> RemoteMCPClient:
    """Backward-compatible synchronous client for legacy imports/tests."""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = RemoteMCPClient(MCP_SERVER_BASE_URL)
    return _mcp_client


def reset_mcp_server() -> None:
    """Backward-compatible reset for legacy imports/tests."""
    global _mcp_client
    _mcp_client = None
