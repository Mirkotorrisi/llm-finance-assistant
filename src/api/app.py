"""FastAPI application for the finance assistant."""

import base64
import io
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from typing import Union
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.models import Action, FinancialParameters, UserInput, LLMNLUResponse
from src.workflow import create_assistant_graph, get_mcp_server
from src.workflow.state import FinanceState
from src.services import FileProcessor, FileValidationError, TransactionParser, VectorizationService

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Maximum audio file size (10 MB)
MAX_AUDIO_SIZE = 10 * 1024 * 1024


@contextmanager
def temporary_audio_file(audio_data: str):
    """Context manager for creating and cleaning up temporary audio files.
    
    Args:
        audio_data: Base64-encoded audio data
        
    Yields:
        Path to temporary audio file
        
    Raises:
        HTTPException: If audio data is invalid or too large
    """
    temp_path = None
    try:
        # Decode and validate audio data
        try:
            audio_bytes = base64.b64decode(audio_data)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid base64 audio data: {str(e)}")
        
        # Check size limit
        if len(audio_bytes) > MAX_AUDIO_SIZE:
            raise HTTPException(
                status_code=400, 
                detail=f"Audio file too large. Maximum size is {MAX_AUDIO_SIZE / 1024 / 1024}MB"
            )
        
        # Create temporary file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
            temp_audio.write(audio_bytes)
            temp_path = temp_audio.name
        
        yield temp_path
        
    finally:
        # Always clean up the temporary file
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception as e:
                print(f"Warning: Failed to delete temporary file {temp_path}: {e}")


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


def custom_openapi():
    """Custom OpenAPI schema that includes all domain models."""
    if app.openapi_schema:
        return app.openapi_schema
    
    # Import the default openapi utility
    from fastapi.openapi.utils import get_openapi
    
    # Generate the base OpenAPI schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    
    # Ensure domain models are included in the schema
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}
    if "schemas" not in openapi_schema["components"]:
        openapi_schema["components"]["schemas"] = {}
    
    # Add domain models explicitly
    from pydantic import TypeAdapter
    
    # Add Action enum
    action_adapter = TypeAdapter(Action)
    openapi_schema["components"]["schemas"]["Action"] = action_adapter.json_schema()
    
    # Add FinancialParameters
    openapi_schema["components"]["schemas"]["FinancialParameters"] = FinancialParameters.model_json_schema()
    
    # Add UserInput
    openapi_schema["components"]["schemas"]["UserInput"] = UserInput.model_json_schema()
    
    # Add LLMNLUResponse
    openapi_schema["components"]["schemas"]["LLMNLUResponse"] = LLMNLUResponse.model_json_schema()
    
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


class RootResponse(BaseModel):
    """Response model for root endpoint."""
    message: str
    version: str
    endpoints: dict


class HealthResponse(BaseModel):
    """Response model for health check endpoint."""
    status: str


class TransactionsResponse(BaseModel):
    """Response model for transactions endpoint."""
    transactions: list


class BalanceResponse(BaseModel):
    """Response model for balance endpoint."""
    balance: float


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


class UploadStatementResponse(BaseModel):
    """Response model for statement upload endpoint."""
    success: bool
    message: str
    transactions_processed: int
    transactions_added: int
    transactions_skipped: int
    transactions: list


@app.get("/", response_model=RootResponse, tags=["health"])
async def root() -> RootResponse:
    """Root endpoint."""
    return RootResponse(
        message="Finance Assistant API",
        version="1.0.0",
        endpoints={
            "health": "/health",
            "chat": "/api/chat (POST)",
            "websocket": "/ws/chat (WebSocket)",
            "transactions": "/api/transactions (GET)",
            "balance": "/api/balance (GET)",
            "upload_statement": "/statements/upload (POST)"
        }
    )


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(status="healthy")


@app.get("/api/transactions", response_model=TransactionsResponse, tags=["transactions"])
async def get_transactions(
    category: Union[str, None] = None,
    start_date: Union[str, None] = None,
    end_date: Union[str, None] = None
) -> TransactionsResponse:
    """Get transactions with optional filters."""
    mcp_server = get_mcp_server()
    transactions = mcp_server.list_transactions(category, start_date, end_date)
    return TransactionsResponse(transactions=transactions)


@app.get("/api/balance", response_model=BalanceResponse, tags=["transactions"])
async def get_balance() -> BalanceResponse:
    """Get current balance."""
    mcp_server = get_mcp_server()
    balance = mcp_server.get_balance()
    return BalanceResponse(balance=balance)


