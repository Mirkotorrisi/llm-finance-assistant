"""FastAPI application for the finance assistant."""

import base64
import io
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from typing import Union, List, Dict, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.models import Action, FinancialParameters, UserInput
from src.workflow import create_assistant_graph, get_mcp_server
from src.workflow.state import FinanceState
from src.services import FileProcessor, FileValidationError, TransactionParser, RAGService

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

# Initialize global RAG service for transaction semantic search
rag_service = RAGService()
logger.info("RAG service initialized")


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


class MonthlyDataResponse(BaseModel):
    """Response model for monthly data in financial data endpoint."""
    month: str  # "Jan", "Feb", etc.
    netWorth: float
    expenses: float
    income: float
    net: float


class AccountBreakdownResponse(BaseModel):
    """Response model for account breakdown in financial data endpoint."""
    liquidity: float
    investments: float
    otherAssets: float


class FinancialDataResponse(BaseModel):
    """Response model for financial data endpoint."""
    year: int
    currentNetWorth: float
    netSavings: float
    monthlyData: List[MonthlyDataResponse]
    accountBreakdown: AccountBreakdownResponse


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
            "search_transactions": "/api/transactions/search (POST) - RAG semantic search",
            "balance": "/api/balance (GET)",
            "upload_statement": "/statements/upload (POST)",
            "financial_data": "/api/financial-data/{year} (GET)"
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


@app.get("/api/financial-data/{year}")
async def get_financial_data(year: int) -> FinancialDataResponse:
    """Get aggregated financial data for a specific year.
    
    Args:
        year: Year to fetch financial data for (YYYY)
        
    Returns:
        Aggregated financial data including monthly breakdown and account breakdown
        
    Raises:
        HTTPException: If there's an error fetching the data
    """
    try:
        mcp_server = get_mcp_server()
        
        # Fetch monthly snapshots for the year
        snapshots = mcp_server.get_monthly_snapshots(year)
        
        # Fetch all active accounts
        accounts = mcp_server.get_accounts()
        
        # Create a map of account_id to account type for quick lookup
        account_type_map = {acc['id']: acc['type'] for acc in accounts}
        
        # Initialize monthly data structure for all 12 months
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        monthly_data_map = {
            month: {
                "month": month_names[month - 1],
                "netWorth": 0.0,
                "expenses": 0.0,
                "income": 0.0,
                "net": 0.0,
                "has_data": False  # Track if month has any snapshots
            }
            for month in range(1, 13)
        }
        
        # Aggregate data by month
        for snapshot in snapshots:
            month = snapshot['month']
            if month in monthly_data_map:
                monthly_data_map[month]["netWorth"] += snapshot['ending_balance']
                monthly_data_map[month]["expenses"] += snapshot['total_expense']
                monthly_data_map[month]["income"] += snapshot['total_income']
                monthly_data_map[month]["has_data"] = True
        
        # Calculate net for each month after aggregation
        for month in range(1, 13):
            monthly_data_map[month]["net"] = (
                monthly_data_map[month]["income"] - monthly_data_map[month]["expenses"]
            )
        
        # Convert to list of MonthlyDataResponse
        monthly_data = [
            MonthlyDataResponse(
                month=monthly_data_map[month]["month"],
                netWorth=monthly_data_map[month]["netWorth"],
                expenses=monthly_data_map[month]["expenses"],
                income=monthly_data_map[month]["income"],
                net=monthly_data_map[month]["net"]
            )
            for month in range(1, 13)
        ]
        
        # Calculate current net worth (latest month with data)
        current_net_worth = 0.0
        for month in range(12, 0, -1):
            if monthly_data_map[month]["has_data"]:
                current_net_worth = monthly_data_map[month]["netWorth"]
                break
        
        # Calculate net savings (sum of all net values)
        net_savings = sum(md.net for md in monthly_data)
        
        # Calculate account breakdown
        # Get the latest snapshots for each account
        account_latest_balances = {}
        for snapshot in snapshots:
            account_id = snapshot['account_id']
            month = snapshot['month']
            # Keep track of the latest balance for each account
            if account_id not in account_latest_balances or account_latest_balances[account_id]['month'] < month:
                account_latest_balances[account_id] = {
                    'month': month,
                    'balance': snapshot['ending_balance']
                }
        
        # Categorize accounts by type
        liquidity = 0.0
        investments = 0.0
        other_assets = 0.0
        
        for account_id, data in account_latest_balances.items():
            account_type = account_type_map.get(account_id, "").lower()
            balance = data['balance']
            
            if account_type in ["checking", "savings", "cash"]:
                liquidity += balance
            elif account_type == "investment":
                investments += balance
            else:
                other_assets += balance
        
        account_breakdown = AccountBreakdownResponse(
            liquidity=liquidity,
            investments=investments,
            otherAssets=other_assets
        )
        
        return FinancialDataResponse(
            year=year,
            currentNetWorth=current_net_worth,
            netSavings=net_savings,
            monthlyData=monthly_data,
            accountBreakdown=account_breakdown
        )
        
    except Exception as e:
        logger.error(f"Error fetching financial data for year {year}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching financial data: {str(e)}")


@app.post("/statements/upload")
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
        
        # Add to RAG vector store for semantic search
        try:
            rag_service.add_transactions(added_transactions)
            logger.info(f"Added {len(added_transactions)} transactions to RAG vector store")
        except Exception as e:
            logger.warning(f"Failed to add transactions to RAG store (non-critical): {str(e)}")
        
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


class RAGQueryRequest(BaseModel):
    """Request model for RAG query endpoint."""
    query: str
    top_k: int = 5


class RAGQueryResponse(BaseModel):
    """Response model for RAG query endpoint."""
    query: str
    results: List[Dict[str, Any]]
    total_in_store: int


@app.post("/api/transactions/search")
async def search_transactions(request: RAGQueryRequest) -> RAGQueryResponse:
    """Search transactions using semantic similarity (RAG).
    
    Args:
        request: Query request with natural language query
        
    Returns:
        List of most relevant transactions with similarity scores
    """
    try:
        results = rag_service.query(request.query, top_k=request.top_k)
        
        return RAGQueryResponse(
            query=request.query,
            results=results,
            total_in_store=rag_service.size()
        )
        
    except Exception as e:
        logger.error(f"Error searching transactions: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error searching transactions: {str(e)}")


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
