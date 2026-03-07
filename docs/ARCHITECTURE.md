# Agent-Client Architecture

## Overview

`llm-finance-assistant` is a **pure agentic client**. It contains no local database, no in-memory data storage, and no business logic for financial computations. All of that responsibility belongs to the remote [finance-assistant-api](https://github.com/Mirkotorrisi/finance-assistant-api) MCP server.

## Design Principle

> **This service = conversation + intelligence. The MCP server = data + persistence.**

The agent's only responsibilities are:

1. Accept user messages (text or audio) over WebSocket.
2. Transcribe audio to text (ASR).
3. Extract intent, action, and parameters from natural language (NLU via OpenAI).
4. Forward tool calls to the remote MCP server and receive results.
5. Generate a natural language response to return to the user.

## Data Flow

```
User
 │
 │  WebSocket (ws://agent/ws/chat)
 ▼
┌──────────────────────────────────────────┐
│           LangGraph Agent                │
│                                          │
│  1. ASR Node         (audio → text)      │
│  2. NLU Node         (text → intent)     │
│  3. MCP Client Node  (intent → API call) │
│  4. Generator Node   (result → response) │
└──────────────────────┬───────────────────┘
                       │
                       │  HTTP / MCP Protocol
                       │  Authorization: Bearer <MCP_API_KEY>
                       ▼
          ┌────────────────────────┐
          │   finance-assistant-api │
          │   (Remote MCP Server)   │
          │                        │
          │  - Transaction CRUD    │
          │  - Balance queries     │
          │  - Monthly snapshots   │
          │  - SQL-first RAG       │
          │  - Bank statement      │
          │    ingestion           │
          │  - PostgreSQL storage  │
          └────────────────────────┘
```

## Environment Configuration

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | OpenAI key used by ASR (Whisper), NLU (gpt-4o-mini), and response generation |
| `MCP_SERVER_BASE_URL` | Yes | Base URL of the running `finance-assistant-api` instance (e.g. `http://localhost:9000`) |
| `MCP_API_KEY` | Yes | Bearer token for authenticating MCP tool calls |

## Workflow Nodes

### 1. ASR Node (`workflow/nodes.py`)

- Input: raw message and optional base64-encoded WAV audio.
- If `is_audio=true`, decodes the WAV and calls OpenAI Whisper to transcribe.
- Output: plain text message forwarded to the NLU node.

### 2. NLU Node (`workflow/nodes.py`)

- Input: text message + conversation history.
- Calls `gpt-4o-mini` with a structured prompt to extract:
  - `action`: one of `list`, `add`, `delete`, `balance`, `unknown`
  - `parameters`: category, amount, date range, transaction ID, description, currency
- Output: populated `FinancialParameters` object and resolved `Action`.

### 3. MCP Client Node (`workflow/nodes.py`)

- Input: `Action` + `FinancialParameters`.
- Selects the appropriate MCP tool and sends an authenticated HTTP request to `MCP_SERVER_BASE_URL`.
- Output: raw tool result (transaction list, balance, confirmation, etc.).

### 4. Generator Node (`workflow/nodes.py`)

- Input: tool result + conversation history.
- Calls `gpt-4o-mini` to produce a friendly, conversational answer.
- Output: final text response sent back over WebSocket.

## Remote MCP Server Capabilities

The `finance-assistant-api` server exposes (via MCP):

| Tool | Description |
|---|---|
| `list_transactions` | Query transactions by category, date range, or description |
| `add_transaction` | Record a new income or expense entry |
| `delete_transaction` | Remove a transaction by ID |
| `get_balance` | Retrieve current account balance(s) |
| `get_monthly_snapshot` | Fetch monthly summary (income, expenses, net) |
| `upload_statement` | Ingest a bank statement (CSV, Excel, PDF) |

Refer to the [finance-assistant-api documentation](https://github.com/Mirkotorrisi/finance-assistant-api) for full MCP tool schemas.

## What Is NOT in This Repository

The following concerns are **deliberately excluded** from this codebase and live entirely in `finance-assistant-api`:

- PostgreSQL / database configuration
- SQLAlchemy ORM models
- Monthly account snapshot model
- SQL-first RAG pipeline (AggregationService, NarrativeGenerator, NarrativeRAGService)
- Bank statement file processing (PDF/Excel/CSV parsers)
- Transaction business logic
- Data seeding scripts
