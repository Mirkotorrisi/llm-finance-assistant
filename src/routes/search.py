"""Transaction search endpoint — uses the configured LLM provider's web search to identify merchants."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.llm import LLMError, get_llm_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["search"])


class TransactionSearchRequest(BaseModel):
    description: str


class TransactionSearchResponse(BaseModel):
    result: str


@router.post("/search-transaction", response_model=TransactionSearchResponse)
async def search_transaction(request: TransactionSearchRequest):
    """Identify a transaction merchant/service via web search."""
    if not request.description.strip():
        raise HTTPException(status_code=400, detail="description cannot be empty")

    prompt = (
        f"I found this entry on my bank statement: \"{request.description}\". "
        "Search the web and tell me in 2-3 sentences what this is: "
        "what kind of business or service it is, what it sells or does, "
        "and any other detail useful for categorizing it in a personal finance context. "
        "Be concise and factual."
    )

    try:
        return {"result": await get_llm_client().web_search(prompt)}
    except LLMError as e:
        logger.error("search-transaction error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
