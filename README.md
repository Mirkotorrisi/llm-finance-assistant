# Multimodal Personal Finance Assistant

A professional virtual assistant for managing personal finances. This application allows users to interact via text or voice, perform complex queries on their financial data, and manage transactions through both a CLI and a REST API with WebSocket support.

## Features

- **Multimodal Interaction**: Supports text-based input and audio transcription (via `SpeechRecognition`).
- **MCP Architecture**: Uses an in-memory Model Context Protocol (MCP) store to decouple transaction management from the conversational flow.
- **Intelligent NLU**: Powered by OpenAI's `gpt-4o-mini` to dynamically interpret user intent, categories, and timeframes.
- **Dynamic Transaction Management**:
  - Add expenses or income.
  - Delete transactions by ID.
  - Query historic spending with natural language (e.g., "last 3 days", "this week").
- **In-Memory Storage**: Lightweight session-scoped transaction store. All long-term persistence is handled by the external finance-assistant-api.
- **State Management**: Built with `LangGraph` to manage conversational history and execution nodes.
- **REST API & WebSocket**: FastAPI-based API for programmatic access and real-time chat via WebSocket.
- **Debug Mode**: Includes a specialized logging mode to inspect LLM reasonings and system prompts.

## Architecture

The agent is a **pure client**: all persistence and financial computation are delegated to the external `finance-assistant-api`. The local MCP store is a lightweight in-memory cache for the current session only.

```
llm-finance-assistant/
├── src/
│   ├── workflow/          # Agentic workflow (LangGraph)
│   │   ├── mcp_instance.py # In-memory MCP store (session-scoped)
│   │   ├── nodes.py       # Workflow nodes (ASR, NLU, Query, Generator)
│   │   ├── graph.py       # Graph definition and compilation
│   │   └── state.py       # State type definitions
│   ├── models/            # Shared data models
│   │   └── domain.py      # Domain models (Action, Parameters, etc.)
│   ├── api/               # FastAPI application
│   │   └── app.py         # API endpoints and WebSocket handler
│   ├── services/          # File processing, parsing, and RAG services
│   ├── main_cli.py        # CLI entry point
│   └── main_api.py        # API server entry point
├── finance_assistant.py   # Original monolithic file (deprecated)
└── README.md
```

## Prerequisites

- Python 3.10+
- OpenAI API Key

## Setup

1. **Install Dependencies**:

   Using pipenv:
   ```bash
   pipenv install
   ```

   Or using pip:
   ```bash
   pip install pydantic langgraph speechrecognition python-dotenv openai fastapi uvicorn[standard] websockets pypdf2 openpyxl pandas python-multipart
   ```

2. **Configure Environment**:
   Create a `.env` file in the project root (use `.env.example` as a template):

   ```env
   OPENAI_API_KEY=your_actual_key_here
   ```

## Usage

### Command Line Interface (CLI)

Run the assistant in interactive mode:

```bash
python -m src.main_cli
```

#### CLI Commands Examples

- **Queries**: "How much did I spend on food this week?"
- **Additions**: "I spent 15.50 on a bus ticket today"
- **Deletions**: "Delete transaction 4"
- **Balance**: "What is my current total balance?"

#### Audio Simulation

To simulate audio input (transcribing a `.wav` file):

```text
You: audio:path/to/voice_note.wav
```

#### Debug Mode

To inspect the LLM's reasoning prompts and MCP data output:

```bash
python -m src.main_cli --debug
```

### REST API and WebSocket

Start the FastAPI server:

```bash
python -m src.main_api
```

The API will be available at `http://localhost:8000`

#### API Documentation

Once the server is running, visit:
- **Interactive API docs**: http://localhost:8000/docs
- **Alternative docs**: http://localhost:8000/redoc

#### Available Endpoints

1. **Health Check**
   ```
   GET /health
   ```

2. **Get Transactions**
   ```
   GET /api/transactions?category=food&start_date=2026-01-01&end_date=2026-01-31
   ```

3. **Get Balance**
   ```
   GET /api/balance
   ```

4. **Upload Bank Statement**
   ```
   POST /statements/upload
   Content-Type: multipart/form-data
   
   Form data:
   - file: Bank statement file (PDF, XLS/XLSX, or CSV, max 10 MB)
   ```
   
   Supported file formats:
   - **CSV**: Expects columns for date, description, amount (optionally currency, category)
   - **Excel (XLS/XLSX)**: Expects columns for date, description, amount (optionally currency, category)
   - **PDF**: Uses LLM (GPT-4o-mini) to intelligently extract and parse transactions from unstructured text
   
   Features:
   - **LLM-based PDF parsing**: Intelligently extracts transactions from bank statement PDFs using OpenAI
   - **Automatic duplicate detection**: Skips already existing transactions
   - **Smart categorization**: Uses LLM to assign appropriate categories to transactions
   - **Currency support**: Extracts from data or defaults to EUR
   - **RAG integration**: Transactions are automatically added to vector store for semantic search
   
   Example response:
   ```json
   {
     "success": true,
     "message": "Successfully processed 5 transactions",
     "transactions_processed": 5,
     "transactions_added": 5,
     "transactions_skipped": 0,
     "transactions": [...]
   }
   ```

