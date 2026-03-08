"""Request/response models for chat endpoints."""

from typing import Union
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
