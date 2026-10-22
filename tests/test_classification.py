"""Blueprint-constrained classification, and how an import decides each category."""

import asyncio
from typing import Type

import pytest
from pydantic import BaseModel

from src.llm import LLMClient, LLMError
from src.llm.classify import UNCATEGORIZED, classify, leaf_categories
from src.services.classification import classify_for_import, classify_orders
from src.services.transaction_parser import TransactionParser

BLUEPRINT = {
    "instructions": "Pannolini e scuola sono spese dei figli.",
    "categories": [
        {"key": "casa", "label": "Casa", "children": [
            {"key": "spesa", "label": "Spesa", "description": "Supermercato", "examples": ["Coop", "Esselunga"]},
            {"key": "utenze", "label": "Utenze"},
        ]},
        {"key": "figli", "label": "Figli"},
        {"key": "stipendio", "label": "Stipendio", "kind": "income"},
    ],
}


def run(coro):
    return asyncio.run(coro)


class ScriptedLLM(LLMClient):
    """Answers each listed transaction from a {text fragment: (category, confidence)} table."""

    name = "scripted"

    def __init__(self, table=None, fail=False, raw=None):
        self.table, self.fail, self.raw = table or {}, fail, raw
        self.calls: list[dict] = []

    async def structured(self, system, user, schema: Type[BaseModel], *, temperature=0.0):
        self.calls.append({"system": system, "user": user, "schema": schema.model_json_schema()})
        if self.fail:
            raise LLMError("down")
        if self.raw is not None:
            return schema.model_validate(self.raw)
        items = []
        for line in user.split("Transactions:\n")[1].splitlines():
            if ")" not in line.split(" ")[0]:
                continue
            index, text = line.split(") ", 1)
            category, confidence = next((v for k, v in self.table.items() if k in text), ("unknown", 0.2))
            items.append({"index": int(index), "category": category, "confidence": confidence})
        return schema.model_validate({"items": items})

    async def web_search(self, prompt):
        return ""


class FakeApi:
    def __init__(self, rules=None, blueprint=None, categories=None):
        self.rules, self.blueprint, self.categories = rules or [], BLUEPRINT if blueprint is None else blueprint, categories or []
        self.resolve_calls = 0

    async def call_tool(self, name, args):
        if name == "resolve_merchants":
            self.resolve_calls += 1
            out = []
            for d in args["descriptions"]:
                rule = next((r for r in self.rules if r["pattern"] in d.lower()), None)
                out.append({"description": d, "category": rule["category"] if rule else None})
            return out
        if name == "get_blueprint":
            return {"blueprint": self.blueprint}
        if name == "get_distinct_categories":
            return self.categories
        raise AssertionError(name)


def tx(description, amount=-10.0, category=None):
    return {"date": "2026-03-01", "description": description, "amount": amount, "currency": "EUR", "category": category}


# ----------------------------------------------------------------------------- the classifier

def test_leaves_are_the_assignable_categories_with_their_path():
    leaves = {l.key: l for l in leaf_categories(BLUEPRINT)}
    assert set(leaves) == {"spesa", "utenze", "figli", "stipendio"}  # "casa" has children, so it is not assignable
    assert leaves["spesa"].path == "Casa › Spesa" and leaves["stipendio"].kind == "income"


def test_the_model_can_only_answer_with_blueprint_keys_and_sees_the_owners_rules():
    llm = ScriptedLLM({"COOP": ("spesa", 0.95)})
    answers = run(classify(llm, [tx("COOP LOMBARDIA")], BLUEPRINT))
    schema = llm.calls[0]["schema"]
    assert sorted(schema["$defs"]["ClassificationItem"]["properties"]["category"]["enum"]) == ["figli", "spesa", "stipendio", "unknown", "utenze"]
    prompt = llm.calls[0]["user"]
    assert "Pannolini e scuola sono spese dei figli." in prompt and "- spesa: Casa › Spesa — Supermercato (e.g. Coop, Esselunga)" in prompt
    assert "[income]" in prompt and "casa:" not in prompt  # the parent is not offered
    assert (answers[0].category, answers[0].confidence) == ("spesa", 0.95)


def test_unknown_answers_have_no_category_and_capped_confidence():
    answers = run(classify(ScriptedLLM(raw={"items": [{"index": 0, "category": "unknown", "confidence": 0.99}]}), [tx("???")], BLUEPRINT))
    assert answers[0].category is None and answers[0].confidence == 0.3


def test_confidence_is_clamped_and_out_of_range_indices_ignored():
    raw = {"items": [{"index": 0, "category": "spesa", "confidence": 7}, {"index": 99, "category": "spesa", "confidence": 0.5}]}
    answers = run(classify(ScriptedLLM(raw=raw), [tx("a")], BLUEPRINT))
    assert list(answers) == [0] and answers[0].confidence == 1.0


def test_batches_use_global_positions_and_a_failed_batch_is_skipped():
    llm = ScriptedLLM({"x": ("spesa", 0.9)})
    answers = run(classify(llm, [tx(f"x{i}") for i in range(45)], BLUEPRINT))
    assert len(llm.calls) == 2 and sorted(answers) == list(range(45))
    assert run(classify(ScriptedLLM(fail=True), [tx("a")], BLUEPRINT)) == {}
    assert run(classify(ScriptedLLM(), [tx("a")], {"categories": []})) == {}  # nothing assignable: no call at all


