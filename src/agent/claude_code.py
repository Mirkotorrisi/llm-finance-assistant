"""Agent backend that drives a long-lived headless ``claude`` process per chat session.

Uses the user's own Claude Code login (subscription) — no API key. Each session
keeps one ``claude -p --input-format stream-json`` process alive so only the
first message pays the startup cost. Tools come from the ``finance-mcp`` stdio
server; nothing else (no shell, no files) is allowed.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.agent.base import (
    AgentBackend,
    AgentError,
    Event,
    done,
    error,
    status,
    text_delta,
    ui_component,
)

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
MCP_SERVER_NAME = "finance"
TOOL_PREFIX = f"mcp__{MCP_SERVER_NAME}__"
RENDER_TOOL = f"{TOOL_PREFIX}render_component"

FALLBACK_PROMPT = """You help one person track income and expenses through the finance tools.
Amounts: negative = expense, positive = income. Dates are YYYY-MM-DD. Reply in the user's language.
Call render_component next to your textual answer to show a dashboard component; never instead of it."""

PROMPT_HEADER = """You are the assistant inside the Finance Assistant chat app. Be concise and conversational; \
the app renders UI components next to your text, so do not repeat tabular data you already show with a component. \
The chat shows plain text only: never use markdown (no **bold**, no # headings, no tables); simple "- " lists are fine.
Today is {today}. The current user id is {user_id} (tools default to it)."""


@dataclass
class ClaudeConfig:
    bin: str = field(default_factory=lambda: os.getenv("FINANCE_CLAUDE_BIN", "claude"))
    model: str = field(default_factory=lambda: os.getenv("FINANCE_CLAUDE_MODEL", "haiku"))
    api_url: str = field(default_factory=lambda: os.getenv("FINANCE_API_URL", "http://localhost:8080"))
    home: Path = field(
        default_factory=lambda: Path(os.getenv("FINANCE_AGENT_HOME", str(Path.home() / ".finance-assistant" / "agent")))
    )
    skill_path: Path = field(
        default_factory=lambda: Path(
            os.getenv("FINANCE_SKILL_PATH", str(REPO_ROOT / "plugin" / "skills" / "finance-assistant" / "SKILL.md"))
        )
    )
    # further skills appended to the prompt (os.pathsep-separated in FINANCE_EXTRA_SKILL_PATHS); default: the advisor
    extra_skill_paths: list[Path] = field(default_factory=lambda: default_extra_skill_paths())
    mcp_command: list[str] | None = None  # [command, *args]; resolved lazily when None
    turn_timeout: float = field(default_factory=lambda: float(os.getenv("FINANCE_TURN_TIMEOUT", "180")))
    idle_ttl: float = field(default_factory=lambda: float(os.getenv("FINANCE_SESSION_IDLE_TTL", "900")))
    max_sessions: int = field(default_factory=lambda: int(os.getenv("FINANCE_MAX_SESSIONS", "4")))  # live processes
    session_ttl: float = field(default_factory=lambda: float(os.getenv("FINANCE_SESSION_TTL", "21600")))

    def resolve_mcp_command(self) -> list[str]:
        if self.mcp_command:
            return self.mcp_command
        env_cmd = os.getenv("FINANCE_MCP_COMMAND")
        if env_cmd:
            return env_cmd.split()
        project = REPO_ROOT / "finance-mcp"
        venv_bin = project / ".venv" / "bin" / "finance-mcp"
        if venv_bin.exists():
            return [str(venv_bin)]
        return ["uv", "run", "--quiet", "--project", str(project), "finance-mcp"]


def default_extra_skill_paths() -> list[Path]:
    raw = os.getenv("FINANCE_EXTRA_SKILL_PATHS")
    if raw is None:
        return [REPO_ROOT / "plugin" / "skills" / "advisor" / "SKILL.md"]
    return [Path(p) for p in raw.split(os.pathsep) if p.strip()]


def _skill_body(path: Path) -> str:
    """A skill file's text without its frontmatter."""
    return re.sub(r"\A---\n.*?\n---\n", "", path.read_text(encoding="utf-8"), flags=re.DOTALL).strip()


def load_system_prompt(cfg: ClaudeConfig, user_id: str, today: str | None = None) -> str:
    """The skills' text (frontmatter stripped, one after the other) behind a short header with today's date and
    the user id. The main skill falls back to a built-in prompt when missing; extra skills are skipped."""
    try:
        body = _skill_body(Path(cfg.skill_path))
    except OSError:
        logger.warning("Skill file %s not readable, using built-in prompt", cfg.skill_path)
        body = FALLBACK_PROMPT
    parts = [body]
    for extra in getattr(cfg, "extra_skill_paths", None) or []:
        try:
            parts.append(_skill_body(Path(extra)))
        except OSError:
            logger.warning("Extra skill file %s not readable, skipped", extra)
    header = PROMPT_HEADER.format(today=today or datetime.date.today().isoformat(), user_id=user_id)
    return "\n\n".join([header, *parts])


def normalize_component(tool_input: dict[str, Any], index: int) -> dict[str, Any]:
    comp: dict[str, Any] = {
        "type": tool_input.get("type"),
        "order": tool_input.get("order", index),
        "title": tool_input.get("title"),
    }
    action = tool_input.get("action")
    if isinstance(action, dict):
        comp["action"] = {
            "service": action.get("service", ""),
            "method": action.get("method", ""),
            "params": action.get("params") or {},
        }
    return {k: v for k, v in comp.items() if v is not None}


class TurnParser:
    """Turns raw stream-json lines of one turn into backend events."""

    def __init__(self) -> None:
        self.text_parts: list[str] = []
        self.components: list[dict[str, Any]] = []
        self._seen_tool_ids: set[str] = set()
        self._streamed = False

    def feed(self, ev: dict[str, Any]) -> list[Event]:
        out: list[Event] = []
        etype = ev.get("type")

        if etype == "stream_event":
            inner = ev.get("event") or {}
            itype = inner.get("type")
            if itype == "content_block_start":
                block = inner.get("content_block") or {}
                if block.get("type") == "tool_use":
                    name = block.get("name", "")
                    if name != RENDER_TOOL:
                        out.append(status(name.removeprefix(TOOL_PREFIX)))
                elif block.get("type") == "text" and self.text_parts:
                    # a new text block after tool calls: keep paragraphs apart
                    self.text_parts.append("\n\n")
                    out.append(text_delta("\n\n"))
            elif itype == "content_block_delta":
                delta = inner.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    self._streamed = True
                    self.text_parts.append(delta["text"])
                    out.append(text_delta(delta["text"]))

        elif etype == "assistant":
            for block in (ev.get("message") or {}).get("content") or []:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == RENDER_TOOL
                    and block.get("id") not in self._seen_tool_ids
                ):
                    self._seen_tool_ids.add(block.get("id"))
                    comp = normalize_component(block.get("input") or {}, len(self.components))
                    if comp.get("type"):
                        self.components.append(comp)
                        out.append(ui_component(comp))

        return out

    def finish(self, result: dict[str, Any]) -> list[Event]:
        if result.get("is_error"):
            return [error(str(result.get("result") or "The agent failed to answer."))]
        out: list[Event] = []
        final_text = "".join(self.text_parts)
        if not self._streamed:
            # partial messages unavailable: fall back to the final text
            final_text = str(result.get("result") or "")
            if final_text:
                out.append(text_delta(final_text))
        out.append(done(final_text, self.components))
        return out


