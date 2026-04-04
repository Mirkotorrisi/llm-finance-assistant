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

## 🛠️ Configuration

Configure the agent in `.env`:
- `OPENAI_API_KEY`: Required for LLM reasoning.
- `FINANCE_API_URL`: Path to the core Finance API (default: `http://localhost:8081`).

---
Part of the [Finance Assistant Monorepo](../)
