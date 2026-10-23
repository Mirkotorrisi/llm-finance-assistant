"""The OpenAI backend: a function-calling loop over finance-mcp's registry, driven here by a scripted fake client."""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from finance_mcp.registry import ToolError

import src.agent.openai_tools as mod
from src.agent import create_backend
from src.agent.openai_tools import OpenAIConfig, OpenAIToolsBackend


def run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------------------- a scripted OpenAI client

def text(*parts):
    return [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=p, tool_calls=None))]) for p in parts]


def call(index, name, args, call_id=None):
    """A tool call, split over two chunks like the real stream does."""
    raw = args if isinstance(args, str) else json.dumps(args)
    head = SimpleNamespace(index=index, id=call_id or f"call_{index}", function=SimpleNamespace(name=name, arguments=raw[:3]))
    tail = SimpleNamespace(index=index, id=None, function=SimpleNamespace(name=None, arguments=raw[3:]))
    return [
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[head]))]),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[tail]))]),
    ]


class FakeOpenAI:
    """Each ``create`` call plays the next scripted response (a list of chunks, or an exception to raise)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.closed = False

    async def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": copy.deepcopy(kwargs["messages"])})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response

        async def stream():
            for chunk in response:
                yield chunk

        return stream()

    async def close(self):
        self.closed = True


@pytest.fixture
def tools(monkeypatch):
    """Replace the registry's executor: record the calls, answer from a dict (or raise what is given)."""
    calls, results = [], {}

    async def fake_call_tool(api, name, args):
        calls.append((name, args, api.default_user_id, api.workspace_id))
        outcome = results.get(name, {"ok": True})
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(mod, "call_tool", fake_call_tool)
    return SimpleNamespace(calls=calls, results=results)


def backend(client, **cfg):
    return OpenAIToolsBackend(OpenAIConfig(skill_path="/nonexistent/SKILL.md", **cfg), client=client)


async def collect(b, message="ciao", session="s1", user="u1", workspace_id=None):
    return [ev async for ev in b.run_turn(session, user, message, workspace_id)]


def types(events):
    return [e["type"] for e in events]


# ----------------------------------------------------------------------------- the loop

def test_a_plain_answer_streams_text_and_finishes():
    client = FakeOpenAI(text("Ciao ", "Mirko"))
    events = run(collect(backend(client)))
    assert types(events) == ["text_delta", "text_delta", "done"]
    assert events[-1] == {"type": "done", "text": "Ciao Mirko", "ui": None}
    first = client.requests[0]
    assert first["stream"] is True and first["messages"][0]["role"] == "system"
    assert "u1" in first["messages"][0]["content"]  # the prompt names the user the tools default to


def test_the_tools_are_the_registry_ones():
    client = FakeOpenAI(text("ok"))
    run(collect(backend(client)))
    names = {t["function"]["name"] for t in client.requests[0]["tools"]}
    assert {"list_transactions", "spending_report", "get_investments", "render_component"} <= names


def test_a_tool_call_is_run_and_its_result_goes_back_to_the_model(tools):
    tools.results["spending_report"] = {"total": 120.5}
    client = FakeOpenAI(
        text("Vediamo.") + call(0, "spending_report", {"start_date": "2026-01-01", "end_date": "2026-01-31"}, "c1"),
        text("Hai speso 120,50 €"),
    )
    events = run(collect(backend(client), workspace_id=7, user="u9"))

    assert types(events) == ["text_delta", "status", "text_delta", "text_delta", "done"]
    assert events[1] == {"type": "status", "tool": "spending_report"}
    assert events[2]["text"] == "\n\n"  # the paragraph break between the text before and after a tool call
    assert events[-1]["text"] == "Vediamo.\n\nHai speso 120,50 €"
    assert tools.calls == [("spending_report", {"start_date": "2026-01-01", "end_date": "2026-01-31"}, "u9", 7)]

    second = client.requests[1]["messages"]
    assert second[-2]["tool_calls"][0]["id"] == "c1"
    assert second[-1] == {"role": "tool", "tool_call_id": "c1", "content": '{"total": 120.5}'}


def test_render_component_becomes_a_ui_event_and_not_a_status(tools):
    args = {"type": "SpendingPie", "title": "Spese", "action": {"service": "financialSummary", "method": "getSpendingDistribution", "params": {}}}
    client = FakeOpenAI(call(0, "render_component", args), text("Ecco."))
    events = run(collect(backend(client)))
    assert types(events) == ["ui_component", "text_delta", "done"]
    comp = events[0]["component"]
    assert comp["type"] == "SpendingPie" and comp["order"] == 0 and comp["action"]["service"] == "financialSummary"
    assert events[-1]["ui"] == {"text": "", "components": [comp]}


