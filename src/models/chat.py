"""Request/response models for chat endpoints."""

from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel


class ChatRequest(BaseModel):
    """Request model for chat endpoint."""

    message: str
    is_audio: bool = False
    audio_data: Union[str, None] = None


class ChatResponse(BaseModel):
    """Response model for chat endpoint."""

    response: str
    action: str
    parameters: dict
    query_results: Union[list, dict, float, bool, None] = None


class MessagePart(BaseModel):
    """A single content part within a UI message."""

    type: str
    text: Optional[str] = None


class Message(BaseModel):
    """A single message in the UI chat messages array."""

    role: str
    content: Optional[str] = None
    parts: Optional[List[MessagePart]] = None


class ChatPlanRequest(BaseModel):
    """Request model for the /chat/plan endpoint (UI-compatible)."""

    messages: List[Message]


class ChatPlanResponse(BaseModel):
    """Response model for the /chat/plan endpoint."""

    text: str
    plan: Optional[Dict[str, Any]] = None
