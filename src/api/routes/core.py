"""Core and system endpoints."""

from fastapi import APIRouter

router = APIRouter(tags=["core"])


@router.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "Finance Assistant API",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "websocket": "/ws/chat (WebSocket)",
            "upload_statement": "/statements/upload (POST)",
        },
    }


@router.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}
