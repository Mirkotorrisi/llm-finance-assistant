"""FastAPI application for the finance assistant."""

import base64
import io
import json
import os
import tempfile
from typing import Union
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.models import Action, FinancialParameters, UserInput
from src.workflow import create_assistant_graph
from src.workflow.state import FinanceState
from src.workflow.graph import get_mcp_server

app = FastAPI(
    title="Finance Assistant API",
    description="A multimodal finance assistant supporting text and audio interactions",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    """Request model for chat endpoint."""
    message: str
    is_audio: bool = False
    audio_data: Union[str, None] = None  # Base64-encoded audio data


class ChatResponse(BaseModel):
    """Response model for chat endpoint."""
    response: str
    action: str
    parameters: dict
    query_results: Union[list, dict, float, bool, None] = None


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "Finance Assistant API",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "chat": "/api/chat (POST)",
            "websocket": "/ws/chat (WebSocket)",
            "transactions": "/api/transactions (GET)",
            "balance": "/api/balance (GET)"
        }
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/api/transactions")
async def get_transactions(
    category: Union[str, None] = None,
    start_date: Union[str, None] = None,
    end_date: Union[str, None] = None
):
    """Get transactions with optional filters."""
    mcp_server = get_mcp_server()
    transactions = mcp_server.list_transactions(category, start_date, end_date)
    return {"transactions": transactions}


@app.get("/api/balance")
async def get_balance():
    """Get current balance."""
    mcp_server = get_mcp_server()
    balance = mcp_server.get_balance()
    return {"balance": balance}


@app.post("/api/chat")
async def chat(request: ChatRequest) -> ChatResponse:
    """Process a chat request (text or audio).
    
    For audio requests, provide base64-encoded audio data in the audio_data field.
    """
    assistant_graph = create_assistant_graph()
    
    # Handle audio input
    text_to_process = request.message
    is_audio = request.is_audio
    
    if is_audio and request.audio_data:
        # Save base64-encoded audio to a temporary file
        try:
            audio_bytes = base64.b64decode(request.audio_data)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
                temp_audio.write(audio_bytes)
                text_to_process = temp_audio.name
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid audio data: {str(e)}")
    
    state: FinanceState = {
        "input": UserInput(text=text_to_process, is_audio=is_audio),
        "transcription": None,
        "action": Action.UNKNOWN,
        "parameters": FinancialParameters(),
        "query_results": None,
        "response": None,
        "history": []
    }
    
    try:
        result = assistant_graph.invoke(state)
        
        # Clean up temporary audio file if created
        if is_audio and request.audio_data and os.path.exists(text_to_process):
            os.unlink(text_to_process)
        
        return ChatResponse(
            response=result["response"],
            action=result["action"].value,
            parameters=result["parameters"].model_dump(exclude_none=True),
            query_results=result["query_results"]
        )
    except Exception as e:
        # Clean up temporary audio file if created
        if is_audio and request.audio_data and os.path.exists(text_to_process):
            os.unlink(text_to_process)
        raise HTTPException(status_code=500, detail=f"Error processing request: {str(e)}")


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """WebSocket endpoint for real-time chat.
    
    Accepts JSON messages with the following format:
    {
        "message": "text message or path to audio file",
        "is_audio": false,
        "audio_data": "base64-encoded audio data (optional)"
    }
    
    Responds with JSON messages containing:
    {
        "response": "assistant response",
        "action": "action taken",
        "parameters": {...},
        "query_results": {...}
    }
    """
    await websocket.accept()
    assistant_graph = create_assistant_graph()
    history = []
    
    try:
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            
            try:
                message_data = json.loads(data)
                message = message_data.get("message", "")
                is_audio = message_data.get("is_audio", False)
                audio_data = message_data.get("audio_data")
                
                # Handle audio input
                text_to_process = message
                
                if is_audio and audio_data:
                    # Save base64-encoded audio to a temporary file
                    try:
                        audio_bytes = base64.b64decode(audio_data)
                        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
                            temp_audio.write(audio_bytes)
                            text_to_process = temp_audio.name
                    except Exception as e:
                        await websocket.send_json({
                            "error": f"Invalid audio data: {str(e)}"
                        })
                        continue
                
                # Process the request
                state: FinanceState = {
                    "input": UserInput(text=text_to_process, is_audio=is_audio),
                    "transcription": None,
                    "action": Action.UNKNOWN,
                    "parameters": FinancialParameters(),
                    "query_results": None,
                    "response": None,
                    "history": history
                }
                
                result = assistant_graph.invoke(state)
                history = result["history"]
                
                # Clean up temporary audio file if created
                if is_audio and audio_data and os.path.exists(text_to_process):
                    os.unlink(text_to_process)
                
                # Send response
                await websocket.send_json({
                    "response": result["response"],
                    "action": result["action"].value,
                    "parameters": result["parameters"].model_dump(exclude_none=True),
                    "query_results": result["query_results"]
                })
                
            except json.JSONDecodeError:
                await websocket.send_json({
                    "error": "Invalid JSON format"
                })
            except Exception as e:
                await websocket.send_json({
                    "error": f"Error processing request: {str(e)}"
                })
                
    except WebSocketDisconnect:
        print("WebSocket client disconnected")
