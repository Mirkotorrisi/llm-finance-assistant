"""Actions the chat agent may only propose.

Destructive tools (``finance_mcp.CONFIRM_TOOLS``) are not executed in a chat session: finance-mcp returns a
``pending_confirmation`` result instead (see ``FINANCE_MCP_CONFIRM_WRITES``). Both backends hand that result here,
which remembers it with the session's identity and gives back a ``ConfirmAction`` UI component. The app shows the
card; when the user clicks Confirm, ``POST /chat/confirm`` pops the action and runs the tool with the identity the
agent had (never one the client sends), so the model can neither run it nor forge a confirmation.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from finance_mcp import ApiClient, ToolError, call_tool, is_pending_confirmation

PENDING_TTL = float(os.getenv("FINANCE_CONFIRM_TTL", "900"))  # 15 minutes to click
MAX_PENDING = 500


def confirm_writes_enabled() -> bool:
    """``FINANCE_CHAT_CONFIRM_WRITES`` (default on): destructive tools in the chat wait for the user's click."""
    return os.getenv("FINANCE_CHAT_CONFIRM_WRITES", "1").strip().lower() not in ("0", "false", "no", "off")


@dataclass
class PendingAction:
    confirmation_id: str
    session_id: str
    user_id: str
    workspace_id: Optional[int]
    tool: str
    args: Dict[str, Any]
    created: float = field(default_factory=time.monotonic)

    def component(self, order: int = 0) -> Dict[str, Any]:
        return {
            "type": "ConfirmAction",
            "order": order,
            "action": {
                "service": "agent",
                "method": "confirm",
                "params": {"confirmationId": self.confirmation_id, "tool": self.tool, "args": self.args},
            },
        }


class PendingActions:
    def __init__(self, ttl: float = PENDING_TTL, max_items: int = MAX_PENDING) -> None:
        self.ttl = ttl
        self.max_items = max_items
        self._items: Dict[str, PendingAction] = {}

    def _prune(self) -> None:
        now = time.monotonic()
        for key, item in list(self._items.items()):
            if now - item.created > self.ttl:
                self._items.pop(key, None)
        while len(self._items) > self.max_items:
            oldest = min(self._items, key=lambda k: self._items[k].created)
            self._items.pop(oldest, None)

    def register(
        self, result: Any, session_id: str, user_id: str, workspace_id: Optional[int], order: int = 0
    ) -> Optional[Dict[str, Any]]:
        """Remember a ``pending_confirmation`` tool result; returns the component to show, or None if it is not one."""
        if not is_pending_confirmation(result):
            return None
        self._prune()
        item = PendingAction(
            confirmation_id=str(result["confirmation_id"]),
            session_id=session_id,
            user_id=user_id,
            workspace_id=workspace_id,
            tool=str(result["tool"]),
            args=dict(result.get("args") or {}),
        )
        self._items[item.confirmation_id] = item
        return item.component(order)

    def pop(self, confirmation_id: str) -> Optional[PendingAction]:
        self._prune()
        return self._items.pop(confirmation_id, None)

    def __len__(self) -> int:
        return len(self._items)


pending_actions = PendingActions()


async def execute(item: PendingAction, api_url: str) -> Any:
    """Run the confirmed tool as the user the agent was acting for."""
    api = ApiClient(base_url=api_url, default_user_id=item.user_id, workspace_id=item.workspace_id)
    try:
        return await call_tool(api, item.tool, item.args, confirm=False)
    finally:
        await api.aclose()


__all__ = ["PendingAction", "PendingActions", "ToolError", "confirm_writes_enabled", "execute", "pending_actions"]
