"""FastAPI application bootstrap for the finance assistant."""

from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from src.agent import shutdown_backend
from src.chat_history import shutdown_history
from src.routes.agent_chat import router as agent_chat_router
from src.routes.amazon import router as amazon_router
from src.routes.banking import router as banking_router, scheduler as bank_scheduler
from src.banking.logging import install as install_log_redaction
from src.routes.core import router as core_router
from src.routes.search import router as search_router
from src.routes.statements import router as statements_router

from src.api_client import get_api_client, reset_api_client
from src.single_process import assert_single_process

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO line prints full URLs (bank account uids)
logger = logging.getLogger(__name__)


def cors_origins() -> list:
    """Browser origins allowed to call this service.

    ``FINANCE_CORS_ORIGINS`` (comma-separated) wins; else the UI's URL (``FINANCE_UI_URL``, set by the launcher) in
    both its ``localhost`` and ``127.0.0.1`` spellings; else the UI's default port. Never ``*``: the services carry
    no authentication, so any web page allowed through could read and change the user's data.
    """
    explicit = os.getenv("FINANCE_CORS_ORIGINS", "").strip()
    if explicit:
        return [o.strip().rstrip("/") for o in explicit.split(",") if o.strip()]
    ui_url = os.getenv("FINANCE_UI_URL", "http://localhost:3000").rstrip("/")
    origins = {ui_url}
    for a, b in (("localhost", "127.0.0.1"), ("127.0.0.1", "localhost")):
        if f"//{a}:" in ui_url or ui_url.endswith(f"//{a}"):
            origins.add(ui_url.replace(f"//{a}", f"//{b}", 1))
    return sorted(origins)

@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_single_process("llm-finance-assistant")  # chat sessions, jobs and confirmations live in this process
    logger.info("Finance agent starting up — API client initialised lazily on first request")
    install_log_redaction()  # bank authorization codes never reach the access log
    bank_scheduler().start()  # no-op unless Enable Banking is configured and FINANCE_BANK_SYNC=auto
    yield
    await bank_scheduler().stop()
    await shutdown_backend()
    await shutdown_history()
    client = await get_api_client()
    await client.disconnect()
    reset_api_client()
    logger.info("Finance agent shut down cleanly")

app = FastAPI(
    title="Finance Assistant API",
    description="A multimodal finance assistant supporting text and audio interactions",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=False,  # identity travels in X-User-Id, not in cookies
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(core_router)
app.include_router(agent_chat_router)
app.include_router(search_router)
app.include_router(statements_router)
app.include_router(amazon_router)
app.include_router(banking_router)


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)