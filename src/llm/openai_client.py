"""OpenAI provider (needs OPENAI_API_KEY)."""

from __future__ import annotations

import os
from typing import Any

from src.llm.base import LLMClient, LLMError, T


class OpenAIClient(LLMClient):
    name = "openai"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.getenv("FINANCE_OPENAI_MODEL", "gpt-4o-mini")
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise LLMError("OPENAI_API_KEY is not set")
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=api_key)
        return self._client

    async def structured(self, system: str, user: str, schema: type[T], *, temperature: float = 0.0) -> T:
        client = self._get_client()
        try:
            completion = await client.beta.chat.completions.parse(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format=schema,
                temperature=temperature,
            )
        except Exception as e:
            raise LLMError(f"OpenAI request failed: {e}") from e
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise LLMError("OpenAI returned no structured output")
        return parsed

    async def web_search(self, prompt: str) -> str:
        client = self._get_client()
        try:
            response = await client.responses.create(
                model=self.model, tools=[{"type": "web_search_preview"}], input=prompt
            )
        except Exception as e:
            raise LLMError(f"OpenAI request failed: {e}") from e
        return response.output_text
