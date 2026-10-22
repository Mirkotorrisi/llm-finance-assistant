"""Deciding the category of imported transactions: rules the user confirmed, then the bank's own column, then the
model constrained to the project's blueprint. Every automatic answer is a suggestion (``reviewed`` stays false)."""

import logging
from typing import Any, Dict, List, Optional

from src.llm import LLMClient
from src.llm.categorize import categorize_transactions
from src.llm.classify import UNCATEGORIZED, Classification, classify, leaf_categories
from src.services.transaction_parser import TransactionParser

logger = logging.getLogger(__name__)


async def _blueprint(api) -> Dict[str, Any]:
    try:
        return (await api.call_tool("get_blueprint", {}))["blueprint"]
    except Exception as e:  # noqa: BLE001 - classification still works without the blueprint
        logger.warning("Could not read the blueprint: %s", e)
        return {}


async def classify_for_import(transactions: List[Dict[str, Any]], api, llm: LLMClient) -> List[Dict[str, Any]]:
    """Return the transactions with ``category``, ``category_source`` and ``confidence`` set.

    Order of authority: a saved merchant rule (``rule``, confirmed) > the file's own category (``file``) > the
    model (``ai``). When the model cannot be reached the row is ``uncategorized`` (``fallback``) in a project with a
    blueprint, or gets the old keyword guess in a project without one.
    """
    if not transactions:
        return []
    resolved = await api.call_tool("resolve_merchants", {"descriptions": [t["description"] for t in transactions]})

    rows: List[Dict[str, Any]] = [dict(t) for t in transactions]
    pending: List[int] = []
    for i, row in enumerate(rows):
        rule_category = resolved[i].get("category") if i < len(resolved) else None
        if rule_category:
            row.update(category=rule_category, category_source="rule", confidence=None)
        elif row.get("category"):
            row.update(category_source="file", confidence=None)
        else:
            pending.append(i)
    if not pending:
        return rows

    blueprint = await _blueprint(api)
    if leaf_categories(blueprint):
        answers = await classify(llm, [rows[i] for i in pending], blueprint)
        for position, i in enumerate(pending):
            answer: Optional[Classification] = answers.get(position)
            if answer is None:
                rows[i].update(category=UNCATEGORIZED, category_source="fallback", confidence=0.0)
            elif answer.category is None:
                rows[i].update(category=UNCATEGORIZED, category_source="ai", confidence=answer.confidence)
            else:
                rows[i].update(category=answer.category, category_source="ai", confidence=answer.confidence)
        return rows

    # A project without a blueprint: free-text categories, as before
    existing = await api.call_tool("get_distinct_categories", {})
    guessed = await categorize_transactions(llm, [rows[i] for i in pending], list(existing))
    for position, i in enumerate(pending):
        if position in guessed:
            rows[i].update(category=guessed[position], category_source="ai", confidence=None)
        else:
            rows[i].update(
                category=TransactionParser.categorize_transaction(rows[i]["description"], rows[i]["amount"]),
                category_source="fallback", confidence=0.0,
            )
    return rows


async def classify_orders(llm: LLMClient, matched: List[Dict[str, Any]], blueprint: Dict[str, Any]) -> Dict[str, Classification]:
    """Amazon orders → blueprint category, from the products bought. ``{order_id: Classification}``; unknowns omitted."""
    rows = [
        {"description": "Amazon order: " + "; ".join(m["products"][:10]), "amount": m["transaction"].get("amount", -1)}
        for m in matched
    ]
    answers = await classify(llm, rows, blueprint)
    return {
        matched[position]["order_id"]: answer for position, answer in answers.items() if answer.category is not None
    }