5. **Search Transactions (RAG)**
   ```
   POST /api/transactions/search
   Content-Type: application/json
   
   {
     "query": "food and grocery expenses last month",
     "top_k": 5
   }
   ```
   
   Uses semantic search (RAG) to find transactions matching natural language queries.

6. **Chat (REST)**
   ```
   POST /api/chat
   Content-Type: application/json
   
   {
     "message": "What is my balance?",
     "is_audio": false
   }
   ```

7. **Chat with Audio (REST)**
   ```
   POST /api/chat
   Content-Type: application/json
   
   {
     "message": "audio query",
     "is_audio": true,
     "audio_data": "<base64-encoded WAV file>"
   }
   ```

#### WebSocket Chat

Connect to the WebSocket endpoint at `ws://localhost:8000/ws/chat`

**Send message (text)**:
```json
{
  "message": "Show me my food expenses",
  "is_audio": false
}
```

**Receive response**:
```json
{
  "response": "You have spent $125 on food this week...",
  "action": "list",
  "parameters": {"category": "food", "start_date": "2026-01-05"},
  "query_results": [...]
}
```

### Using the API with Python

```python
import requests

# Get balance
response = requests.get("http://localhost:8000/api/balance")
print(response.json())

# Chat
response = requests.post(
    "http://localhost:8000/api/chat",
    json={"message": "How much did I spend on food this week?"}
)
print(response.json())

# Upload bank statement
with open("statement.csv", "rb") as f:
    files = {"file": ("statement.csv", f, "text/csv")}
    response = requests.post("http://localhost:8000/statements/upload", files=files)
    print(response.json())
    # Output: {"success": true, "transactions_added": 5, ...}
```

### Bank Statement Upload Format

The `/statements/upload` endpoint accepts bank statements in CSV, Excel (XLS/XLSX), or PDF format.

#### CSV/Excel Format

```csv
date,description,amount,currency,category
2026-01-11,Amazon payment,-49.90,EUR,Shopping
2026-01-12,Grocery shopping,-125.50,USD,Food
2026-01-13,Salary payment,2500.00,USD,Income
```

**Column mapping** (flexible field names):
- **Date**: `date`, `transaction date`, `booking date`, `value date`
- **Description**: `description`, `details`, `memo`, `narrative`, `transaction details`
- **Amount**: `amount`, `value`, `debit`, `credit`, `transaction amount`
- **Currency** (optional): `currency`, `ccy` - defaults to EUR if not provided
- **Category** (optional): `category`, `type`, `transaction type` - auto-categorized if not provided

#### PDF Format

PDF files are parsed to extract text. For best results, PDF statements should contain:
- Date in format: DD/MM/YYYY or MM/DD/YYYY or YYYY-MM-DD
- Clear transaction descriptions
- Amounts with currency symbols ($, €, £) or numeric values

## Development

### Running Tests

Run unit tests with pytest:

```bash
python -m pytest tests/ -v
```

### Module Structure

- **workflow/**: Contains the LangGraph-based agentic workflow with ASR, NLU, query execution, and response generation nodes
  - `mcp_instance.py`: In-memory MCP store (session-scoped, no DB dependency)
- **models/**: Shared Pydantic models for type safety and validation
- **api/**: FastAPI application with REST and WebSocket endpoints
- **services/**: File processing, transaction parsing, and RAG services
  - `file_processor.py`: Validates and extracts data from PDF, Excel, and CSV files
  - `transaction_parser.py`: Parses extracted data using LLM for intelligent categorization and PDF extraction
  - `vectorization.py`: RAG service with in-memory vector store for semantic transaction search

### Workflow Nodes

1. **ASR Node**: Converts audio to text (or passes through text input)
2. **NLU Node**: Uses LLM to extract intent and parameters
3. **Query Node**: Executes the action on the in-memory MCP store
4. **Generator Node**: Generates natural language response

### API Application

FastAPI application with:
- REST endpoints for direct access to transactions and balance
- POST endpoint for uploading and processing bank statements (PDF, Excel, CSV) with LLM-based parsing
- POST endpoint for semantic transaction search using RAG
- POST endpoint for processing chat requests
- WebSocket endpoint for real-time bidirectional communication
- Support for both text and audio inputs (base64-encoded)

### File Processing Services

The services module handles bank statement uploads:
- **FileProcessor**: Validates file formats/sizes and extracts raw data from PDF, Excel, and CSV files
- **TransactionParser**: Uses LLM (GPT-4o-mini) to intelligently parse PDFs and categorize transactions
- **RAGService**: In-memory vector store for semantic transaction search using embeddings

## Contributing

The modular structure makes it easy to:
- Add new workflow nodes in `workflow/nodes.py`
- Extend API endpoints in `api/app.py`
- Add new models in `models/domain.py`
- Add new file format support in `services/file_processor.py`
- Improve LLM-based parsing in `services/transaction_parser.py`
- Enhance RAG search capabilities in `services/vectorization.py`

## License

[Add your license here]
