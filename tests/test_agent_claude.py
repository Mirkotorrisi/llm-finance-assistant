"""Tests for the Claude Code agent backend, using a fake `claude` that speaks stream-json."""

import asyncio
import json
import stat
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.agent import create_backend
from src.agent.base import AgentBackend, done, error, text_delta
from src.agent.claude_code import (
    ClaudeCodeBackend,
    ClaudeConfig,
    TurnParser,
    load_system_prompt,
    normalize_component,
)

FAKE_CLAUDE = textwrap.dedent(
    """\
    #!{python}
    import json, os, sys

    with open(os.environ["FAKE_ARGV_LOG"], "a") as f:
        f.write(json.dumps(sys.argv[1:]) + "\\n")

    def emit(obj):
        print(json.dumps(obj), flush=True)

    turn = 0
    for line in sys.stdin:
        msg = json.loads(line)["message"]["content"]
        turn += 1
        if "boom" in msg:
            sys.exit(1)
        emit({{"type": "system", "subtype": "init",
              "mcp_servers": [{{"name": "finance", "status": os.environ.get("FAKE_MCP_STATUS", "connected")}}]}})
        emit({{"type": "stream_event", "event": {{"type": "content_block_start",
              "content_block": {{"type": "tool_use", "name": "mcp__finance__list_transactions"}}}}}})
        emit({{"type": "stream_event", "event": {{"type": "content_block_start", "content_block": {{"type": "text"}}}}}})
        for chunk in ("Turno ", str(turn), " ok"):
            emit({{"type": "stream_event", "event": {{"type": "content_block_delta",
                  "delta": {{"type": "text_delta", "text": chunk}}}}}})
        if "cancella" in msg:
            pending = {{"pending_confirmation": True, "confirmation_id": "abc123def456", "tool": "delete_transaction",
                       "args": {{"transaction_id": 7}}, "message": "NOT executed"}}
            emit({{"type": "user", "message": {{"content": [{{"type": "tool_result", "tool_use_id": "d%d" % turn,
                  "content": [{{"type": "text", "text": json.dumps(pending)}}]}}]}}}})
        emit({{"type": "assistant", "message": {{"content": [{{"type": "tool_use", "id": "t%d" % turn,
              "name": "mcp__finance__render_component",
              "input": {{"type": "TransactionsTable", "title": "Tabella",
                        "action": {{"service": "transactions", "method": "list", "params": {{"category": "x"}}}}}}}}]}}}})
        emit({{"type": "result", "is_error": False, "result": "Turno %d ok" % turn}})
    """
)


