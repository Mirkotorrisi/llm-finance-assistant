# Use a standard Python image
FROM python:3.13-slim

# Copy the uv binary from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy from the cache instead of linking since it's a container
ENV UV_LINK_MODE=copy

# Install system dependencies (necessary for packages like speechrecognition)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies using uv
# We bind-mount pyproject.toml and uv.lock to install dependencies without copying the whole code first
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Copy the rest of the application
COPY . .

# Install the project itself (includes context and nodes)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Expose the Agent port
EXPOSE 8000

# Set environment variables for production
ENV PATH="/app/.venv/bin:$PATH"

# Run the agent using uvicorn
# app:app refers to the 'app' variable in the 'app.py' module
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
