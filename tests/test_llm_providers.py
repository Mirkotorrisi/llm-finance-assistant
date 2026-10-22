"""Tests for the LLM provider layer and the batch jobs that use it. No network, no subscription use."""

import asyncio
import json
import stat
import sys
import textwrap

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from src.llm import LLMClient, LLMError, create_llm_client
from src.llm.categorize import categorize_orders, categorize_transactions
from src.llm.claude_client import ClaudeClient
from src.llm.openai_client import OpenAIClient
from src.services.amazon_order_parser import AmazonOrderParser
from src.services.transaction_parser import TransactionParser


def run(coro):
    return asyncio.run(coro)


class Items(BaseModel):
    items: list[int]


# --------------------------------------------------------------------------- fake `claude`

FAKE_CLAUDE = textwrap.dedent(
    """\
    #!{python}
    import json, os, sys, time

    prompt = sys.stdin.read()
    log = os.environ["FAKE_LOG"]
    with open(log, "a") as f:
        f.write(json.dumps({{"event": "start", "t": time.time(), "argv": sys.argv[1:], "prompt": prompt}}) + "\\n")
    time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
    with open(log, "a") as f:
        f.write(json.dumps({{"event": "end", "t": time.time()}}) + "\\n")
    mode = os.environ.get("FAKE_MODE", "ok")
    if mode == "garbage":
        print("not json")
    elif mode == "error":
        print(json.dumps({{"is_error": True, "result": "rate limited"}}))
    elif mode == "nostructured":
        print(json.dumps({{"is_error": False, "result": "hi"}}))
    else:
        print(json.dumps({{"is_error": False, "result": "Esselunga is a supermarket.",
                          "structured_output": json.loads(os.environ.get("FAKE_STRUCTURED", '{{"items": [1, 2]}}'))}}))
    """
)


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    script = tmp_path / "fake-claude"
    script.write_text(FAKE_CLAUDE.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_LOG", str(log))

    def records():
        return [json.loads(line) for line in log.read_text().splitlines()]

    return str(script), records


def test_claude_structured_returns_validated_model_and_locks_down_tools(fake_claude):
    bin_, records = fake_claude
    result = run(ClaudeClient(bin=bin_).structured("be brief", "the user text", Items))
    assert result == Items(items=[1, 2])
    call = records()[0]
    argv = call["argv"]
    assert call["prompt"] == "the user text"
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--system-prompt") + 1] == "be brief"
    assert json.loads(argv[argv.index("--json-schema") + 1])["properties"]["items"]["type"] == "array"
    assert "--bare" not in argv and "--no-session-persistence" in argv


@pytest.mark.parametrize(
    "mode,message",
    [("garbage", "invalid output"), ("error", "rate limited"), ("nostructured", "no structured output")],
)
def test_claude_failures_become_llm_errors(fake_claude, monkeypatch, mode, message):
    monkeypatch.setenv("FAKE_MODE", mode)
    with pytest.raises(LLMError, match=message):
        run(ClaudeClient(bin=fake_claude[0]).structured("s", "u", Items))


def test_claude_rejects_output_that_violates_schema(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_STRUCTURED", '{"items": ["not-an-int"]}')
    with pytest.raises(LLMError, match="does not match"):
        run(ClaudeClient(bin=fake_claude[0]).structured("s", "u", Items))


def test_claude_missing_binary_is_reported(tmp_path):
    with pytest.raises(LLMError, match="Cannot start"):
        run(ClaudeClient(bin=str(tmp_path / "nope")).structured("s", "u", Items))


def test_claude_web_search_enables_only_the_search_tool(fake_claude):
    bin_, records = fake_claude
    text = run(ClaudeClient(bin=bin_).web_search("what is Esselunga?"))
    assert text == "Esselunga is a supermarket."
    argv = records()[0]["argv"]
    assert argv[argv.index("--tools") + 1] == "WebSearch"
    assert argv[argv.index("--allowedTools") + 1] == "WebSearch"


def test_claude_concurrency_is_capped(fake_claude, monkeypatch):
    bin_, records = fake_claude
    monkeypatch.setenv("FAKE_SLEEP", "0.4")
    client = ClaudeClient(bin=bin_, concurrency=2)

    async def scenario():
        await asyncio.gather(*(client.structured("s", f"u{i}", Items) for i in range(5)))

    run(scenario())
    live = peak = 0
    for rec in sorted(records(), key=lambda r: r["t"]):
        live += 1 if rec["event"] == "start" else -1
        peak = max(peak, live)
    assert peak == 2


# --------------------------------------------------------------------------- OpenAI provider


class _FakeCompletions:
    def __init__(self, parsed=None, fail=False):
        self.parsed, self.fail, self.kwargs = parsed, fail, None

    async def parse(self, **kwargs):
        self.kwargs = kwargs
        if self.fail:
            raise RuntimeError("boom")
        message = type("M", (), {"parsed": self.parsed})
        return type("C", (), {"choices": [type("Ch", (), {"message": message})]})


def _openai_with(completions):
    client = OpenAIClient(model="m")
    client._client = type("X", (), {"beta": type("B", (), {"chat": type("Ch", (), {"completions": completions})})})
    return client


def test_openai_structured_passes_schema_and_returns_parsed():
    completions = _FakeCompletions(parsed=Items(items=[7]))
    result = run(_openai_with(completions).structured("sys", "usr", Items, temperature=0.3))
    assert result == Items(items=[7])
    assert completions.kwargs["response_format"] is Items and completions.kwargs["temperature"] == 0.3


def test_openai_errors_and_missing_key(monkeypatch):
    with pytest.raises(LLMError, match="OpenAI request failed"):
        run(_openai_with(_FakeCompletions(fail=True)).structured("s", "u", Items))
    with pytest.raises(LLMError, match="no structured output"):
        run(_openai_with(_FakeCompletions(parsed=None)).structured("s", "u", Items))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        run(OpenAIClient().structured("s", "u", Items))


# --------------------------------------------------------------------------- factory


def test_provider_selection_precedence(monkeypatch):
    for var in ("FINANCE_LLM_PROVIDER", "FINANCE_BACKEND"):
        monkeypatch.delenv(var, raising=False)
    assert create_llm_client().name == "claude"
    monkeypatch.setenv("FINANCE_BACKEND", "openai")
    assert create_llm_client().name == "openai"  # one switch moves chat and batch jobs
    monkeypatch.setenv("FINANCE_LLM_PROVIDER", "claude")
    assert create_llm_client().name == "claude"  # explicit override wins
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        create_llm_client("gemini")


# --------------------------------------------------------------------------- batched categorization


class StubLLM(LLMClient):
    """Answers categorization batches with a function of the listed items."""

    name = "stub"

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    async def structured(self, system, user, schema: type[BaseModel], *, temperature=0.0):
        self.calls.append(user)
        return schema.model_validate(self.answer(user))

    async def web_search(self, prompt):
        return f"searched: {prompt}"


def _indices(user: str) -> list[int]:
    return [int(line.split(")")[0]) for line in user.split("Items:\n")[1].splitlines() if ")" in line.split(" ")[0]]


def test_categorize_transactions_is_one_call_per_batch_with_global_indices():
    llm = StubLLM(lambda user: {"items": [{"index": i, "category": " Food "} for i in _indices(user)]})
    txs = [{"description": f"shop {i}", "amount": -1.0} for i in range(45)]
    result = run(categorize_transactions(llm, txs, ["rent"]))
    assert len(llm.calls) == 2  # 40 + 5
    assert result == {i: "food" for i in range(45)}  # lowercased, trimmed, original positions


def test_later_batches_see_categories_discovered_earlier():
    llm = StubLLM(lambda user: {"items": [{"index": i, "category": "groceries"} for i in _indices(user)]})
    run(categorize_transactions(llm, [{"description": f"d{i}", "amount": -1.0} for i in range(41)], []))
    assert "None yet" in llm.calls[0]
    assert "groceries" in llm.calls[1].split("Items:")[0]


def test_categorize_ignores_out_of_range_indices_and_survives_failed_batches():
    llm = StubLLM(lambda user: {"items": [{"index": 0, "category": "a"}, {"index": 99, "category": "bogus"}]})
    assert run(categorize_transactions(llm, [{"description": "x", "amount": 1}], [])) == {0: "a"}

    class Failing(StubLLM):
        async def structured(self, *a, **k):
            raise LLMError("down")

    assert run(categorize_transactions(Failing(None), [{"description": "x", "amount": 1}], [])) == {}


def test_descriptions_are_capped_before_reaching_the_prompt():
    llm = StubLLM(lambda user: {"items": []})
    run(categorize_transactions(llm, [{"description": "A" * 5000 + "\x00", "amount": -1.0}], []))
    assert "A" * 501 not in llm.calls[0] and "\x00" not in llm.calls[0]


def test_amazon_orders_map_back_to_order_ids():
    llm = StubLLM(lambda user: {"items": [{"index": i, "category": ["baby", "books"][i]} for i in _indices(user)]})
    matched = [
        {"order_id": "O1", "products": ["Chicco Passeggino"], "transaction": {"id": 1}},
        {"order_id": "O2", "products": ["Il nome della rosa"], "transaction": {"id": 2}},
    ]
    assert run(AmazonOrderParser.categorize_orders_with_llm(matched, [], llm=llm)) == {"O1": "baby", "O2": "books"}
    assert run(categorize_orders(llm, [["a"]], [])) == {0: "baby"}


# --------------------------------------------------------------------------- statement parsing


def test_csv_rows_use_rules_and_explicit_categories_then_one_batched_call():
    llm = StubLLM(lambda user: {"items": [{"index": i, "category": "dining"} for i in _indices(user)]})
    rows = [
        {"date": "2026-01-01", "description": "NETFLIX.COM", "amount": "-12.99"},  # merchant rule
        {"date": "2026-01-02", "description": "Trattoria", "amount": "-30", "category": "Eating Out"},  # explicit
        {"date": "2026-01-03", "description": "Pizzeria", "amount": "-18"},  # needs the LLM
        {"date": "2026-01-04", "description": "Osteria", "amount": "-22"},  # needs the LLM
        {"description": "no date", "amount": "-1"},  # unparsable
    ]
    out = run(
        TransactionParser.parse_transactions_async(
            rows, [], [{"pattern": "netflix", "category": "subscriptions"}], llm=llm
        )
    )
    assert [t["category"] for t in out] == ["subscriptions", "eating out", "dining", "dining"]
    assert len(llm.calls) == 1 and "Pizzeria" in llm.calls[0] and "NETFLIX" not in llm.calls[0]


def test_csv_falls_back_to_rules_when_the_llm_fails():
    class Down(StubLLM):
        async def structured(self, *a, **k):
            raise LLMError("down")

    rows = [{"date": "2026-01-03", "description": "Grocery Store", "amount": "-18"}]
    out = run(TransactionParser.parse_transactions_async(rows, [], None, llm=Down(None)))
    assert out[0]["category"] == "food"


def test_pdf_chunks_are_parsed_in_parallel_and_report_progress():
    answer = {"transactions": [{"date": "2026-01-01", "description": "X", "amount": -5.0, "category": "misc"}]}
    llm = StubLLM(lambda user: answer)
    progress = []
    chunks = [{"pdf_text": "page text", "pages": "1-2"}, {"pdf_text": "more text", "pages": "3-4"}]
    out = run(
        TransactionParser.parse_transactions_async(
            chunks, [], None, on_chunk_done=lambda done, total: progress.append((done, total)), llm=llm
        )
    )
    assert len(out) == 2 and progress == [(1, 2), (2, 2)]


def test_failed_pdf_chunk_is_skipped_not_fatal():
    class Down(StubLLM):
        async def structured(self, *a, **k):
            raise LLMError("down")

    out = run(TransactionParser.parse_transactions_async([{"pdf_text": "t", "pages": "1"}], [], None, llm=Down(None)))
    assert out == []


# --------------------------------------------------------------------------- search route


def test_search_route_uses_the_configured_provider(monkeypatch):
    from app import app

    monkeypatch.setattr("src.routes.search.get_llm_client", lambda: StubLLM(None))
    res = TestClient(app).post("/search-transaction", json={"description": "AMZN MKTP"})
    assert res.status_code == 200 and res.json()["result"].startswith("searched: ")
    assert "AMZN MKTP" in res.json()["result"]

    class Down(StubLLM):
        async def web_search(self, prompt):
            raise LLMError("no key")

    monkeypatch.setattr("src.routes.search.get_llm_client", lambda: Down(None))
    bad = TestClient(app).post("/search-transaction", json={"description": "x"})
    assert bad.status_code == 500 and "no key" in bad.json()["detail"]
