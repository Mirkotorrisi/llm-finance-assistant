# Multimodal Personal Finance Assistant

A professional, self-contained virtual assistant for managing personal finances. This application allows users to interact via text or voice, perform complex queries on their financial data, and manage transactions through both a CLI and a REST API with WebSocket support.

## Features

- **Multimodal Interaction**: Supports text-based input and audio transcription (via `SpeechRecognition`).
- **MCP Architecture**: Uses a Model Context Protocol (MCP) simulation to decouple business logic from the conversational flow.
- **Intelligent NLU**: Powered by OpenAI's `gpt-4o-mini` to dynamically interpret user intent, categories, and timeframes.
- **Dynamic Transaction Management**:
  - Add expenses or income.
  - Delete transactions by ID.
  - Query historic spending with natural language (e.g., "last 3 days", "this week").
- **State Management**: Built with `LangGraph` to manage conversational history and execution nodes.
- **REST API & WebSocket**: FastAPI-based API for programmatic access and real-time chat via WebSocket.
- **Debug Mode**: Includes a specialized logging mode to inspect LLM reasonings and system prompts.

## Architecture

The application is organized into a modular structure:

```
llm-finance-assistant/
├── src/
│   ├── business_logic/    # Business logic layer
│   │   ├── mcp.py         # FinanceMCP class for transaction management
│   │   └── data.py        # Initial data setup
│   ├── workflow/          # Agentic workflow (LangGraph)
│   │   ├── nodes.py       # Workflow nodes (ASR, NLU, Query, Generator)
│   │   ├── graph.py       # Graph definition and compilation
│   │   └── state.py       # State type definitions
│   ├── models/            # Shared data models
│   │   └── domain.py      # Domain models (Action, Parameters, etc.)
│   ├── api/               # FastAPI application
│   │   └── app.py         # API endpoints and WebSocket handler
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
   pip install pydantic langgraph speechrecognition python-dotenv openai fastapi uvicorn[standard] websockets
   ```

2. **Configure Environment**:
   Create a `.env` file in the project root:

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

4. **Chat (REST)**
   ```
   POST /api/chat
   Content-Type: application/json
   
   {
     "message": "What is my balance?",
     "is_audio": false
   }
   ```

5. **Chat with Audio (REST)**
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

**Send message (audio)**:
```json
{
  "message": "audio query",
  "is_audio": true,
  "audio_data": "<base64-encoded WAV file>"
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
```

### Using WebSocket with Python

```python
import asyncio
import websockets
import json

async def chat():
    uri = "ws://localhost:8000/ws/chat"
    async with websockets.connect(uri) as websocket:
        # Send message
        await websocket.send(json.dumps({
            "message": "What is my balance?",
            "is_audio": False
        }))
        
        # Receive response
        response = await websocket.recv()
        print(json.loads(response))

asyncio.run(chat())
```

## Development

### Running Tests

The application structure supports easy testing. You can test individual components:

```python
# Test business logic
from src.business_logic import FinanceMCP, get_initial_data

mcp = FinanceMCP(get_initial_data())
balance = mcp.get_balance()
print(f"Balance: {balance}")

# Test models
from src.models import Action, FinancialParameters

params = FinancialParameters(category="food", amount=-50.0)
print(params.model_dump())
```

### Module Structure

- **business_logic/**: Contains the core financial transaction logic (FinanceMCP)
- **workflow/**: Contains the LangGraph-based agentic workflow with ASR, NLU, query execution, and response generation nodes
- **models/**: Shared Pydantic models for type safety and validation
- **api/**: FastAPI application with REST and WebSocket endpoints

## Key Components

### FinanceMCP (Business Logic)

The `FinanceMCP` class provides methods for:
- `list_transactions()`: Query transactions with filters
- `add_transaction()`: Add new transaction
- `delete_transaction()`: Remove transaction by ID
- `get_balance()`: Get current balance

### Workflow Nodes

1. **ASR Node**: Converts audio to text (or passes through text input)
2. **NLU Node**: Uses LLM to extract intent and parameters
3. **Query Node**: Executes the action on the MCP server
4. **Generator Node**: Generates natural language response

### API Application

FastAPI application with:
- REST endpoints for direct access to transactions and balance
- POST endpoint for processing chat requests
- WebSocket endpoint for real-time bidirectional communication
- Support for both text and audio inputs (base64-encoded)

## Contributing

The modular structure makes it easy to:
- Add new transaction types in `business_logic/mcp.py`
- Add new workflow nodes in `workflow/nodes.py`
- Extend API endpoints in `api/app.py`
- Add new models in `models/domain.py`

## License

[Add your license here]