@app.post("/statements/upload", response_model=UploadStatementResponse, tags=["statements"])
async def upload_statement(file: UploadFile = File(...)) -> UploadStatementResponse:
    """Upload and process a bank statement file.
    
    Accepts PDF, XLS/XLSX, or CSV files up to 10 MB.
    Extracts transactions and adds them to the system.
    
    Args:
        file: Uploaded file
        
    Returns:
        Upload result with statistics
        
    Raises:
        HTTPException: If file validation or processing fails
    """
    try:
        # Read file content
        file_content = await file.read()
        file_size = len(file_content)
        
        logger.info(f"Received file upload: {file.filename}, size: {file_size} bytes")
        
        # Validate and process file
        try:
            extracted_data = FileProcessor.process_file(
                file.filename,
                io.BytesIO(file_content),
                file_size
            )
        except FileValidationError as e:
            logger.error(f"File validation error: {str(e)}")
            raise HTTPException(status_code=400, detail=str(e))
        
        # Get existing categories from MCP server
        mcp_server = get_mcp_server()
        existing_categories = mcp_server.get_existing_categories()
        
        logger.info(f"Found {len(existing_categories)} existing categories: {existing_categories}")
        
        # Parse transactions with LLM-based categorization
        transactions = TransactionParser.parse_transactions(
            extracted_data,
            existing_categories=existing_categories,
            use_llm_categorization=True
        )
        
        if not transactions:
            return UploadStatementResponse(
                success=True,
                message="No valid transactions found in the file",
                transactions_processed=len(extracted_data),
                transactions_added=0,
                transactions_skipped=0,
                transactions=[]
            )
        
        # Remove duplicates
        existing_transactions = mcp_server.list_transactions()
        unique_transactions = TransactionParser.remove_duplicates(transactions, existing_transactions)
        
        # Add transactions to the system
        added_transactions = mcp_server.add_transactions_bulk(unique_transactions)
        
        # Process for vectorization (optional - don't fail if it errors)
        # TODO: Consider moving this to an async background task for better performance
        try:
            vectorization_service = VectorizationService()
            vectorization_service.process_transactions(unique_transactions)
            logger.info("Transactions vectorized successfully")
        except Exception as e:
            logger.warning(f"Vectorization failed (non-critical): {str(e)}")
        
        logger.info(
            f"Statement upload completed: {len(added_transactions)} transactions added, "
            f"{len(transactions) - len(unique_transactions)} duplicates skipped"
        )
        
        return UploadStatementResponse(
            success=True,
            message=f"Successfully processed {len(added_transactions)} transactions",
            transactions_processed=len(extracted_data),
            transactions_added=len(added_transactions),
            transactions_skipped=len(transactions) - len(unique_transactions),
            transactions=added_transactions
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error processing file: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing file: {str(e)}")


@app.post("/api/chat", response_model=ChatResponse, tags=["chat"])
async def chat(request: ChatRequest) -> ChatResponse:
    """Process a chat request (text or audio).
    
    For audio requests, provide base64-encoded audio data in the audio_data field.
    """
    assistant_graph = create_assistant_graph()
    
    # Handle audio input
    text_to_process = request.message
    is_audio = request.is_audio
    
    if is_audio and request.audio_data:
        # Use context manager to handle temporary audio file
        with temporary_audio_file(request.audio_data) as temp_path:
            state: FinanceState = {
                "input": UserInput(text=temp_path, is_audio=is_audio),
                "transcription": None,
                "action": Action.UNKNOWN,
                "parameters": FinancialParameters(),
                "query_results": None,
                "response": None,
                "history": []
            }
            
            result = assistant_graph.invoke(state)
            
            return ChatResponse(
                response=result["response"],
                action=result["action"].value,
                parameters=result["parameters"].model_dump(exclude_none=True),
                query_results=result["query_results"]
            )
    else:
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
            
            return ChatResponse(
                response=result["response"],
                action=result["action"].value,
                parameters=result["parameters"].model_dump(exclude_none=True),
                query_results=result["query_results"]
            )
        except Exception as e:
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
                
                # Handle audio input with validation
                if is_audio and audio_data:
                    try:
                        with temporary_audio_file(audio_data) as temp_path:
                            # Process the request
                            state: FinanceState = {
                                "input": UserInput(text=temp_path, is_audio=is_audio),
                                "transcription": None,
                                "action": Action.UNKNOWN,
                                "parameters": FinancialParameters(),
                                "query_results": None,
                                "response": None,
                                "history": history
                            }
                            
                            result = assistant_graph.invoke(state)
                            history = result["history"]
                            
                            # Send response
                            await websocket.send_json({
                                "response": result["response"],
                                "action": result["action"].value,
                                "parameters": result["parameters"].model_dump(exclude_none=True),
                                "query_results": result["query_results"]
                            })
                    except HTTPException as he:
                        await websocket.send_json({
                            "error": he.detail
                        })
                        continue
                else:
                    # Process text input
                    state: FinanceState = {
                        "input": UserInput(text=message, is_audio=False),
                        "transcription": None,
                        "action": Action.UNKNOWN,
                        "parameters": FinancialParameters(),
                        "query_results": None,
                        "response": None,
                        "history": history
                    }
                    
                    result = assistant_graph.invoke(state)
                    history = result["history"]
                    
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


@app.get("/api/openapi.json", include_in_schema=False)
async def get_openapi_schema():
    """Serve the OpenAPI schema JSON (required by Swagger UI)."""
    return JSONResponse(content=app.openapi())


@app.get("/api/swagger", include_in_schema=False)
async def get_swagger_ui():
    """Serve the Swagger UI documentation at /api/swagger."""
    return get_swagger_ui_html(
        openapi_url="/api/openapi.json",
        title=f"{app.title} - Swagger UI",
        swagger_favicon_url="https://fastapi.tiangolo.com/img/favicon.png"
    )