class ClaudeSession:
    """One persistent ``claude`` process serving a chat session, one turn at a time."""

    def __init__(self, session_id: str, user_id: str, cfg: ClaudeConfig, workspace_id: int | None = None):
        self.session_id = session_id
        self.user_id = user_id
        self.workspace_id = workspace_id
        self.cfg = cfg
        self.claude_session_id = str(uuid.uuid4())
        self.proc: asyncio.subprocess.Process | None = None
        self.lock = asyncio.Lock()
        self.turns_completed = 0
        self.last_used = time.monotonic()
        self._stderr_task: asyncio.Task | None = None
        self._mcp_status: str | None = None

    def _write_mcp_config(self) -> Path:
        self.cfg.home.mkdir(parents=True, exist_ok=True)
        command, *args = self.cfg.resolve_mcp_command()
        path = self.cfg.home / f"mcp-{self.claude_session_id}.json"
        path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        MCP_SERVER_NAME: {
                            "command": command,
                            "args": args,
                            "env": {
                                "FINANCE_API_URL": self.cfg.api_url,
                                "FINANCE_GATEWAY_URL": os.getenv("FINANCE_GATEWAY_URL", "http://127.0.0.1:8000"),
                                "FINANCE_USER_ID": self.user_id,
                                **({"FINANCE_WORKSPACE_ID": str(self.workspace_id)} if self.workspace_id is not None else {}),
                            },
                        }
                    }
                }
            )
        )
        return path

    async def start(self) -> None:
        if self.alive:
            return
        mcp_config = self._write_mcp_config()
        session_flag = ["--resume", self.claude_session_id] if self.turns_completed else ["--session-id", self.claude_session_id]
        cmd = [
            self.cfg.bin, "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--include-partial-messages", "--verbose",
            "--tools", "",
            "--setting-sources", "",
            "--model", self.cfg.model,
            "--mcp-config", str(mcp_config), "--strict-mcp-config",
            "--allowedTools", f"{TOOL_PREFIX}*",
            "--system-prompt", load_system_prompt(self.cfg, self.user_id),
            *session_flag,
        ]
        logger.info("Starting claude session %s (resume=%s)", self.session_id, bool(self.turns_completed))
        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.cfg.home),
            limit=2**24,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr(self.proc))

    @staticmethod
    async def _drain_stderr(proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        async for line in proc.stderr:
            logger.warning("claude stderr: %s", line.decode(errors="replace").rstrip())

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def kill(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        if self._stderr_task:
            self._stderr_task.cancel()
            self._stderr_task = None
        try:
            (self.cfg.home / f"mcp-{self.claude_session_id}.json").unlink(missing_ok=True)
        except OSError:
            pass

    async def turn(self, message: str) -> AsyncIterator[Event]:
        async with self.lock:
            self.last_used = time.monotonic()
            await self.start()
            assert self.proc and self.proc.stdin and self.proc.stdout
            parser = TurnParser()
            finished = False
            today = datetime.date.today().isoformat()
            payload = {"type": "user", "message": {"role": "user", "content": f"[today: {today}] {message}"}}
            try:
                self.proc.stdin.write((json.dumps(payload) + "\n").encode())
                await self.proc.stdin.drain()
                deadline = time.monotonic() + self.cfg.turn_timeout
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise AgentError(f"The agent did not answer within {self.cfg.turn_timeout:.0f}s.")
                    try:
                        line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=remaining)
                    except TimeoutError:
                        raise AgentError(f"The agent did not answer within {self.cfg.turn_timeout:.0f}s.")
                    if not line:
                        raise AgentError("The agent process exited unexpectedly.")
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("type") == "system" and ev.get("subtype") == "init":
                        servers = {s.get("name"): s.get("status") for s in ev.get("mcp_servers") or []}
                        self._mcp_status = servers.get(MCP_SERVER_NAME)
                        if self._mcp_status and self._mcp_status != "connected":
                            raise AgentError(f"Finance tools are unavailable (MCP server status: {self._mcp_status}).")
                    elif ev.get("type") == "result":
                        for out in parser.finish(ev):
                            yield out
                        finished = True
                        self.turns_completed += 1
                        return
                    else:
                        for out in parser.feed(ev):
                            yield out
            except AgentError as e:
                yield error(str(e))
            finally:
                self.last_used = time.monotonic()
                if not finished:
                    # abandoned or failed mid-turn: stale events would poison the next turn
                    await self.kill()


MAX_REMEMBERED_SESSIONS = 50


class ClaudeCodeBackend(AgentBackend):
    """Chat sessions on top of ``ClaudeSession``.

    A session's *process* is cheap to drop and is stopped when idle (``idle_ttl``) or when too many are
    live (``max_sessions``); the conversation survives because the next message respawns it with
    ``--resume``. A session is forgotten entirely after ``session_ttl`` without messages.
    """

    name = "claude"

    def __init__(self, cfg: ClaudeConfig | None = None):
        self.cfg = cfg or ClaudeConfig()
        self._sessions: dict[str, ClaudeSession] = {}
        self._reaper: asyncio.Task | None = None

    def _session(self, session_id: str, user_id: str, workspace_id: int | None = None) -> ClaudeSession:
        session = self._sessions.get(session_id)
        if session is not None and (session.user_id != user_id or session.workspace_id != workspace_id):
            # another user or project: the tools are bound to them, so start a fresh conversation
            asyncio.create_task(self._stop(session))
            session = None
        if session is None:
            session = ClaudeSession(session_id, user_id, self.cfg, workspace_id)
            self._sessions[session_id] = session
            self._forget_over_limit(keep=session_id)
        self._limit_live_processes(keep=session_id)
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap_loop())
        return session

    @staticmethod
    async def _stop(session: ClaudeSession) -> None:
        """Stop the process without racing a turn that is about to use it."""
        async with session.lock:
            await session.kill()

    def _limit_live_processes(self, keep: str) -> None:
        others = [s for s in self._sessions.values() if s.alive and s.session_id != keep and not s.lock.locked()]
        excess = len(others) - (self.cfg.max_sessions - 1)
        for victim in sorted(others, key=lambda s: s.last_used)[: max(excess, 0)]:
            asyncio.create_task(self._stop(victim))

    def _forget_over_limit(self, keep: str) -> None:
        while len(self._sessions) > MAX_REMEMBERED_SESSIONS:
            victim = min((s for s in self._sessions.values() if s.session_id != keep), key=lambda s: s.last_used, default=None)
            if victim is None:
                return
            self._sessions.pop(victim.session_id, None)
            asyncio.create_task(self._stop(victim))

    async def _reap_once(self) -> None:
        now = time.monotonic()
        for sid, session in list(self._sessions.items()):
            if session.lock.locked():
                continue
            if now - session.last_used > self.cfg.session_ttl:
                self._sessions.pop(sid, None)
                await self._stop(session)
            elif session.alive and now - session.last_used > self.cfg.idle_ttl:
                await self._stop(session)

    async def _reap_loop(self) -> None:
        while self._sessions:
            await asyncio.sleep(max(self.cfg.idle_ttl / 4, 1))
            await self._reap_once()

    async def run_turn(
        self, session_id: str, user_id: str, message: str, workspace_id: int | None = None
    ) -> AsyncIterator[Event]:
        async for ev in self._session(session_id, user_id, workspace_id).turn(message):
            yield ev

    async def warm(self, session_id: str, user_id: str, workspace_id: int | None = None) -> None:
        session = self._session(session_id, user_id, workspace_id)
        async with session.lock:
            await session.start()

    async def aclose(self) -> None:
        if self._reaper:
            self._reaper.cancel()
        sessions, self._sessions = list(self._sessions.values()), {}
        await asyncio.gather(*(s.kill() for s in sessions), return_exceptions=True)

    def info(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "model": self.cfg.model,
            "sessions": len(self._sessions),
            "live_processes": sum(1 for s in self._sessions.values() if s.alive),
        }