def test_untrusted_descriptions_are_capped():
    llm = ScriptedLLM()
    run(classify(llm, [tx("A" * 5000 + "\x00")], BLUEPRINT))
    assert "A" * 301 not in llm.calls[0]["user"] and "\x00" not in llm.calls[0]["user"]


# ----------------------------------------------------------------------------- the import decision

def test_rules_beat_the_file_which_beats_the_model():
    api = FakeApi(rules=[{"pattern": "coop", "category": "spesa"}])
    llm = ScriptedLLM({"Bar": ("utenze", 0.8)})
    rows = run(classify_for_import(
        [tx("COOP LOMBARDIA", category="altro"), tx("Pizzeria", category="ristorazione"), tx("Bar Sport")], api, llm))
    assert [(r["category"], r["category_source"]) for r in rows] == [("spesa", "rule"), ("ristorazione", "file"), ("utenze", "ai")]
    assert rows[2]["confidence"] == 0.8 and rows[0]["confidence"] is None
    assert len(llm.calls) == 1 and "Pizzeria" not in llm.calls[0]["user"]  # only what is still undecided goes to the model
    assert api.resolve_calls == 1  # one lookup for the whole file


def test_nothing_goes_to_the_model_when_rules_and_the_file_decide_everything():
    llm = ScriptedLLM()
    rows = run(classify_for_import([tx("Coop", category="x")], FakeApi(), llm))
    assert rows[0]["category_source"] == "file" and llm.calls == []


def test_an_unknown_answer_is_stored_as_uncategorized_and_a_dead_model_as_a_fallback():
    unknown = run(classify_for_import([tx("???")], FakeApi(), ScriptedLLM()))[0]
    assert (unknown["category"], unknown["category_source"], unknown["confidence"]) == (UNCATEGORIZED, "ai", 0.2)
    down = run(classify_for_import([tx("Coop")], FakeApi(), ScriptedLLM(fail=True)))[0]
    assert (down["category"], down["category_source"], down["confidence"]) == (UNCATEGORIZED, "fallback", 0.0)


def test_a_project_without_a_blueprint_keeps_free_text_categories():
    class FreeLLM(ScriptedLLM):
        async def structured(self, system, user, schema, *, temperature=0.0):
            lines = [l for l in user.split("Items:\n")[1].splitlines() if ")" in l.split(" ")[0]]
            return schema.model_validate({"items": [{"index": int(l.split(")")[0]), "category": "groceries"} for l in lines]})

    api = FakeApi(blueprint={"categories": []}, categories=["rent"])
    rows = run(classify_for_import([tx("Coop")], api, FreeLLM()))
    assert (rows[0]["category"], rows[0]["category_source"], rows[0]["confidence"]) == ("groceries", "ai", None)
    # and the old keyword guess when the model is down
    down = run(classify_for_import([tx("Grocery Store")], api, ScriptedLLM(fail=True)))[0]
    assert (down["category"], down["category_source"]) == ("food", "fallback")


def test_a_blueprint_that_cannot_be_read_does_not_break_the_import():
    class NoBlueprint(FakeApi):
        async def call_tool(self, name, args):
            if name == "get_blueprint":
                raise RuntimeError("api down")
            return await super().call_tool(name, args)

    class FreeLLM(ScriptedLLM):
        async def structured(self, system, user, schema, *, temperature=0.0):
            raise LLMError("down")

    row = run(classify_for_import([tx("Grocery Store")], NoBlueprint(), FreeLLM()))[0]
    assert row["category_source"] == "fallback"


# ----------------------------------------------------------------------------- extraction & Amazon

def test_pdf_extraction_does_not_ask_the_model_for_categories():
    class PdfLLM(ScriptedLLM):
        async def structured(self, system, user, schema, *, temperature=0.0):
            self.calls.append({"system": system, "schema": schema.model_json_schema()})
            return schema.model_validate({"transactions": [{"date": "2026-01-03", "description": "COOP", "amount": -23.4}]})

    llm = PdfLLM()
    rows = run(TransactionParser.extract_async([{"pdf_text": "stmt", "pages": "1"}], llm=llm))
    assert rows == [{"date": "2026-01-03", "description": "COOP", "amount": -23.4, "currency": "EUR"}]
    assert "category" not in llm.calls[0]["schema"]["$defs"]["_ExtractedTransaction"]["properties"]
    assert "Do not categorize" in llm.calls[0]["system"]


def test_csv_extraction_keeps_the_files_own_category_column():
    rows = run(TransactionParser.extract_async([
        {"date": "2026-01-03", "description": "Coop", "amount": "-5", "category": "Alimentari"},
        {"date": "2026-01-04", "description": "Bar", "amount": "-2"},
        {"description": "no date", "amount": "-1"},
    ]))
    assert [(r["description"], r["category"]) for r in rows] == [("Coop", "alimentari"), ("Bar", None)]


def test_amazon_orders_are_classified_from_the_products_and_unknowns_are_left_out():
    llm = ScriptedLLM({"Pannolini": ("figli", 0.9)})
    matched = [
        {"order_id": "O1", "products": ["Pannolini Pampers"], "transaction": {"id": 1, "amount": -30}},
        {"order_id": "O2", "products": ["Oggetto misterioso"], "transaction": {"id": 2, "amount": -12}},
    ]
    result = run(classify_orders(llm, matched, BLUEPRINT))
    assert list(result) == ["O1"] and result["O1"].category == "figli"
    assert "Amazon order: Pannolini Pampers" in llm.calls[0]["user"]
