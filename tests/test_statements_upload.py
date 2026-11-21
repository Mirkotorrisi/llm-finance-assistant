"""Statement upload flow: POST /statements/upload -> background job -> GET /statements/jobs/{id}.

The finance API and the LLM are replaced by in-memory fakes, so this runs offline.
"""

import io
from collections import Counter

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app import app
from src.api_client import scope_headers
from src.llm import LLMClient, LLMError


class FakeApi:
    """The slice of FinanceApiClient the upload job uses, backed by a list."""

    def __init__(self, rules=None):
        self.transactions: list[dict] = []
        self.rules = rules or []
        self.import_requests: list[dict] = []
        self.scopes: list[dict] = []
        self.blueprint: dict = {}  # no blueprint unless a test sets one

    async def call_tool(self, name, args):
        self.scopes.append(scope_headers())
        if name == "get_distinct_categories":
            return sorted({t["category"] for t in self.transactions})
        if name == "get_merchant_rules":
            return self.rules
        if name == "get_blueprint":
            return {"blueprint": self.blueprint}
        if name == "resolve_merchants":
            found = []
            for description in args["descriptions"]:
                rule = next((r for r in self.rules if r["pattern"].lower() in description.lower()), None)
                found.append({"description": description, "category": rule["category"] if rule else None})
            return found
        if name == "import_statement":
            self.import_requests.append(args)
            stored = Counter((t["date"], t["amount"], t["description"]) for t in self.transactions)
            seen: Counter = Counter()
            added = []
            for row in args["transactions"]:
                key = (row["date"], row["amount"], row["description"])
                seen[key] += 1
                if seen[key] > stored[key]:
                    source = row.get("category_source", "file")
                    added.append({
                        **row, "id": len(self.transactions) + len(added) + 1, "account_id": args["account_id"],
                        "reviewed": source in ("user", "rule"),
                    })
            self.transactions.extend(added)
            return {
                "batch_id": 1 if added else None,
                "row_count": len(args["transactions"]),
                "added_count": len(added),
                "skipped_count": len(args["transactions"]) - len(added),
                "invalid_rows": [],
                "duplicate_file": False,
                "transfers_detected": 0,
                "transactions": added,
            }
        raise AssertionError(f"unexpected tool {name}")


class StubLLM(LLMClient):
    name = "stub"

    def __init__(self, categories=None, fail=False):
        self.categories, self.fail, self.prompts = categories or {}, fail, []

    async def structured(self, system, user, schema: type[BaseModel], *, temperature=0.0):
        self.prompts.append(user)
        if self.fail:
            raise LLMError("provider down")
        constrained = "Transactions:\n" in user  # the blueprint classifier's prompt (vs the free-text categorizer's)
        listing = user.split("Transactions:\n" if constrained else "Items:\n")[1]
        items = []
        for line in listing.splitlines():
            if ")" not in line.split(" ")[0]:
                continue
            index, text = line.split(") ", 1)
            category = next((c for key, c in self.categories.items() if key in text), "unknown" if constrained else "other")
            item = {"index": int(index), "category": category}
            if constrained:
                item["confidence"] = 0.9
            items.append(item)
        return schema.model_validate({"items": items})

    async def web_search(self, prompt):
        return ""


@pytest.fixture
def env(monkeypatch):
    api = FakeApi(rules=[{"pattern": "netflix", "category": "subscriptions"}])
    llm = StubLLM({"Grocery": "groceries", "Salary": "income"})

    async def fake_get_api_client():
        return api

    monkeypatch.setattr("src.routes.statements.get_api_client", fake_get_api_client)
    monkeypatch.setattr("src.services.transaction_parser.get_llm_client", lambda: llm)
    monkeypatch.setattr("src.routes.statements.get_llm_client", lambda: llm)
    return TestClient(app), api, llm


def upload(client, name, content, account_id=1, headers=None):
    res = client.post(
        "/statements/upload",
        files={"file": (name, io.BytesIO(content), "text/csv")},
        data={"account_id": account_id},
        headers=headers,
    )
    assert res.status_code == 200
    return client.get(f"/statements/jobs/{res.json()['job_id']}").json()


CSV = b"""date,description,amount,currency
2026-01-15,Test Grocery,-50.00,EUR
2026-01-16,NETFLIX.COM,-12.99,EUR
2026-01-17,Test Salary,2000.00,EUR"""


def test_upload_categorizes_adds_and_then_skips_duplicates(env):
    client, api, llm = env

    job = upload(client, "statement.csv", CSV)
    assert job["status"] == "complete"
    result = job["result"]
    assert (result["transactions_added"], result["transactions_skipped"]) == (3, 0)
    assert {t["description"]: t["category"] for t in api.transactions} == {
        "Test Grocery": "groceries",  # from the LLM
        "NETFLIX.COM": "subscriptions",  # merchant rule, no LLM involved
        "Test Salary": "income",
    }
    assert len(llm.prompts) == 1 and "NETFLIX" not in llm.prompts[0]  # one batched call for the rest
    assert {t["account_id"] for t in api.transactions} == {1}

    again = upload(client, "statement.csv", CSV)["result"]
    assert (again["transactions_added"], again["transactions_skipped"]) == (0, 3)
    assert len(api.transactions) == 3


