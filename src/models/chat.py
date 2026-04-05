"""Request/response models for chat endpoints."""

from typing import List, Optional, Union
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


# --- UI-compatible models for POST /chat/plan ---

class MessagePart(BaseModel):
    """A content part within a chat message (for multi-modal messages)."""

    type: str
    text: Optional[str] = None


class Message(BaseModel):
    """A single message in a conversation thread."""

    role: str
    content: Optional[str] = None
    parts: Optional[List[MessagePart]] = None


class ChatPlanRequest(BaseModel):
    """Request model for the UI-compatible /chat/plan endpoint.

    Accepts a list of conversation messages and extracts the last user message
    to drive the assistant graph.
    """

    messages: List[Message]


class UIPlanAction(BaseModel):
    """Describes a backend service call needed to populate a UI component."""

    service: str
    method: str
    params: dict = {}


class UIPlanComponent(BaseModel):
    """A single UI component to be rendered by the frontend."""

    type: str
    order: int
    title: Optional[str] = None
    action: Optional[UIPlanAction] = None


class UIPlan(BaseModel):
    """UI rendering plan returned alongside the assistant text response."""

    text: str
    components: List[UIPlanComponent]


class ChatPlanResponse(BaseModel):
    """Response model for the UI-compatible /chat/plan endpoint."""

    text: str
    plan: Optional[UIPlan] = None
