# Finance Assistant Agent Gateway 🏦🤖

FastAPI service between the web UI and the Finance API. It answers the chat with a switchable **agent backend** and runs the statement/Amazon/search jobs on a switchable **LLM provider**.

| Backend / provider | Engine | Needs |
|---|---|---|
| `claude` (default) | headless `claude` on your Claude Code login | the `claude` CLI, logged in |
| `openai` | OpenAI API (chat: the LangGraph pipeline in `src/workflow/`) | `OPENAI_API_KEY` |

Select with `FINANCE_BACKEND=claude|openai`. `FINANCE_LLM_PROVIDER` overrides it for the batch jobs only.

## 🚀 Quick start

The easiest way is the launcher at the repo root, which starts the API, this gateway and the UI: `plugin/bin/finance start` (see the [root README](../README.md)).

To run this service alone (needs [uv](https://github.com/astral-sh/uv) and a running Finance API):

```bash
uv sync
FINANCE_API_URL=http://127.0.0.1:8080 uv run uvicorn app:app --reload --port 8000
uv run pytest        # offline: fake `claude`, stub LLMs, in-memory API
```

## 📡 Endpoints

### `POST /chat/stream`
Server-sent events for one chat turn. The same events are produced by every backend.

```json
{ "message": "Show my groceries", "session_id": "uuid", "user_id": "uuid", "workspace_id": 1 }
```

| Event | Payload |
|---|---|
| `status` | `{"tool": "list_transactions"}` — a tool call started |
| `text_delta` | `{"text": "..."}` — incremental assistant text |
| `ui_component` | `{"component": {"type", "order", "title", "action"}}` — a dashboard component to render |
| `done` | `{"text": "...", "ui": {"text": "", "components": [...]} \| null}` |
| `error` | `{"message": "..."}` (always last) |

### `POST /chat/session`
`{"session_id", "user_id"}` — pre-starts the agent so the first message skips the startup cost.

### `GET /agent/info`
Active backend, model and session counts.

### Jobs
- `POST /statements/upload` (multipart: `file`, **`account_id`** required; optional `X-Workspace-Id` / `X-User-Id` headers) → `{"job_id"}`, then poll `GET /statements/jobs/{job_id}`. CSV/Excel rows are parsed locally and categorized in batches; PDF chunks are parsed by the LLM in parallel; the API then deduplicates, records an undoable batch and detects transfers between own accounts.
- `POST /amazon/upload` and `GET /amazon/jobs/{job_id}` — match Amazon orders to bank transactions and re-categorize them from the products bought.
- `POST /search-transaction` — identify a merchant with web search.
- `GET /health`

## 🧱 Layout

- `src/agent/` — backends behind one interface (`claude_code.py`: one long-lived `claude -p --input-format stream-json` per chat session, resumed after idle; `openai_graph.py`).
- `src/llm/` — `LLMClient` (`structured`, `web_search`) with Claude and OpenAI implementations, plus batched categorization.
- `src/workflow/` — the LangGraph pipeline used by the `openai` backend.
- `src/api_client.py` — HTTP client for the Finance API.

## 🛠️ Configuration

| Variable | Default | Purpose |
|---|---|---|
| `FINANCE_BACKEND` | `claude` | chat engine (and default LLM provider) |
| `FINANCE_LLM_PROVIDER` | follows backend | batch-job provider |
| `FINANCE_API_URL` | `http://localhost:8080` | Finance API |
| `FINANCE_CLAUDE_MODEL` | `haiku` | model for the claude backend/provider |
| `FINANCE_SESSION_IDLE_TTL` / `FINANCE_SESSION_TTL` | `900` / `21600` s | stop an idle process / forget a conversation |
| `FINANCE_MAX_SESSIONS` | `4` | live `claude` processes |
| `FINANCE_BATCH_CONCURRENCY` | `3` | parallel `claude` calls for batch jobs |
| `OPENAI_API_KEY`, `FINANCE_OPENAI_MODEL` | – / `gpt-4o-mini` | OpenAI backend/provider |

Further overrides (`FINANCE_CLAUDE_BIN`, `FINANCE_AGENT_HOME`, `FINANCE_SKILL_PATH`, `FINANCE_MCP_COMMAND`, `FINANCE_TURN_TIMEOUT`, `FINANCE_BATCH_TIMEOUT`) are documented in [CLAUDE.md](../CLAUDE.md).

---
Part of the [Finance Assistant Monorepo](../)
