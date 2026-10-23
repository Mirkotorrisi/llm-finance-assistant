"""Agent backend on OpenAI function calling (needs OPENAI_API_KEY).

It is the same agent as the Claude backend with a different engine: the system prompt is the plugin's
SKILL.md and the tools are ``finance-mcp``'s registry (``openai_tools()`` / ``call_tool``), so a tool added
there works on both backends and nothing here maps tool names to REST calls.

One turn is a loop: stream the model's reply (text goes out as ``text_delta`` as it arrives), run the tool
calls it asks for, feed the results back, until it answers without calling a tool. The conversation is kept
in memory per chat session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from finance_mcp.registry import ApiClient, ToolError, call_tool, openai_tools

from src.agent.base import (
    AgentBackend,
    Event,
    done,
    error,
    status,
    text_delta,
    ui_component,
)
from src.agent.claude_code import ClaudeConfig, load_system_prompt, normalize_component

logger = logging.getLogger(__name__)

RENDER_TOOL = "render_component"
MAX_TOOL_RESULT_CHARS = 20_000
MAX_REMEMBERED_SESSIONS = 50


@dataclass
class OpenAIConfig:
    model: str = field(default_factory=lambda: os.getenv("FINANCE_OPENAI_MODEL", "gpt-4o-mini"))
    api_url: str = field(default_factory=lambda: os.getenv("FINANCE_API_URL", "http://localhost:8080"))
    max_steps: int = field(default_factory=lambda: int(os.getenv("FINANCE_MAX_TOOL_STEPS", "12")))
    turn_timeout: float = field(default_factory=lambda: float(os.getenv("FINANCE_TURN_TIMEOUT", "180")))
    session_ttl: float = field(default_factory=lambda: float(os.getenv("FINANCE_SESSION_TTL", "21600")))
    max_messages: int = field(default_factory=lambda: int(os.getenv("FINANCE_MAX_HISTORY_MESSAGES", "80")))
    skill_path: Path = field(default_factory=lambda: ClaudeConfig().skill_path)

    def __post_init__(self) -> None:
        self.skill_path = Path(self.skill_path)


@dataclass
class _Conversation:
    user_id: str
    workspace_id: int | None
    messages: list[dict[str, Any]] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used: float = field(default_factory=time.monotonic)


def _trim(messages: list[dict[str, Any]], limit: int) -> None:
    """Drop the oldest exchanges, always cutting at a user message so tool calls keep their results."""
    if len(messages) <= limit:
        return
    for i in range(len(messages) - limit, len(messages)):
        if messages[i]["role"] == "user":
            del messages[:i]
            return


def _tool_message(call_id: str, payload: Any) -> dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + '… [truncated: ask for a narrower period or filter]'
    return {"role": "tool", "tool_call_id": call_id, "content": text}


class OpenAIToolsBackend(AgentBackend):
    name = "openai"

    def __init__(self, cfg: OpenAIConfig | None = None, client: Any = None) -> None:
        self.cfg = cfg or OpenAIConfig()
        self._client = client  # injectable for tests; built on first use otherwise
        self._tools = openai_tools()
        self._sessions: dict[str, _Conversation] = {}

    # ------------------------------------------------------------------ plumbing

    def _openai(self) -> Any:
        if self._client is None:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is not set")
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=api_key)
        return self._client

    def _conversation(self, session_id: str, user_id: str, workspace_id: int | None) -> _Conversation:
        now = time.monotonic()
        for sid, conv in list(self._sessions.items()):
            if now - conv.last_used > self.cfg.session_ttl and not conv.lock.locked():
                self._sessions.pop(sid, None)
        conv = self._sessions.get(session_id)
        if conv is not None and (conv.user_id != user_id or conv.workspace_id != workspace_id):
            conv = None  # another user or project: the tools are bound to them, start afresh
        if conv is None:
            conv = _Conversation(user_id, workspace_id)
            self._sessions[session_id] = conv
            while len(self._sessions) > MAX_REMEMBERED_SESSIONS:
                oldest = min((k for k in self._sessions if k != session_id), key=lambda k: self._sessions[k].last_used, default=None)
                if oldest is None:
                    break
                self._sessions.pop(oldest, None)
        conv.last_used = now
        return conv

    # ------------------------------------------------------------------ one turn

    async def run_turn(
        self, session_id: str, user_id: str, message: str, workspace_id: int | None = None
    ) -> AsyncIterator[Event]:
        conv = self._conversation(session_id, user_id, workspace_id)
        async with conv.lock:
            checkpoint = len(conv.messages)
            conv.messages.append({"role": "user", "content": message})
            api = ApiClient(base_url=self.cfg.api_url, default_user_id=user_id, workspace_id=workspace_id)
            try:
                async for event in self._loop(conv, api):
                    yield event
                _trim(conv.messages, self.cfg.max_messages)
            except asyncio.CancelledError:  # the client went away mid-turn
                del conv.messages[checkpoint:]
                raise
            except Exception as e:  # surfaced to the client as an error event
                logger.exception("OpenAI turn failed")
                del conv.messages[checkpoint:]  # never keep tool calls that have no result
                yield error(f"OpenAI backend failed: {e}")
            finally:
                conv.last_used = time.monotonic()
                await api.aclose()

    async def _loop(self, conv: _Conversation, api: ApiClient) -> AsyncIterator[Event]:
        client = self._openai()
        deadline = time.monotonic() + self.cfg.turn_timeout
        system = {"role": "system", "content": load_system_prompt(self.cfg, conv.user_id)}  # type: ignore[arg-type]
        text_parts: list[str] = []
        components: list[dict[str, Any]] = []

        for _ in range(self.cfg.max_steps):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("the turn took too long")
            stream = await asyncio.wait_for(
                client.chat.completions.create(
                    model=self.cfg.model,
                    messages=[system, *conv.messages],
                    tools=self._tools,
                    stream=True,
                ),
                timeout=remaining,
            )
            content = ""
            calls: dict[int, dict[str, str]] = {}
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    if not content and text_parts:  # new text after tool calls: keep paragraphs apart
                        text_parts.append("\n\n")
                        yield text_delta("\n\n")
                    content += delta.content
                    text_parts.append(delta.content)
                    yield text_delta(delta.content)
                for tc in delta.tool_calls or []:
                    slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function:
                        slot["name"] += tc.function.name or ""
                        slot["arguments"] += tc.function.arguments or ""

            ordered = [calls[i] for i in sorted(calls)]
            assistant: dict[str, Any] = {"role": "assistant", "content": content or None}
            if ordered:
                assistant["tool_calls"] = [
                    {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                    for c in ordered
                ]
            conv.messages.append(assistant)
            if not ordered:
                yield done("".join(text_parts), components)
                return

            for c in ordered:
                if c["name"] != RENDER_TOOL:
                    yield status(c["name"])
                try:
                    args = json.loads(c["arguments"] or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be a JSON object")
                except ValueError as e:
                    conv.messages.append(_tool_message(c["id"], f"Error: invalid arguments: {e}"))
                    continue
                try:
                    result = await asyncio.wait_for(call_tool(api, c["name"], args), timeout=max(deadline - time.monotonic(), 1))
                except ToolError as e:
                    conv.messages.append(_tool_message(c["id"], f"Error: {e}"))
                    continue
                except Exception as e:  # noqa: BLE001 - the model should see a failure and can recover
                    logger.warning("tool %s failed: %s", c["name"], e)
                    conv.messages.append(_tool_message(c["id"], f"Error: {type(e).__name__}: {e}"))
                    continue
                conv.messages.append(_tool_message(c["id"], result))
                if c["name"] == RENDER_TOOL:
                    comp = normalize_component(args, len(components))
                    if comp.get("type"):
                        components.append(comp)
                        yield ui_component(comp)

        raise RuntimeError(f"no final answer after {self.cfg.max_steps} tool steps")

    # ------------------------------------------------------------------ lifecycle

    async def aclose(self) -> None:
        client, self._client = self._client, None
        close = getattr(client, "close", None)
        if close is not None:
            await close()

    def info(self) -> dict[str, Any]:
        return {"backend": self.name, "model": self.cfg.model, "sessions": len(self._sessions)}
