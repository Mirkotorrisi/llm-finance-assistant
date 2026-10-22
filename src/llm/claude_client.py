"""Claude provider: one headless ``claude -p`` call per request, on the user's Claude login (no API key)."""

from __future__ import annotations

import asyncio
import json
import os
import weakref
from typing import Any

from pydantic import ValidationError

from src.llm.base import LLMClient, LLMError, T


class ClaudeClient(LLMClient):
    name = "claude"

    def __init__(
        self,
        bin: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        concurrency: int | None = None,
    ) -> None:
        self.bin = bin or os.getenv("FINANCE_CLAUDE_BIN", "claude")
        self.model = model or os.getenv("FINANCE_CLAUDE_MODEL", "haiku")
        self.timeout = timeout or float(os.getenv("FINANCE_BATCH_TIMEOUT", "180"))
        self.concurrency = concurrency or int(os.getenv("FINANCE_BATCH_CONCURRENCY", "3"))
        # asyncio primitives are bound to one event loop; keep one semaphore per loop
        self._semaphores: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
            weakref.WeakKeyDictionary()
        )

    def _semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        sem = self._semaphores.get(loop)
        if sem is None:
            sem = self._semaphores[loop] = asyncio.Semaphore(self.concurrency)
        return sem

    async def _run(self, prompt: str, extra_args: list[str]) -> dict[str, Any]:
        cmd = [
            self.bin, "-p",
            "--output-format", "json",
            "--setting-sources", "",
            "--model", self.model,
            "--no-session-persistence",
            *extra_args,
        ]
        async with self._semaphore():
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError as e:
                raise LLMError(f"Cannot start '{self.bin}': {e}") from e
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout=self.timeout)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                raise LLMError(f"claude did not answer within {self.timeout:.0f}s")
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            raise LLMError(f"claude returned invalid output (exit {proc.returncode}): {stderr.decode(errors='replace')[:300]}")
        if data.get("is_error"):
            raise LLMError(f"claude failed: {data.get('result')}")
        return data

    async def structured(self, system: str, user: str, schema: type[T], *, temperature: float = 0.0) -> T:
        # temperature has no equivalent in headless Claude Code; accepted for interface parity
        data = await self._run(
            user,
            [
                "--tools", "",
                "--system-prompt", system,
                "--json-schema", json.dumps(schema.model_json_schema()),
            ],
        )
        payload = data.get("structured_output")
        if payload is None:
            raise LLMError("claude returned no structured output")
        try:
            return schema.model_validate(payload)
        except ValidationError as e:
            raise LLMError(f"claude output does not match the schema: {e}") from e

    async def web_search(self, prompt: str) -> str:
        data = await self._run(prompt, ["--tools", "WebSearch", "--allowedTools", "WebSearch"])
        return str(data.get("result") or "")
