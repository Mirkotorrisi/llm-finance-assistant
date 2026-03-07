"""Domain routers for the API layer."""

from src.api.routes.chat import router as chat_router
from src.api.routes.core import router as core_router
from src.api.routes.finance import router as finance_router
from src.api.routes.statements import router as statements_router

__all__ = [
    "chat_router",
    "core_router",
    "finance_router",
    "statements_router",
]