def test_a_failing_tool_is_reported_to_the_model_which_can_go_on(tools):
    tools.results["get_balance"] = ToolError("API error 500: boom")
    client = FakeOpenAI(call(0, "get_balance", {}, "c1"), text("Non riesco a leggere il saldo."))
    events = run(collect(backend(client)))
    assert types(events)[-1] == "done"
    assert client.requests[1]["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": "Error: API error 500: boom"}


def test_invalid_arguments_are_reported_without_calling_the_tool(tools):
    client = FakeOpenAI(call(0, "get_balance", "{not json", "c1"), text("Riprovo."))
    run(collect(backend(client)))
    assert tools.calls == []
    assert client.requests[1]["messages"][-1]["content"].startswith("Error: invalid arguments")


def test_several_tool_calls_in_one_reply_all_get_a_result(tools):
    client = FakeOpenAI(call(0, "get_balance", {}, "a") + call(1, "list_accounts", {}, "b"), text("Fatto."))
    run(collect(backend(client)))
    results = [m for m in client.requests[1]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in results] == ["a", "b"]


def test_a_long_tool_result_is_cut(tools):
    tools.results["list_transactions"] = "x" * 50_000
    client = FakeOpenAI(call(0, "list_transactions", {}, "c1"), text("ok"))
    run(collect(backend(client)))
    content = client.requests[1]["messages"][-1]["content"]
    assert len(content) < 21_000 and "truncated" in content


# ----------------------------------------------------------------------------- failures

def test_a_client_error_ends_the_turn_and_leaves_no_dangling_tool_calls(tools):
    client = FakeOpenAI(call(0, "get_balance", {}, "c1"), RuntimeError("rate limited"), text("Eccomi"))
    b = backend(client)
    events = run(collect(b, "primo"))
    assert types(events) == ["status", "error"] and "rate limited" in events[-1]["message"]

    # the next message starts clean: no assistant tool_calls without their result are sent again
    events = run(collect(b, "secondo"))
    assert events[-1]["text"] == "Eccomi"
    sent = [m for m in client.requests[2]["messages"] if m["role"] != "system"]
    assert sent == [{"role": "user", "content": "secondo"}] + [m for m in sent[1:]]
    assert not any(m["role"] == "tool" for m in sent)


def test_a_model_that_never_stops_calling_tools_is_cut_off(tools):
    client = FakeOpenAI(*[call(0, "get_balance", {}, f"c{i}") for i in range(5)])
    events = run(collect(backend(client, max_steps=3)))
    assert types(events)[-1] == "error" and "3 tool steps" in events[-1]["message"]
    assert len(client.requests) == 3


def test_a_missing_api_key_is_an_error_event(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    events = run(collect(OpenAIToolsBackend(OpenAIConfig(skill_path="/nonexistent/SKILL.md"))))
    assert types(events) == ["error"] and "OPENAI_API_KEY" in events[0]["message"]


# ----------------------------------------------------------------------------- sessions

def test_the_conversation_is_kept_between_turns():
    client = FakeOpenAI(text("Uno"), text("Due"))
    b = backend(client)
    run(collect(b, "primo"))
    run(collect(b, "secondo"))
    roles = [m["role"] for m in client.requests[1]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_another_project_starts_a_fresh_conversation():
    client = FakeOpenAI(text("Uno"), text("Due"))
    b = backend(client)
    run(collect(b, "primo", workspace_id=1))
    run(collect(b, "secondo", workspace_id=2))
    assert [m["role"] for m in client.requests[1]["messages"]] == ["system", "user"]


def test_old_exchanges_are_dropped_at_a_user_message():
    client = FakeOpenAI(*[text(f"r{i}") for i in range(6)])
    b = backend(client, max_messages=4)
    for i in range(6):
        run(collect(b, f"m{i}"))
    kept = b._sessions["s1"].messages
    assert len(kept) <= 4 and kept[0]["role"] == "user"


def test_the_client_is_closed_with_the_backend():
    client = FakeOpenAI()
    run(backend(client).aclose())
    assert client.closed


def test_the_factory_builds_it():
    assert isinstance(create_backend("openai"), OpenAIToolsBackend)
