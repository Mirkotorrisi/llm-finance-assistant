"""Streaming chat endpoint served by whichever agent backend is configured."""

import asyncio
import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.agent import get_backend
from src.agent.confirm import ToolError, execute, pending_actions
from src.chat_history import get_history, with_transcript

logger = logging.getLogger(__name__)
router = APIRouter(tags=["agent-chat"])


# Session and user ids are client-generated UUIDs (or similar plain tokens). They end up in the agent's system
# prompt, in environment variables and in file names, so anything beyond a token is refused at the door.
IDENT = Field(pattern=r"^[A-Za-z0-9_.:-]{1,80}$")
MAX_MESSAGE_CHARS = 20_000


class StreamChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    session_id: str = IDENT
    user_id: str = Field(default="anonymous", pattern=r"^[A-Za-z0-9_.:-]{1,80}$")
    workspace_id: Optional[int] = Field(default=None, ge=1)  # the project being worked in; default project when omitted


class WarmRequest(BaseModel):
    session_id: str = IDENT
    user_id: str = Field(default="anonymous", pattern=r"^[A-Za-z0-9_.:-]{1,80}$")
    workspace_id: Optional[int] = Field(default=None, ge=1)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


class _TurnRecord:
    """What a turn produced, for the history: the final text, the components shown, the tools called, the error."""

    def __init__(self) -> None:
        self.text = ""
        self.components: list = []
        self.tools: list = []
        self.error: Optional[str] = None

    def see(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "status":
            self.tools.append(event.get("tool"))
        elif kind == "text_delta":
            self.text += event.get("text") or ""
        elif kind == "ui_component":
            self.components.append(event.get("component"))
        elif kind == "done":
            self.text = event.get("text") or self.text
            ui = event.get("ui") or {}
            self.components = list(ui.get("components") or self.components)
        elif kind == "error":
            self.error = event.get("message") or "error"

    @property
    def worth_keeping(self) -> bool:
        return bool(self.text.strip() or self.components or self.error)


@router.post("/chat/stream")
async def chat_stream(request: StreamChatRequest):
    """Server-sent events: status / text_delta / ui_component events, then done or error.

    Every turn is recorded in the chat history (API ``/api/chat-sessions``) so the conversation can be reopened;
    when the engine no longer holds the session (restart, expiry) the stored transcript is replayed to it first.
    """
    backend = get_backend()
    history = get_history()
    sid, uid, wid = request.session_id, request.user_id, request.workspace_id

    async def generate():
        message = request.message
        if not backend.has_session(sid, uid, wid):
            message = with_transcript(await history.transcript(sid, uid, wid), message)
        await history.record(sid, uid, wid, "user", request.message, backend=backend.name)
        record = _TurnRecord()
        try:
            async for event in backend.run_turn(sid, uid, message, wid):
                record.see(event)
                yield _sse(event)
        except asyncio.CancelledError:
            if record.worth_keeping:  # the client went away mid-answer: keep what was said so far
                asyncio.ensure_future(history.record(sid, uid, wid, "assistant", record.text, record.components, record.tools, "interrupted"))
            raise
        except Exception as e:
            logger.error("chat/stream failed: %s", e, exc_info=True)
            record.error = str(e)
            yield _sse({"type": "error", "message": str(e)})
        if record.worth_keeping:
            await history.record(sid, uid, wid, "assistant", record.text, record.components, record.tools, record.error, backend.name)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/session")
async def chat_session(request: WarmRequest):
    """Pre-start the agent for a session so the first message skips the startup cost."""
    backend = get_backend()
    asyncio.create_task(_warm(backend, request))
    return {"backend": backend.name, "warming": True}


async def _warm(backend, request: WarmRequest) -> None:
    try:
        await backend.warm(request.session_id, request.user_id, request.workspace_id)
    except Exception as e:  # noqa: BLE001 - warming is best effort
        logger.warning("warm-up failed: %s", e)


@router.get("/agent/info")
async def agent_info():
    return get_backend().info()


class ConfirmRequest(BaseModel):
    confirmation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,80}$")
    approve: bool = True


@router.post("/chat/confirm")
async def chat_confirm(request: ConfirmRequest):
    """Run (or drop) a destructive action the agent proposed. The action is executed with the identity the agent
    was acting for when it proposed it; an unknown or expired id is 404."""
    item = pending_actions.pop(request.confirmation_id)
    if item is None:
        raise HTTPException(status_code=404, detail="This action is no longer pending (already handled or expired)")
    if not request.approve:
        return {"status": "cancelled", "tool": item.tool}
    api_url = os.getenv("FINANCE_API_URL", "http://localhost:8080")
    try:
        result = await execute(item, api_url)
    except ToolError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "done", "tool": item.tool, "result": result}
