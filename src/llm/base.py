"""Provider-agnostic LLM interface for the batch jobs (statement parsing, categorization, search).

Interactive chat goes through ``src.agent``; this is for one-shot, non-conversational calls.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """An LLM call failed or the provider is not usable (e.g. missing API key)."""


class LLMClient(ABC):
    name: str = "abstract"

    @abstractmethod
    async def structured(self, system: str, user: str, schema: type[T], *, temperature: float = 0.0) -> T:
        """Return the model's answer validated against ``schema``."""

    @abstractmethod
    async def web_search(self, prompt: str) -> str:
        """Answer ``prompt`` using web search and return plain text."""
