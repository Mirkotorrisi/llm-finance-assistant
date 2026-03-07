# LLM Finance Assistant — Agentic Client

An intelligent, multimodal agentic client for personal finance management. This application acts as a **pure agentic client**: it handles natural language understanding (NLU), conversational history, and audio transcription, then delegates all business logic, data persistence, and financial computations to the remote [finance-assistant-api](https://github.com/Mirkotorrisi/finance-assistant-api) MCP server.

> **Architecture note:** This repository contains **no local database, no in-memory storage, and no business logic**. All financial data operations are handled by the remote MCP server configured via `MCP_SERVER_BASE_URL`.

## Features

- **Agentic Client Architecture**: Thin agent that routes user intent to the remote MCP server — no local data storage.
- **Multimodal Interaction**: Supports text-based input and audio transcription (via `SpeechRecognition`).
- **Intelligent NLU**: Powered by OpenAI's `gpt-4o-mini` to extract intent, categories, and timeframes from natural language.
- **Real-Time WebSocket Chat**: Primary interaction interface — persistent connection for fluid, stateful conversations.
- **State Management**: Built with `LangGraph` to manage conversational history and execution nodes.
- **MCP Protocol Integration**: Communicates with the remote finance-assistant-api over the Model Context Protocol (MCP).

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  llm-finance-assistant                  │
│                   (Agentic Client)                      │
│                                                         │
│  User ──► WebSocket ──► LangGraph Agent                 │
│                          │                              │
│                     ┌────▼────┐                         │
│                     │  ASR    │  (audio → text)         │
│                     └────┬────┘                         │
│                     ┌────▼────┐                         │
│                     │  NLU    │  (intent extraction)    │
│                     └────┬────┘                         │
│                     ┌────▼────┐                         │
│                     │ MCP     │  (remote tool calls)    │
│                     │ Client  │                         │
│                     └────┬────┘                         │
│                     ┌────▼────┐                         │
│                     │ Response│  (natural language)     │
│                     │ Gen     │                         │
│                     └─────────┘                         │
└───────────────────────┬─────────────────────────────────┘
                        │  MCP over HTTP/WS
                        ▼
┌─────────────────────────────────────────────────────────┐
│               finance-assistant-api                     │
│             (Remote MCP Server)                         │
│                                                         │
│  - Business logic & transaction management              │
│  - PostgreSQL persistence                               │
│  - Monthly snapshot model                               │
│  - SQL-first RAG pipeline                               │
│  - Bank statement ingestion                             │
└─────────────────────────────────────────────────────────┘
```

### Module Structure

```
llm-finance-assistant/
├── src/
│   ├── workflow/          # Agentic workflow (LangGraph)
│   │   ├── nodes.py       # Workflow nodes (ASR, NLU, MCP Client, Generator)
│   │   ├── graph.py       # Graph definition and compilation
│   │   └── state.py       # State type definitions
│   ├── models/            # Shared data models
│   │   └── domain.py      # Domain models (Action, Parameters, etc.)
│   ├── api/               # FastAPI application
│   │   └── app.py         # WebSocket handler and health endpoint
│   └── main_api.py        # API server entry point
├── docs/                  # Documentation
│   └── ARCHITECTURE.md    # Agent-client architecture details
└── README.md
```

## Prerequisites

- Python 3.10+
- OpenAI API Key
- A running instance of [finance-assistant-api](https://github.com/Mirkotorrisi/finance-assistant-api) (the remote MCP server)

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
   Create a `.env` file in the project root (use `.env.example` as a template):

   ```env
   OPENAI_API_KEY=your_openai_api_key_here

   # Remote MCP server (finance-assistant-api)
   MCP_SERVER_BASE_URL=http://localhost:9000
   MCP_API_KEY=your_mcp_api_key_here
   ```

   | Variable | Required | Description |
   |---|---|---|
   | `OPENAI_API_KEY` | Yes | OpenAI key for NLU and response generation |
   | `MCP_SERVER_BASE_URL` | Yes | Base URL of the remote finance-assistant-api MCP server |
   | `MCP_API_KEY` | Yes | API key for authenticating requests to the MCP server |

3. **Start the Finance-Assistant-API** (MCP server):

   Make sure the remote MCP server is running and reachable at the URL set in `MCP_SERVER_BASE_URL`. Refer to the [finance-assistant-api documentation](https://github.com/Mirkotorrisi/finance-assistant-api) for setup instructions.

4. **Start the Agent**:

   ```bash
   python -m src.main_api
   ```

   The agent API will be available at `http://localhost:8000`.

## Usage

### WebSocket Chat (Recommended)

The primary way to interact with the assistant is via the WebSocket endpoint. It maintains a persistent connection and conversational history across messages.

Connect to `ws://localhost:8000/ws/chat`.

**Send a text message:**
```json
{
  "message": "Show me my food expenses for this month",
  "is_audio": false
}
```

**Send an audio message (base64-encoded WAV):**
```json
{
  "message": "audio query",
  "is_audio": true,
  "audio_data": "<base64-encoded WAV file>"
}
```

**Receive a response:**
```json
{
  "response": "You have spent €125 on food this month...",
  "action": "list",
  "parameters": {"category": "food", "start_date": "2026-03-01"},
  "query_results": [...]
}
```

### Using WebSocket with Python

```python
import asyncio
import websockets
import json

async def chat():
    uri = "ws://localhost:8000/ws/chat"
    async with websockets.connect(uri) as websocket:
        # Send a message
        await websocket.send(json.dumps({
            "message": "What is my current balance?",
            "is_audio": False
        }))

        # Receive the response
        response = await websocket.recv()
        print(json.loads(response))

asyncio.run(chat())
```

### API Documentation

Once the agent is running, visit:
- **Interactive API docs**: http://localhost:8000/docs
- **Alternative docs**: http://localhost:8000/redoc

### Health Check

```
GET /health
```

## Workflow Nodes

1. **ASR Node**: Transcribes audio input to text (passes text input through unchanged).
2. **NLU Node**: Uses OpenAI `gpt-4o-mini` to extract intent, action, and parameters from the user message.
3. **MCP Client Node**: Calls the appropriate tool on the remote finance-assistant-api MCP server.
4. **Generator Node**: Produces a natural language response from the tool result.

## Development

### Running Tests

```bash
python -m pytest tests/ -v
```

## Contributing

The modular structure makes it easy to:
- Add new workflow nodes in `workflow/nodes.py`
- Extend the WebSocket handler in `api/app.py`
- Add new domain models in `models/domain.py`

## License

[Add your license here]
