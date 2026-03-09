"""
A remote client for the MCP server, designed to be used by the agent workflow.
"""

import os
import asyncio
from typing import Any, Dict, List, Optional
from mcp import ClientSession
from mcp.client.sse import sse_client
from dotenv import load_dotenv

load_dotenv()

MCP_SERVER_URL = os.getenv("MCP_SERVER_SSE_URL", "")
MCP_SERVER_BASE_URL = os.getenv("MCP_SERVER_BASE_URL", "http://localhost:8000")


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
        response = self.session.post(f"{self.base_url}/api/transactions/bulk", json=transactions)
        response.raise_for_status()
        return response.json()

    def delete_transaction(self, transaction_id):
        response = self.session.delete(f"{self.base_url}/api/transactions/{transaction_id}")
        response.raise_for_status()
        return response.json()

    def get_balance(self):
        response = self.session.get(f"{self.base_url}/api/balance")
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


class MCPClientManager:
    """Manager for the MCP connection via SSE."""
    
    def __init__(self, url: str):
        self.url = url
        self.session: Optional[ClientSession] = None
        self._client_context = None

    async def connect(self):
        """Initialize the SSE connection and MCP session."""
        if self.session:
            return
            
        self._client_context = sse_client(url=self.url)
        read_stream, write_stream = await self._client_context.__aenter__()
        
        self.session = ClientSession(read_stream, write_stream)
        await self.session.initialize()
        print(f"✓ MCP highway opened towards: {self.url}")

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call any tool on the remote server."""
        if not self.session:
            await self.connect()
        
        # This is the standard MCP JSON-RPC call
        result = await self.session.call_tool(tool_name, arguments)
        return result.content

    async def list_tools(self):
        """Get the list of available tools (Discovery)."""
        if not self.session:
            await self.connect()
        return await self.session.list_tools()

    async def disconnect(self):
        """Cleanly close MCP session and transport context."""
        if self.session:
            await self.session.__aexit__(None, None, None)
            self.session = None
        if self._client_context:
            await self._client_context.__aexit__(None, None, None)
            self._client_context = None

# Singleton for the agent
_manager = None
_mcp_client = None


def get_mcp_server():
    """Backward-compatible singleton accessor for legacy imports/tests."""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = RemoteMCPClient(MCP_SERVER_BASE_URL)
    return _mcp_client


def reset_mcp_server():
    """Backward-compatible singleton reset for legacy imports/tests."""
    global _mcp_client
    _mcp_client = None

async def get_mcp_client():
    global _manager
    if _manager is None:
        _manager = MCPClientManager(MCP_SERVER_URL)
        await _manager.connect()
    return _manager