def test_rows_fall_back_to_rules_when_the_llm_is_down(env):
    client, api, llm = env
    llm.fail = True

    job = upload(client, "s.csv", b"date,description,amount\n2026-02-01,Grocery Store Roma,-20\n2026-02-02,Uber trip,-9\n")
    assert job["status"] == "complete"
    assert {t["description"]: t["category"] for t in api.transactions} == {
        "Grocery Store Roma": "food",
        "Uber trip": "transport",
    }


def test_empty_statement_completes_with_nothing_added(env):
    client, api, _ = env
    job = upload(client, "empty.csv", b"date,description,amount\n")
    assert job["status"] == "complete" and job["result"]["transactions_added"] == 0
    assert api.transactions == []


def test_unsupported_format_is_reported_on_the_job(env):
    client, _, _ = env
    job = upload(client, "notes.txt", b"this is not a statement")
    assert job["status"] == "error" and "Unsupported file format" in job["error"]


def test_oversized_file_is_refused_before_it_is_buffered(env):
    """The body is read in chunks and refused at the cap (413): nothing is queued, no multi-GB buffer is held."""
    client, _, _ = env
    res = client.post(
        "/statements/upload",
        files={"file": ("big.csv", io.BytesIO(b"date,description,amount\n" * 600_000), "text/csv")},
        data={"account_id": "1"},
    )
    assert res.status_code == 413 and "exceeds maximum limit" in res.json()["detail"]


def test_unknown_job_is_404(env):
    client, _, _ = env
    assert client.get("/statements/jobs/does-not-exist").status_code == 404


def test_the_account_is_required(env):
    client, _, _ = env
    res = client.post("/statements/upload", files={"file": ("s.csv", io.BytesIO(CSV), "text/csv")})
    assert res.status_code == 422  # a statement without an account cannot be told apart from another bank's


def test_the_import_summary_reaches_the_client(env):
    client, api, _ = env
    result = upload(client, "statement.csv", CSV, account_id=7)["result"]
    assert api.import_requests[0]["account_id"] == 7 and len(api.import_requests[0]["file_hash"]) == 64
    assert {"batch_id", "transfers_detected", "duplicate_file", "invalid_rows"} <= set(result)


def test_workspace_and_user_are_passed_on_to_every_api_call(env):
    client, api, _ = env
    upload(client, "statement.csv", CSV, headers={"X-Workspace-Id": "5", "X-User-Id": "alice"})
    assert api.scopes and all(s == {"X-Workspace-Id": "5", "X-User-Id": "alice"} for s in api.scopes)
    api.scopes.clear()
    upload(client, "statement.csv", CSV)  # no headers: the API picks the default project
    assert all(s == {} for s in api.scopes)


def test_an_api_rejection_becomes_a_readable_job_error(env, monkeypatch):
    import httpx

    client, api, _ = env

    async def reject(name, args):
        if name == "import_statement":
            request = httpx.Request("POST", "http://api/api/imports")
            raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, json={"detail": "Account 9 not found in this workspace"}, request=request))
        return []

    monkeypatch.setattr(api, "call_tool", reject)
    job = upload(client, "statement.csv", CSV, account_id=9)
    assert job["status"] == "error" and job["error"] == "Account 9 not found in this workspace"


BLUEPRINT = {
    "instructions": "Pizzerie e trattorie sono minchiate.",
    "categories": [
        {"key": "casa", "label": "Casa", "children": [{"key": "spesa", "label": "Spesa", "examples": ["Coop"]}]},
        {"key": "svago", "label": "Svago", "children": [{"key": "ristoranti", "label": "Ristoranti"}]},
        {"key": "stipendio", "label": "Stipendio", "kind": "income"},
    ],
}


def test_with_a_blueprint_the_model_is_limited_to_its_categories_and_answers_wait_for_review(env):
    client, api, llm = env
    api.blueprint = BLUEPRINT
    llm.categories = {"Grocery": "spesa", "Salary": "stipendio"}
    job = upload(client, "statement.csv", CSV)
    assert job["status"] == "complete"
    sent = {r["description"]: r for r in api.import_requests[0]["transactions"]}
    assert (sent["NETFLIX.COM"]["category"], sent["NETFLIX.COM"]["category_source"]) == ("subscriptions", "rule")  # confirmed rule
    assert (sent["Test Grocery"]["category"], sent["Test Grocery"]["category_source"]) == ("spesa", "ai")
    assert sent["Test Grocery"]["confidence"] is not None
    assert job["result"]["needs_review"] == 2  # the two model answers; the rule is already confirmed
