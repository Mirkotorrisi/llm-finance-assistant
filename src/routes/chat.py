"""Chat endpoints (HTTP + WebSocket) for the finance assistant."""

import base64
import json
import logging
import os
import tempfile
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from src.models import Action, FinancialParameters, UserInput
from src.models.chat import ChatRequest
from src.workflow import create_assistant_graph
from src.workflow.state import FinanceState

logger = logging.getLogger(__name__)

MAX_AUDIO_SIZE = 10 * 1024 * 1024
router = APIRouter(tags=["chat"])

@contextmanager
def temporary_audio_file(audio_data: str):
    """Context manager for creating and cleaning up temporary audio files."""
    temp_path = None
    try:
        try:
            audio_bytes = base64.b64decode(audio_data)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid base64 audio data: {str(e)}")

        if len(audio_bytes) > MAX_AUDIO_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"Audio file too large. Maximum size is {MAX_AUDIO_SIZE / 1024 / 1024}MB",
            )

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
            temp_audio.write(audio_bytes)
            temp_path = temp_audio.name

        yield temp_path

    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception as e:
                logger.warning(f"Failed to delete temporary file {temp_path}: {e}")

@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """WebSocket endpoint for real-time chat supporting async MCP workflow."""
    await websocket.accept()
    
    assistant_graph = create_assistant_graph()
    history = []

    try:
        while True:
            data = await websocket.receive_text()

            try:
                message_data: ChatRequest = json.loads(data)
                message = message_data.get("message", "")
                is_audio = message_data.get("is_audio", False)
                audio_data = message_data.get("audio_data")

                user_input_text = message
                
                if is_audio and audio_data:
                    with temporary_audio_file(audio_data) as temp_path:
                        user_input_text = temp_path
                        await _run_and_send(websocket, assistant_graph, user_input_text, is_audio, history)
                else:
                    await _run_and_send(websocket, assistant_graph, user_input_text, False, history)

            except json.JSONDecodeError:
                await websocket.send_json({"error": "Invalid JSON format"})
            except Exception as e:
                logger.error(f"Error processing websocket message: {e}", exc_info=True)
                await websocket.send_json({"error": f"Internal server error: {str(e)}"})

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")

@router.post("/chat")
async def chat_endpoint(request: ChatRequest):
    """POST endpoint for synchronous chat interaction with the agent graph."""
    assistant_graph = create_assistant_graph()
    
    state: FinanceState = {
        "input": UserInput(text=request.message, is_audio=request.is_audio),
        "transcription": None,
        "action": Action.UNKNOWN,
        "parameters": FinancialParameters(),
        "query_results": None,
        "ui_metadata": None,
        "response": None,
        "history": [], # In a real app, we'd pass history here
    }

    try:
        result = await assistant_graph.ainvoke(state)
        
        # Parse the JSON response we formed in generator_node
        try:
            parsed_response = json.loads(result["response"])
        except:
            parsed_response = {"text": result["response"], "ui": None}

        return {
            "response": parsed_response,
            "action": result["action"].value if result["action"] else "unknown",
            "parameters": result["parameters"].model_dump(exclude_none=True),
            "query_results": result["query_results"],
            "transcription": result.get("transcription")
        }
    except Exception as e:
        logger.error(f"Error in chat endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


async def _run_and_send(websocket, graph, text_or_path, is_audio, history):
    """Helper function to run the assistant graph and send the response back to the client."""
    
    state: FinanceState = {
        "input": UserInput(text=text_or_path, is_audio=is_audio),
        "transcription": None,
        "action": Action.UNKNOWN,
        "parameters": FinancialParameters(),
        "query_results": None,
        "ui_metadata": None,
        "response": None,
        "history": history,
    }

    # NOTE: We use AINVOKE because the nodes (NLU, Query, Generator) are asynchronous
    # and need to communicate with the MCP server via HTTP/SSE without blocking the server
    result = await graph.ainvoke(state)
    
    # Update the local websocket history
    history.extend(result["history"][-2:]) # Only take the last exchange

    await websocket.send_json({
        "response": result["response"],
        "action": result["action"].value if result["action"] else "unknown",
        "parameters": result["parameters"].model_dump(exclude_none=True),
        "query_results": result["query_results"],
        "transcription": result.get("transcription")
    })