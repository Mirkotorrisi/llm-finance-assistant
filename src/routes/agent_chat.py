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


@router.post("/chat/stream")
async def chat_stream(request: StreamChatRequest):
    """Server-sent events: status / text_delta / ui_component events, then done or error."""
    backend = get_backend()

    async def generate():
        try:
            async for event in backend.run_turn(request.session_id, request.user_id, request.message, request.workspace_id):
                yield _sse(event)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("chat/stream failed: %s", e, exc_info=True)
            yield _sse({"type": "error", "message": str(e)})

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