@pytest.fixture
def fake_cfg(tmp_path, monkeypatch):
    script = tmp_path / "fake-claude"
    script.write_text(FAKE_CLAUDE.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    argv_log = tmp_path / "argv.log"
    monkeypatch.setenv("FAKE_ARGV_LOG", str(argv_log))
    return ClaudeConfig(
        bin=str(script),
        home=tmp_path / "home",
        skill_path=tmp_path / "missing-skill.md",
        mcp_command=["/bin/true"],
        turn_timeout=10,
    )


def run(coro):
    return asyncio.run(coro)


async def collect(backend, session="s1", user="u1", message="ciao"):
    return [ev async for ev in backend.run_turn(session, user, message)]


def test_turn_streams_status_text_component_and_done(fake_cfg):
    async def scenario():
        backend = ClaudeCodeBackend(fake_cfg)
        try:
            return await collect(backend)
        finally:
            await backend.aclose()

    events = run(scenario())
    types = [e["type"] for e in events]
    assert types[0] == "status" and events[0]["tool"] == "list_transactions"
    assert "".join(e["text"] for e in events if e["type"] == "text_delta") == "Turno 1 ok"
    comp = next(e["component"] for e in events if e["type"] == "ui_component")
    assert comp == {
        "type": "TransactionsTable",
        "order": 0,
        "title": "Tabella",
        "action": {"service": "transactions", "method": "list", "params": {"category": "x"}},
    }
    assert events[-1]["type"] == "done"
    assert events[-1]["text"] == "Turno 1 ok"
    assert events[-1]["ui"] == {"text": "", "components": [comp]}


def test_same_process_serves_consecutive_turns(fake_cfg):
    async def scenario():
        backend = ClaudeCodeBackend(fake_cfg)
        try:
            first = await collect(backend)
            second = await collect(backend)
            return first, second
        finally:
            await backend.aclose()

    first, second = run(scenario())
    assert first[-1]["text"] == "Turno 1 ok"
    assert second[-1]["text"] == "Turno 2 ok"  # turn counter lives in the process => no respawn
    argv_log = Path(fake_cfg.home.parent / "argv.log").read_text().strip().splitlines()
    assert len(argv_log) == 1


def test_crash_yields_error_then_respawns_with_resume(fake_cfg):
    async def scenario():
        backend = ClaudeCodeBackend(fake_cfg)
        try:
            ok = await collect(backend)
            crashed = await collect(backend, message="boom")
            after = await collect(backend)
            return ok, crashed, after
        finally:
            await backend.aclose()

    ok, crashed, after = run(scenario())
    assert ok[-1]["type"] == "done"
    assert crashed[-1]["type"] == "error" and "exited" in crashed[-1]["message"]
    assert after[-1]["type"] == "done"
    argvs = [json.loads(l) for l in (fake_cfg.home.parent / "argv.log").read_text().strip().splitlines()]
    assert len(argvs) == 2  # the crashed turn reused the live process; only the next turn respawns
    assert "--session-id" in argvs[0] and "--resume" not in argvs[0]
    assert "--resume" in argvs[1]


def test_unavailable_mcp_server_is_reported(fake_cfg, monkeypatch):
    monkeypatch.setenv("FAKE_MCP_STATUS", "failed")

    async def scenario():
        backend = ClaudeCodeBackend(fake_cfg)
        try:
            return await collect(backend)
        finally:
            await backend.aclose()

    events = run(scenario())
    assert events[-1]["type"] == "error" and "failed" in events[-1]["message"]


def test_spawn_command_is_locked_down(fake_cfg):
    async def scenario():
        backend = ClaudeCodeBackend(fake_cfg)
        try:
            await collect(backend, user="u-42")
        finally:
            await backend.aclose()

    run(scenario())
    argv = json.loads((fake_cfg.home.parent / "argv.log").read_text().splitlines()[0])
    flag = lambda name: argv[argv.index(name) + 1]
    assert flag("--tools") == ""
    assert flag("--allowedTools") == "mcp__finance__*"
    assert "--strict-mcp-config" in argv
    assert "--bare" not in argv
    assert "u-42" in flag("--system-prompt")


def test_parser_falls_back_to_result_text_without_partials():
    parser = TurnParser()
    out = parser.finish({"is_error": False, "result": "Solo risultato"})
    assert [e["type"] for e in out] == ["text_delta", "done"]
    assert out[-1]["text"] == "Solo risultato"


def test_parser_separates_text_blocks_and_dedups_components():
    parser = TurnParser()
    block = lambda t: {"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": t}}}
    delta = lambda s: {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": s}}}
    render = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a", "name": "mcp__finance__render_component", "input": {"type": "SummaryCards"}}]}}
    events = []
    for ev in [block("text"), delta("A"), block("text"), delta("B"), render, render]:
        events += parser.feed(ev)
    events += parser.finish({"is_error": False, "result": "AB"})  # components are shown on their result, or at the end
    assert "".join(e["text"] for e in events if e["type"] == "text_delta") == "A\n\nB"
    assert sum(e["type"] == "ui_component" for e in events) == 1


def test_parser_surfaces_agent_errors():
    out = TurnParser().finish({"is_error": True, "result": "rate limited"})
    assert out == [error("rate limited")]


def test_normalize_component_defaults_order_and_drops_empty():
    assert normalize_component({"type": "SummaryCards"}, 3) == {"type": "SummaryCards", "order": 3}


def test_system_prompt_strips_frontmatter(tmp_path):
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: x\n---\n\n# Rules\nBe nice.\n")
    cfg = ClaudeConfig(skill_path=skill)
    prompt = load_system_prompt(cfg, "u9", today="2030-01-02")
    assert "2030-01-02" in prompt and "u9" in prompt and "Be nice." in prompt
    assert "name: x" not in prompt
    assert "never use markdown" in prompt


def test_system_prompt_falls_back_when_skill_missing(tmp_path):
    prompt = load_system_prompt(ClaudeConfig(skill_path=tmp_path / "nope.md"), "u", today="2030-01-02")
    assert "render_component" in prompt


def test_factory_selects_backend(monkeypatch):
    assert create_backend("claude").name == "claude"
    assert create_backend("openai").name == "openai"
    monkeypatch.setenv("FINANCE_BACKEND", "openai")
    assert create_backend().name == "openai"
    with pytest.raises(ValueError, match="Unknown FINANCE_BACKEND"):
        create_backend("gemini")


class _StubBackend(AgentBackend):
    name = "stub"

    async def run_turn(self, session_id, user_id, message, workspace_id=None):
        yield text_delta(f"{session_id}/{user_id}/{message}/{workspace_id}")
        yield done("fine", [])


def test_chat_stream_route_emits_sse(monkeypatch):
    from app import app

    monkeypatch.setattr("src.routes.agent_chat.get_backend", lambda: _StubBackend())
    client = TestClient(app)
    res = client.post("/chat/stream", json={"message": "hi", "session_id": "s", "user_id": "u"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(chunk[len("data: "):]) for chunk in res.text.strip().split("\n\n")]
    assert events == [{"type": "text_delta", "text": "s/u/hi/None"}, {"type": "done", "text": "fine", "ui": None}]
    assert client.get("/agent/info").json() == {"backend": "stub"}


def _spawn_argvs(cfg):
    return [json.loads(line) for line in (cfg.home.parent / "argv.log").read_text().strip().splitlines()]


def test_idle_reaping_stops_the_process_but_keeps_the_conversation(fake_cfg):
    fake_cfg.idle_ttl = 60

    async def scenario():
        backend = ClaudeCodeBackend(fake_cfg)
        try:
            await collect(backend)
            session = backend._sessions["s1"]
            assert session.alive
            session.last_used -= 120  # idle for two minutes
            await backend._reap_once()
            assert not session.alive and "s1" in backend._sessions
            return await collect(backend)
        finally:
            await backend.aclose()

    after = run(scenario())
    assert after[-1]["type"] == "done"
    argvs = _spawn_argvs(fake_cfg)
    assert len(argvs) == 2 and "--session-id" in argvs[0] and "--resume" in argvs[1]
    assert argvs[0][argvs[0].index("--session-id") + 1] == argvs[1][argvs[1].index("--resume") + 1]  # same conversation


def test_session_is_forgotten_after_its_ttl(fake_cfg):
    fake_cfg.session_ttl = 60

    async def scenario():
        backend = ClaudeCodeBackend(fake_cfg)
        try:
            await collect(backend)
            backend._sessions["s1"].last_used -= 120
            await backend._reap_once()
            assert "s1" not in backend._sessions
            await collect(backend)
        finally:
            await backend.aclose()

    run(scenario())
    argvs = _spawn_argvs(fake_cfg)
    assert len(argvs) == 2 and all("--resume" not in a for a in argvs)  # a fresh conversation


def test_live_process_cap_stops_the_least_recent_but_resumes_it_later(fake_cfg):
    fake_cfg.max_sessions = 2

    async def scenario():
        backend = ClaudeCodeBackend(fake_cfg)
        try:
            for sid in ("a", "b", "c"):
                await collect(backend, session=sid)
                await asyncio.sleep(0)  # let the stop task run
            await asyncio.sleep(0.2)
            live = sorted(s.session_id for s in backend._sessions.values() if s.alive)
            assert len(backend._sessions) == 3 and len(live) == 2 and "a" not in live
            result = await collect(backend, session="a")
            return result
        finally:
            await backend.aclose()

    assert run(scenario())[-1]["type"] == "done"
    assert any("--resume" in a for a in _spawn_argvs(fake_cfg))


def test_workspace_reaches_the_tools_and_changing_it_starts_a_new_conversation(fake_cfg):
    import src.agent.claude_code as cc

    session = cc.ClaudeSession("s", "alice", fake_cfg, workspace_id=7)
    env = json.loads(session._write_mcp_config().read_text())["mcpServers"]["finance"]["env"]
    assert env["FINANCE_WORKSPACE_ID"] == "7" and env["FINANCE_USER_ID"] == "alice"

    default = cc.ClaudeSession("s", "alice", fake_cfg)  # no project selected: the API's default one
    assert "FINANCE_WORKSPACE_ID" not in json.loads(default._write_mcp_config().read_text())["mcpServers"]["finance"]["env"]

    async def scenario():
        backend = ClaudeCodeBackend(fake_cfg)
        try:
            first = backend._session("s", "alice", 1)
            assert backend._session("s", "alice", 1) is first  # same project: same conversation
            second = backend._session("s", "alice", 2)
            assert second is not first and second.workspace_id == 2  # other project: tools are bound, start fresh
        finally:
            await backend.aclose()

    run(scenario())


def test_chat_stream_forwards_the_workspace(monkeypatch):
    from app import app

    monkeypatch.setattr("src.routes.agent_chat.get_backend", lambda: _StubBackend())
    res = TestClient(app).post("/chat/stream", json={"message": "hi", "session_id": "s", "user_id": "u", "workspace_id": 4})
    assert json.loads(res.text.split("\n\n")[0][len("data: "):])["text"] == "s/u/hi/4"


def test_system_prompt_appends_extra_skills_and_skips_missing_ones(tmp_path):
    main = tmp_path / "SKILL.md"
    main.write_text("---\nname: main\n---\nMain rules.\n")
    advisor = tmp_path / "advisor.md"
    advisor.write_text("---\nname: advisor\n---\nAdvisor rules.\n")
    cfg = ClaudeConfig(skill_path=main, extra_skill_paths=[advisor, tmp_path / "missing.md"])
    prompt = load_system_prompt(cfg, "u", today="2030-01-02")
    assert prompt.index("Main rules.") < prompt.index("Advisor rules.")
    assert "name: advisor" not in prompt


def test_extra_skill_paths_come_from_the_environment(monkeypatch, tmp_path):
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    monkeypatch.setenv("FINANCE_EXTRA_SKILL_PATHS", f"{a}{__import__('os').pathsep}{b}")
    assert ClaudeConfig().extra_skill_paths == [a, b]
    monkeypatch.setenv("FINANCE_EXTRA_SKILL_PATHS", "")
    assert ClaudeConfig().extra_skill_paths == []


def test_shipped_skills_build_one_prompt_of_reasonable_size():
    cfg = ClaudeConfig()  # the real finance-assistant and advisor skills
    assert cfg.skill_path.exists() and all(p.exists() for p in cfg.extra_skill_paths)
    prompt = load_system_prompt(cfg, "u", today="2030-01-02")
    assert "Financial advisor" in prompt and "render_component" in prompt
    assert len(prompt) < 40_000, len(prompt)  # the prompt is paid on every turn: keep long texts in references


def test_a_deferred_destructive_tool_becomes_a_confirmation_card(fake_cfg):
    """The tool result says pending_confirmation: the UI gets a ConfirmAction card bound to the session's identity,
    and the gateway remembers it until the user clicks."""
    from src.agent.confirm import pending_actions

    backend = ClaudeCodeBackend(fake_cfg)

    async def scenario():
        return [ev async for ev in backend.run_turn("s1", "alice", "cancella la 7", workspace_id=3)]

    events = run(scenario())
    cards = [e["component"] for e in events if e["type"] == "ui_component" and e["component"]["type"] == "ConfirmAction"]
    assert len(cards) == 1
    params = cards[0]["action"]["params"]
    assert params == {"confirmationId": "abc123def456", "tool": "delete_transaction", "args": {"transaction_id": 7}}
    assert cards[0] in events[-1]["ui"]["components"]
    item = pending_actions.pop("abc123def456")
    assert item and (item.user_id, item.workspace_id, item.tool) == ("alice", 3, "delete_transaction")
    assert pending_actions.pop("abc123def456") is None  # one click only
    run(backend.aclose())


def test_the_mcp_config_asks_finance_mcp_to_defer_destructive_tools(fake_cfg):
    backend = ClaudeCodeBackend(fake_cfg)
    run(collect(backend, "s2", "alice", "ciao"))
    config = json.loads(next((fake_cfg.home).glob("mcp-*.json")).read_text())
    assert config["mcpServers"]["finance"]["env"]["FINANCE_MCP_CONFIRM_WRITES"] == "1"
    run(backend.aclose())


def test_chat_confirm_runs_the_pending_tool_as_the_agent_user(monkeypatch):
    """The click executes the remembered action with the agent's identity; a second click or a bogus id is 404."""
    import httpx
    from app import app
    from src.agent import confirm
    from src.agent.confirm import pending_actions

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"message": "deleted"})

    class MockedApiClient(confirm.ApiClient):
        def _http(self):
            if self._client is None or self._client.is_closed:
                headers = {"X-User-Id": self.default_user_id}
                if self.workspace_id is not None:
                    headers["X-Workspace-Id"] = str(self.workspace_id)
                self._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=self.base_url, headers=headers)
            return self._client

    monkeypatch.setattr(confirm, "ApiClient", MockedApiClient)
    pending = {"pending_confirmation": True, "confirmation_id": "pend0001", "tool": "delete_transaction", "args": {"transaction_id": 9}}
    assert pending_actions.register(pending, "s", "alice", 4)["type"] == "ConfirmAction"
    pending_actions.register({**pending, "confirmation_id": "pend0002"}, "s", "alice", 4)
    client = TestClient(app)
    res = client.post("/chat/confirm", json={"confirmation_id": "pend0001"})
    assert res.status_code == 200 and res.json() == {"status": "done", "tool": "delete_transaction", "result": {"message": "deleted"}}
    assert len(seen) == 1 and seen[0].method == "DELETE" and seen[0].url.path == "/api/transactions/9"
    assert seen[0].headers["x-user-id"] == "alice" and seen[0].headers["x-workspace-id"] == "4"
    assert client.post("/chat/confirm", json={"confirmation_id": "pend0001"}).status_code == 404
    assert client.post("/chat/confirm", json={"confirmation_id": "pend0002", "approve": False}).json() == {"status": "cancelled", "tool": "delete_transaction"}
    assert len(seen) == 1
    assert client.post("/chat/confirm", json={"confirmation_id": "nothere1"}).status_code == 404


