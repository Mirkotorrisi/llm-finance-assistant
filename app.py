"""FastAPI application bootstrap for the finance assistant."""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from src.routes.chat import router as chat_router
from src.routes.core import router as core_router
from src.routes.search import router as search_router
from src.routes.statements import router as statements_router

from src.workflow.mcp_client import get_mcp_client, reset_mcp_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Finance agent starting up — API client initialised lazily on first request")
    yield
    client = await get_mcp_client()
    await client.disconnect()
    reset_mcp_client()
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
app.include_router(chat_router)
app.include_router(search_router)
app.include_router(statements_router)


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)