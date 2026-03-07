"""Chat endpoints (HTTP + WebSocket) for the finance assistant."""

import base64
import json
import logging
import os
import tempfile
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from src.api.models import ChatRequest, ChatResponse
from src.models import Action, FinancialParameters, UserInput
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
    """WebSocket endpoint for real-time chat."""
    await websocket.accept()
    assistant_graph = create_assistant_graph()
    history = []

    try:
        while True:
            data = await websocket.receive_text()

            try:
                message_data = json.loads(data)
                message = message_data.get("message", "")
                is_audio = message_data.get("is_audio", False)
                audio_data = message_data.get("audio_data")

                if is_audio and audio_data:
                    try:
                        with temporary_audio_file(audio_data) as temp_path:
                            state: FinanceState = {
                                "input": UserInput(text=temp_path, is_audio=is_audio),
                                "transcription": None,
                                "action": Action.UNKNOWN,
                                "parameters": FinancialParameters(),
                                "query_results": None,
                                "response": None,
                                "history": history,
                            }

                            result = assistant_graph.invoke(state)
                            history = result["history"]

                            await websocket.send_json(
                                {
                                    "response": result["response"],
                                    "action": result["action"].value,
                                    "parameters": result["parameters"].model_dump(exclude_none=True),
                                    "query_results": result["query_results"],
                                }
                            )
                    except HTTPException as he:
                        await websocket.send_json({"error": he.detail})
                        continue
                else:
                    state: FinanceState = {
                        "input": UserInput(text=message, is_audio=False),
                        "transcription": None,
                        "action": Action.UNKNOWN,
                        "parameters": FinancialParameters(),
                        "query_results": None,
                        "response": None,
                        "history": history,
                    }

                    result = assistant_graph.invoke(state)
                    history = result["history"]

                    await websocket.send_json(
                        {
                            "response": result["response"],
                            "action": result["action"].value,
                            "parameters": result["parameters"].model_dump(exclude_none=True),
                            "query_results": result["query_results"],
                        }
                    )

            except json.JSONDecodeError:
                await websocket.send_json({"error": "Invalid JSON format"})
            except Exception as e:
                await websocket.send_json({"error": f"Error processing request: {str(e)}"})

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
