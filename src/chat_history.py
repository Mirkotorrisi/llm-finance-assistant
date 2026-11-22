"""The chat history, written to the API as the turns run.

The gateway records the user's message when a turn starts and the assistant's answer (text, components, tools
called, or the error) when it ends, under ``/api/chat-sessions`` of finance-assistant-api, with the identity of
the chat session (user and project). Recording is best effort: the API being down must never break the chat, so
every failure is logged and swallowed.

When the agent has no live session for a chat (gateway restarted, session expired) the stored transcript is
prepended to the next message, so the conversation picks up where it left off whichever engine answers.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

TRANSCRIPT_MESSAGES = 20
TRANSCRIPT_CHARS = 12_000


def enabled() -> bool:
    return os.getenv("FINANCE_CHAT_HISTORY", "on").strip().lower() not in ("off", "0", "false", "no")


class ChatHistoryStore:
    def __init__(self, api_url: str, timeout: float = 5.0):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._warned = False

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=self.api_url, timeout=self.timeout)
        return self._client

    @staticmethod
    def _headers(user_id: str, workspace_id: Optional[int]) -> dict[str, str]:
        headers = {"X-User-Id": user_id}
        if workspace_id is not None:
            headers["X-Workspace-Id"] = str(workspace_id)
        return headers

    def _unavailable(self, exc: Exception) -> None:
        # one warning per outage, not one per message
        if not self._warned:
            logger.warning("Chat history not recorded (API unreachable or refused): %s", exc)
            self._warned = True

    async def record(
        self,
        session_id: str,
        user_id: str,
        workspace_id: Optional[int],
        role: str,
        content: str,
        components: list[dict[str, Any]] | None = None,
        tools: list[str] | None = None,
        error: str | None = None,
        backend: str | None = None,
    ) -> None:
        if not enabled():
            return
        body: dict[str, Any] = {"role": role, "content": content or ""}
        if components:
            body["components"] = components
        if tools:
            body["tools"] = tools
        if error:
            body["error"] = error[:5_000]
        if backend:
            body["backend"] = backend
        try:
            res = await self._http().post(
                f"/api/chat-sessions/{session_id}/messages", json=body, headers=self._headers(user_id, workspace_id)
            )
            res.raise_for_status()
            self._warned = False
        except Exception as exc:  # noqa: BLE001 - history must never break the chat
            self._unavailable(exc)

    async def transcript(self, session_id: str, user_id: str, workspace_id: Optional[int]) -> list[dict[str, str]]:
        if not enabled():
            return []
        try:
            res = await self._http().get(
                f"/api/chat-sessions/{session_id}/transcript",
                params={"max_messages": TRANSCRIPT_MESSAGES, "max_chars": TRANSCRIPT_CHARS},
                headers=self._headers(user_id, workspace_id),
            )
            res.raise_for_status()
            messages = res.json().get("messages") or []
            return [m for m in messages if isinstance(m, dict) and m.get("content")]
        except Exception as exc:  # noqa: BLE001
            self._unavailable(exc)
            return []

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None


def with_transcript(prior: list[dict[str, str]], message: str) -> str:
    """The message to send an agent that lost the conversation: the stored exchange, then the new message."""
    if not prior:
        return message
    lines = [
        "[Earlier in this conversation, before the assistant was restarted. Continue from here: "
        "do not repeat these answers or greet again.]"
    ]
    for m in prior:
        role = "user" if m.get("role") == "user" else "assistant"
        lines.append(f"{role}: {m.get('content', '').strip()}")
    lines.append("[End of the earlier conversation.]")
    return "\n".join(lines) + "\n\n" + message


_store: Optional[ChatHistoryStore] = None


def get_history() -> ChatHistoryStore:
    global _store
    if _store is None:
        _store = ChatHistoryStore(os.getenv("FINANCE_API_URL", "http://localhost:8080"))
    return _store


async def shutdown_history() -> None:
    global _store
    if _store is not None:
        await _store.aclose()
        _store = None
