# Use a standard Python image
FROM python:3.13-slim

# Copy the uv binary from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# The build context is the repository root (see docker-compose.yml): the agent imports its tools from
# finance-mcp and reads the plugin's skill as its system prompt, so the layout mirrors the repo.
WORKDIR /app/llm-finance-assistant

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy from the cache instead of linking since it's a container
ENV UV_LINK_MODE=copy

# Build tools for dependencies that compile native extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# finance-mcp is a path dependency (../finance-mcp in pyproject.toml)
COPY finance-mcp /app/finance-mcp

# Install dependencies using uv
# We bind-mount pyproject.toml and uv.lock to install dependencies without copying the whole code first
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=llm-finance-assistant/uv.lock,target=uv.lock \
    --mount=type=bind,source=llm-finance-assistant/pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# The agent's system prompt
COPY plugin/skills/finance-assistant /app/plugin/skills/finance-assistant

# Copy the rest of the application
COPY llm-finance-assistant/ .

# Install the project itself
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Expose the Agent port
EXPOSE 8000

# Set environment variables for production
ENV PATH="/app/llm-finance-assistant/.venv/bin:$PATH"

# Run the agent using uvicorn
# app:app refers to the 'app' variable in the 'app.py' module
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