def test_a_component_the_registry_rejected_is_not_shown():
    """The card is emitted when render_component's result comes back without error, not when the call is made."""
    parser = TurnParser()
    call = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "r1", "name": "mcp__finance__render_component", "input": {"type": "SummaryCards"}},
        {"type": "tool_use", "id": "r2", "name": "mcp__finance__render_component", "input": {"type": "Evil"}},
    ]}}
    assert parser.feed(call) == []
    ok = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "r1", "content": "{\"rendered\": true}"}]}}
    bad = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "r2", "is_error": True, "content": "Unknown component type"}]}}
    assert [e["component"]["type"] for e in parser.feed(ok)] == ["SummaryCards"]
    assert parser.feed(bad) == []
    final = parser.finish({"is_error": False, "result": "ok"})
    assert [c["type"] for c in final[-1]["ui"]["components"]] == ["SummaryCards"]


def test_components_without_a_tool_result_are_still_shown_at_the_end():
    parser = TurnParser()
    parser.feed({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "r1", "name": "mcp__finance__render_component", "input": {"type": "SummaryCards"}}]}})
    final = parser.finish({"is_error": False, "result": "ok"})
    assert [e["type"] for e in final] == ["ui_component", "text_delta", "done"]


def test_the_process_cap_refuses_a_new_session_when_every_other_is_busy(fake_cfg):
    """max_sessions is a hard cap: with every live session mid-turn, a new one gets an error instead of a process."""
    fake_cfg.max_sessions = 1
    backend = ClaudeCodeBackend(fake_cfg)

    async def scenario():
        first = backend._session("busy", "u1")
        async with first.lock:  # a turn in progress
            await first.start()
            events = [ev async for ev in backend.run_turn("other", "u2", "ciao")]
        return events

    events = run(scenario())
    assert [e["type"] for e in events] == ["error"] and "busy" in events[0]["message"]
    assert backend.info()["live_processes"] == 1
    run(backend.aclose())
