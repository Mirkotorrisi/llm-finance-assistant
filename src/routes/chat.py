"""Chat endpoints (HTTP + WebSocket) for the finance assistant."""

import base64
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from typing import Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from src.models import Action, FinancialParameters, UserInput
from src.models.chat import (
    ChatPlanRequest,
    ChatPlanResponse,
    ChatRequest,
    Message,
    UIPlan,
    UIPlanComponent,
)
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


def _extract_last_user_text(messages: list[Message]) -> str:
    """Extract the text content from the last user message in a messages list.

    Checks ``content`` first; falls back to joining text parts from ``parts``.
    Returns an empty string if no user message is found.
    """
    for message in reversed(messages):
        if message.role == "user":
            if message.content:
                return message.content
            if message.parts:
                text_parts = [p.text for p in message.parts if p.type == "text" and p.text]
                if text_parts:
                    return " ".join(text_parts)
    return ""


def _build_ui_plan(text: str, ui_metadata: dict) -> UIPlan:
    """Map ``ui_metadata`` produced by the workflow into a :class:`UIPlan`.

    ``ui_metadata`` follows the shape set by ``ui_planner_node``:
    - ``componentKey``: preferred display component identifier (e.g. ``"summary-table"``)
    - ``type``: fallback type string (e.g. ``"table"``, ``"metric"``)
    - ``metadata.title`` or ``data.label``: human-readable title for the component
    """
    component_type = ui_metadata.get("componentKey") or ui_metadata.get("type", "unknown")

    title: Optional[str] = None
    metadata = ui_metadata.get("metadata", {})
    data = ui_metadata.get("data", {})
    if isinstance(metadata, dict) and metadata.get("title"):
        title = metadata["title"]
    elif isinstance(data, dict) and data.get("label"):
        title = data["label"]

    component = UIPlanComponent(type=component_type, order=0, title=title)
    return UIPlan(text=text, components=[component])


@router.post("/chat/plan", response_model=ChatPlanResponse)
async def chat_plan_endpoint(request: ChatPlanRequest):
    """POST endpoint compatible with the UI contract.

    Accepts ``{ messages }`` (a conversation thread) and returns
    ``{ text, plan? }`` where ``plan`` carries optional UI rendering metadata.

    The last user message is extracted from ``messages`` and fed into the
    existing assistant graph.  The natural-language response and any UI
    metadata produced by the graph are mapped into the response shape the
    frontend expects.
    """
    user_text = _extract_last_user_text(request.messages)
    if not user_text:
        raise HTTPException(status_code=400, detail="No user message text found in messages")

    assistant_graph = create_assistant_graph()

    state: FinanceState = {
        "input": UserInput(text=user_text, is_audio=False),
        "transcription": None,
        "action": Action.UNKNOWN,
        "parameters": FinancialParameters(),
        "query_results": None,
        "ui_metadata": None,
        "response": None,
        "history": [],
    }

    try:
        result = await assistant_graph.ainvoke(state)

        # generator_node stores a JSON string in result["response"]
        try:
            parsed_response = json.loads(result["response"])
            text = parsed_response.get("text") or result["response"]
            ui_data = parsed_response.get("ui")
        except (json.JSONDecodeError, TypeError):
            text = result["response"] or ""
            ui_data = None

        # Prefer the dedicated ui_metadata field if the parsed response had no "ui"
        if ui_data is None:
            ui_data = result.get("ui_metadata")

        plan = _build_ui_plan(text, ui_data) if ui_data else None

        return ChatPlanResponse(text=text, plan=plan)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in chat/plan endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))