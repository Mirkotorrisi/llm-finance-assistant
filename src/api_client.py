"""HTTP client the gateway uses to reach finance-assistant-api."""

import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, List, Optional

import httpx

FINANCE_API_URL = os.getenv("FINANCE_API_URL", "http://localhost:8080")

# Who and which project the current request works for. The client is shared, so these travel in the
# execution context instead of in the client; every call sends them as X-User-Id / X-Workspace-Id.
_workspace_id: ContextVar[Optional[int]] = ContextVar("finance_workspace_id", default=None)
_user_id: ContextVar[Optional[str]] = ContextVar("finance_user_id", default=None)


@contextmanager
def request_context(workspace_id: Optional[int] = None, user_id: Optional[str] = None) -> Iterator[None]:
    """Scope every API call made inside the block to a workspace and user."""
    tokens = (_workspace_id.set(workspace_id), _user_id.set(user_id))
    try:
        yield
    finally:
        _workspace_id.reset(tokens[0])
        _user_id.reset(tokens[1])


def scope_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if _workspace_id.get() is not None:
        headers["X-Workspace-Id"] = str(_workspace_id.get())
    if _user_id.get():
        headers["X-User-Id"] = str(_user_id.get())
    return headers


class FinanceApiClient:
    """Async HTTP client for the finance-assistant-api REST endpoints.

    ``call_tool`` maps a tool name to a REST call. These names are the gateway's own; the agent-facing
    tool set lives in ``finance-mcp``.
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
        "get_merchant_rules":       ("GET",    "/api/merchant-rules"),
        "recategorize_transactions": ("POST",  "/api/merchant-rules/recategorize"),
        "update_transactions_bulk_category": ("PUT", "/api/transactions/bulk-category"),
        "get_user_preferences":     ("GET",    "/api/preferences/{user_id}"),
        "update_user_preferences":  ("PUT",    "/api/preferences/{user_id}"),
        "confirm_user_preferences": ("POST",   "/api/preferences/{user_id}/confirm"),
        "import_statement":         ("POST",   "/api/imports"),
        "resolve_merchants":        ("POST",   "/api/merchant-rules/resolve"),
        "get_blueprint":            ("GET",    "/api/workspaces/current/blueprint"),
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

        headers = scope_headers()
        if method == "GET":
            resp = await client.get(path, params=body_args or None, headers=headers)
        elif method == "POST":
            # add_transactions_bulk expects a list at the root; everything else is a dict
            json_body = body_args["transactions"] if tool_name == "add_transactions_bulk" else body_args
            resp = await client.post(path, json=json_body, headers=headers)
        elif method == "PUT":
            resp = await client.put(path, json=body_args, headers=headers)
        elif method == "DELETE":
            resp = await client.delete(path, headers=headers)
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


def _path_keys(template: str) -> List[str]:
    """Return all {key} names in a path template."""
    return re.findall(r"\{(\w+)\}", template)


_api_client: Optional[FinanceApiClient] = None


async def get_api_client() -> FinanceApiClient:
    """Return the shared client."""
    global _api_client
    if _api_client is None:
        _api_client = FinanceApiClient(FINANCE_API_URL)
    return _api_client


def reset_api_client() -> None:
    """Drop the shared client (it is recreated on next use)."""
    global _api_client
    _api_client = None
