"""Streaming chat endpoint served by whichever agent backend is configured."""

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.agent import get_backend

logger = logging.getLogger(__name__)
router = APIRouter(tags=["agent-chat"])


class StreamChatRequest(BaseModel):
    message: str
    session_id: str
    user_id: str = "anonymous"
    workspace_id: Optional[int] = None  # the project (workspace) being worked in; default project when omitted


class WarmRequest(BaseModel):
    session_id: str
    user_id: str = "anonymous"
    workspace_id: Optional[int] = None


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
