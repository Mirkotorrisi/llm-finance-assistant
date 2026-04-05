# AI Finance Assistant Agent 🏦🤖

The **AI Finance Assistant Agent** is the brain of the ecosystem, powered by **LangGraph** and **FastAPI**. It interprets natural language queries, interacts with the Finance API via MCP tools, and plans dynamic UI responses.

## 🚀 Quick Start

This service is typically run as part of the [Finance Assistant Monorepo](../README.md) using Docker Compose.

### Local Development (with uv)

We use **uv** for high-performance dependency management.

1. Ensure you have [uv](https://github.com/astral-sh/uv) installed.
2. Install dependencies:
   ```bash
   uv sync
   ```
3. Run the development server:
   ```bash
   uv run uvicorn app:app --reload --port 8000
   ```

## 🧠 Brain & Workflow

The agent uses a LangGraph workflow to:
1.  **Interpret Message**: Extract user intent.
2.  **Tool Execution**: Call the Finance API via MCP to fetch balances, transactions, and more.
3.  **UI Planning**: Decide which component (`summary-table`, `metric-card`, `chart`) is best for visualization.
4.  **Generation**: Stream a final text response with embedded UI metadata.

## 📡 API Endpoints

### `POST /chat` (existing)
Synchronous chat endpoint for direct clients.

**Request**
```json
{ "message": "string", "is_audio": false }
```
**Response**
```json
{
  "response": { "text": "string", "ui": {} },
  "action": "string",
  "parameters": {},
  "query_results": null,
  "transcription": null
}
```

---

### `POST /chat/plan` (UI-compatible)
UI-compatible endpoint consumed directly by the frontend.  It accepts a
conversation thread and returns only the assistant text plus an optional
UI rendering plan.

**Request**
```json
{
  "messages": [
    { "role": "user", "content": "What is my balance?" }
  ]
}
```
Each message can alternatively use `parts` instead of `content`:
```json
{
  "messages": [
    {
      "role": "user",
      "parts": [{ "type": "text", "text": "Show my transactions" }]
    }
  ]
}
```

**Response**
```json
{
  "text": "Your current balance is €1,234.56.",
  "plan": {
    "text": "Your current balance is €1,234.56.",
    "components": [
      { "type": "metric-card", "order": 0, "title": "Total Balance" }
    ]
  }
}
```
`plan` is `null` when no UI component was selected for the query.
`plan.text` mirrors the top-level `text` field and is included for
convenience so that the UI can reference the text within the plan object
without looking it up from the parent.

---

## 🛠️ Configuration

Configure the agent in `.env`:
- `OPENAI_API_KEY`: Required for LLM reasoning.
- `FINANCE_API_URL`: Path to the core Finance API (default: `http://localhost:8081`).

---
Part of the [Finance Assistant Monorepo](../)
