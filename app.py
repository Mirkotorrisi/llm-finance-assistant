"""FastAPI application bootstrap for the finance assistant."""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from src.agent import shutdown_backend
from src.routes.agent_chat import router as agent_chat_router
from src.routes.amazon import router as amazon_router
from src.routes.banking import router as banking_router, scheduler as bank_scheduler
from src.banking.logging import install as install_log_redaction
from src.routes.core import router as core_router
from src.routes.search import router as search_router
from src.routes.statements import router as statements_router

from src.api_client import get_api_client, reset_api_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Finance agent starting up — API client initialised lazily on first request")
    install_log_redaction()  # bank authorization codes never reach the access log
    bank_scheduler().start()  # no-op unless Enable Banking is configured and FINANCE_BANK_SYNC=auto
    yield
    await bank_scheduler().stop()
    await shutdown_backend()
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
    allow_origins=["*"],
    allow_credentials=True,
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