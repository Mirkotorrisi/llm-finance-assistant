# Multimodal Personal Finance Assistant

A multimodal virtual assistant for managing personal finances. This application allows users to interact via text or voice to query and manage their financial data. All finance data operations are delegated to a remote MCP server ([finance-assistant-api](https://github.com/Mirkotorrisi/finance-assistant-api)) via HTTP — the agent contains no local database or business logic for transactions.

## Architecture: Agent as MCP Client

This repository implements the **agent layer** only. All finance actions (listing transactions, adding expenses, checking balances, etc.) are performed by calling the remote MCP server over HTTP.

```
User (text/audio)
       │
       ▼
┌─────────────────────────────────────┐
│  LLM Finance Assistant (this repo)  │
│  ┌──────────────────────────────┐   │
│  │  ASR → NLU → Query → Gen    │   │
│  │  (LangGraph workflow)        │   │
│  └──────────┬───────────────────┘   │
└─────────────┼───────────────────────┘
              │  HTTP (REST)
              ▼
┌─────────────────────────────┐
│  finance-assistant-api      │
│  (Remote MCP Server)        │
│  - /api/transactions        │
│  - /api/balance             │
│  - /api/accounts            │
│  - /api/financial-data      │
└─────────────────────────────┘
```

### Remote MCP Client (`src/workflow/mcp_instance.py`)

`RemoteMCPClient` is the HTTP client that translates workflow calls into REST requests:

| Method | Remote endpoint |
|---|---|
| `list_transactions(category, start_date, end_date)` | `GET /api/transactions` |
| `add_transaction(amount, category, description, ...)` | `POST /api/transactions` |
| `add_transactions_bulk(transactions)` | `POST /api/transactions/bulk` |
| `delete_transaction(id)` | `DELETE /api/transactions/{id}` |
| `get_balance()` | `GET /api/balance` |
| `get_existing_categories()` | `GET /api/transactions` (derived) |
| `get_accounts()` | `GET /api/accounts` |
| `get_financial_data(year)` | `GET /api/financial-data/{year}` |

## Features

- **Agent-as-Client Architecture**: Delegates all finance actions to the remote MCP server — no local DB required.
- **Multimodal Interaction**: Supports text-based input and audio transcription (via `SpeechRecognition`).
- **Intelligent NLU**: Powered by OpenAI's `gpt-4o-mini` to dynamically interpret user intent, categories, and timeframes.
- **Dynamic Transaction Management**:
  - Add expenses or income.
  - Delete transactions by ID.
  - Query historic spending with natural language (e.g., "last 3 days", "this week").
- **State Management**: Built with `LangGraph` to manage conversational history and execution nodes.
- **REST API & WebSocket**: FastAPI-based API for programmatic access and real-time chat via WebSocket.
- **Debug Mode**: Includes a specialized logging mode to inspect LLM reasonings and system prompts.

## Code Structure

```
llm-finance-assistant/
├── src/
│   ├── workflow/          # Agentic workflow (LangGraph)
│   │   ├── mcp_instance.py # RemoteMCPClient — HTTP client for the remote MCP server
│   │   ├── nodes.py       # Workflow nodes (ASR, NLU, Query, Generator)
│   │   ├── graph.py       # Graph definition and compilation
│   │   └── state.py       # State type definitions
│   ├── models/            # Shared data models
│   │   └── domain.py      # Domain models (Action, Parameters, etc.)
│   ├── api/               # FastAPI application
│   │   └── app.py         # API endpoints and WebSocket handler
│   ├── services/          # File processing and RAG services
│   └── main_api.py        # API server entry point
└── README.md
```

## Prerequisites

- Python 3.10+
- OpenAI API Key
- Running instance of [finance-assistant-api](https://github.com/Mirkotorrisi/finance-assistant-api) (the remote MCP server)

## Setup

1. **Install Dependencies**:

   Using pipenv:
   ```bash
   pipenv install
   ```

   Or using pip:
   ```bash
   pip install pydantic langgraph speechrecognition python-dotenv openai fastapi uvicorn[standard] websockets pypdf2 openpyxl pandas python-multipart requests
   ```

2. **Configure Environment**:
   Create a `.env` file in the project root (use `.env.example` as a template):

   ```env
   OPENAI_API_KEY=your_actual_key_here

   # URL of the remote finance-assistant-api MCP server
   MCP_SERVER_BASE_URL=http://localhost:8000
   ```

3. **Start the Remote MCP Server**:

   Clone and run [finance-assistant-api](https://github.com/Mirkotorrisi/finance-assistant-api) first, then point `MCP_SERVER_BASE_URL` at it.

## Usage

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
   - **Automatic duplicate detection**: Skips already existing transactions (checked via MCP client)
   - **Smart categorization**: Uses LLM to assign appropriate categories to transactions
   - **Currency support**: Extracts from data or defaults to EUR
   - **MCP client persistence**: Parsed and deduplicated transactions are bulk-added via the MCP client (`get_mcp_client()`), which delegates to the configured MCP server (local or remote)
   - **RAG integration**: Transactions are automatically added to the in-memory vector store for semantic search
   
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
   Transactions are embedded with OpenAI `text-embedding-3-small` and stored in an in-memory
   vector store (populated at upload time). No changes to this endpoint are required when
   switching to a remote MCP server—the RAG vector store remains local to the agent.
   
   Example response:
   ```json
   {
     "query": "food and grocery expenses last month",
     "results": [
       {
         "transaction": {"date": "2026-01-15", "description": "Walmart grocery", ...},
         "text": "Date: 2026-01-15, Description: Walmart grocery, ...",
         "similarity": 0.89
       }
     ],
     "total_in_store": 150
   }
   ```

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

The test suite targets the agent client architecture and covers:

| Test file | What it covers |
|---|---|
| `test_agent_workflow.py` | LangGraph graph creation, ASR / NLU / query / generator nodes |
| `test_websocket_chat.py` | WebSocket connection, text messages, error handling |
| `test_mcp_client.py` | `RemoteMCPClient` interface and HTTP calls to the remote MCP server |
| `test_file_processor.py` | File validation, CSV/PDF extraction |
| `test_transaction_parser.py` | Date/amount parsing, categorization, duplicate removal |
| `test_llm_pdf_parsing.py` | LLM-based PDF statement parsing |
| `test_rag_service.py` | In-memory RAG vector store for semantic search |
| `test_narrative_rag.py` | Narrative RAG document management and querying |
| `test_narrative_generator.py` | Narrative document generation from financial data |
| `test_rag_query_handler.py` | RAG-based conversational query handling |
| `test_integration.py` | End-to-end REST API including file upload |
| `test_rag_search.py` | Semantic search REST endpoint |

### Module Structure

- **workflow/**: LangGraph-based agentic workflow
  - `mcp_instance.py`: `RemoteMCPClient` — HTTP client for the remote MCP server
  - `nodes.py`: Workflow nodes (ASR, NLU, Query, Generator)
  - `graph.py`: Graph definition and compilation
  - `state.py`: State type definitions
- **models/**: Shared Pydantic models for type safety and validation
- **api/**: FastAPI application with REST and WebSocket endpoints
- **services/**: File processing, transaction parsing, and RAG services
  - `file_processor.py`: Validates and extracts data from PDF, Excel, and CSV files
  - `transaction_parser.py`: Parses extracted data using LLM for intelligent categorization and PDF extraction
  - `vectorization.py`: RAG service with in-memory vector store for semantic transaction search

## Key Components

### RemoteMCPClient

`RemoteMCPClient` (in `src/workflow/mcp_instance.py`) is the single point of contact with the remote finance-assistant-api server. It exposes the same method interface that the workflow nodes and API endpoints rely on, translating each call into an appropriate HTTP request.

### Workflow Nodes

1. **ASR Node**: Converts audio to text (or passes through text input)
2. **NLU Node**: Uses LLM to extract intent and parameters
3. **Query Node**: Executes the action via the remote MCP client
4. **Generator Node**: Generates natural language response

### API Application

FastAPI application with:
- REST endpoints that proxy to the remote MCP server for transactions and balance
- POST endpoint for uploading and processing bank statements (PDF, Excel, CSV) with LLM-based parsing
- POST endpoint for semantic transaction search using RAG
- POST endpoint for processing chat requests
- WebSocket endpoint for real-time bidirectional communication
- Support for both text and audio inputs (base64-encoded)

### File Processing Services

The services module handles bank statement uploads:
- **FileProcessor**: Validates file formats/sizes and extracts raw data from PDF, Excel, and CSV files
- **TransactionParser**: Uses LLM (GPT-4o-mini) to intelligently parse PDFs and categorize transactions
- **RAGService**: In-memory vector store for semantic transaction search using OpenAI embeddings

Transaction persistence in the upload flow uses `get_mcp_client()` from `src/workflow/__init__.py`,
which delegates all finance actions to the remote MCP server (`finance-assistant-api`) over HTTP.
Only the `get_mcp_client()` implementation needs to change if the remote server URL changes —
the upload endpoint and services remain unchanged.

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
