"""Classify transactions into the categories of a project's blueprint.

The model may only answer with one of the blueprint's assignable categories (leaves of the tree) or ``unknown``;
the answer carries a confidence. Nothing here decides what is *certain*: every answer is a suggestion until the user
confirms it (review queue) or a rule they made matches.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, create_model

from src.llm.base import LLMClient, LLMError

logger = logging.getLogger(__name__)

BATCH_SIZE = 40
UNKNOWN = "unknown"
# What is stored when the model cannot place a transaction
UNCATEGORIZED = "uncategorized"
MAX_DESCRIPTION = 300


@dataclass
class Classification:
    category: Optional[str]  # a blueprint key, or None when the model answered "unknown" / said nothing
    confidence: float


@dataclass
class Leaf:
    key: str
    path: str
    description: str
    examples: List[str]
    kind: str


def leaf_categories(blueprint: Dict[str, Any]) -> List[Leaf]:
    """The assignable categories: nodes without children, with their path ("Svago › Ristoranti")."""
    leaves: List[Leaf] = []

    def walk(nodes: List[Dict[str, Any]], trail: List[str]) -> None:
        for node in nodes or []:
            here = trail + [node.get("label", node["key"])]
            children = node.get("children") or []
            if children:
                walk(children, here)
            else:
                leaves.append(Leaf(
                    key=node["key"], path=" › ".join(here), description=node.get("description") or "",
                    examples=list(node.get("examples") or []), kind=node.get("kind", "expense"),
                ))

    walk(blueprint.get("categories") or [], [])
    return leaves


def _answer_model(keys: List[str]) -> type[BaseModel]:
    """A response schema whose ``category`` is an enum of the allowed keys (works for both providers)."""
    allowed = Literal[tuple(keys + [UNKNOWN])]  # type: ignore[valid-type]
    item = create_model("ClassificationItem", index=(int, ...), category=(allowed, ...), confidence=(float, ...))
    return create_model("ClassificationAnswer", items=(List[item], ...))  # type: ignore[valid-type]


def _clean(text: str) -> str:
    text = text.strip()[:MAX_DESCRIPTION]
    return "".join(ch for ch in text if ch.isprintable() or ch.isspace())


def _system_prompt() -> str:
    return (
        "You classify bank transactions into the categories of the user's own organisation of their spending. "
        "Answer with the key of exactly one listed category per transaction, or `unknown` when none fits or the text "
        "is too ambiguous to tell: a wrong guess costs the user more than an honest `unknown`. "
        "`confidence` is your honest probability (0-1) that the category is right: 0.9+ only when the merchant is "
        "clearly of that kind, below 0.6 when you are guessing. Amounts are negative for expenses and positive for "
        "income; prefer income categories for money coming in. Follow the owner's instructions."
    )


def _user_prompt(blueprint: Dict[str, Any], leaves: List[Leaf], listing: List[str]) -> str:
    lines = []
    for leaf in leaves:
        extra = f" — {leaf.description}" if leaf.description else ""
        examples = f" (e.g. {', '.join(leaf.examples[:5])})" if leaf.examples else ""
        income = " [income]" if leaf.kind == "income" else ""
        lines.append(f"- {leaf.key}: {leaf.path}{income}{extra}{examples}")
    instructions = (blueprint.get("instructions") or "").strip()
    owner = f"Owner's instructions:\n{instructions}\n\n" if instructions else ""
    return (
        f"{owner}Categories (answer with the key before the colon):\n" + "\n".join(lines)
        + "\n\nTransactions:\n" + "\n".join(listing)
        + "\n\nReturn one entry per transaction, using its number as `index`."
    )


async def classify(
    llm: LLMClient, transactions: List[Dict[str, Any]], blueprint: Dict[str, Any], batch_size: int = BATCH_SIZE
) -> Dict[int, Classification]:
    """``transactions`` need ``description`` and ``amount``. Returns ``{position: Classification}`` for those answered.

    Batches run in sequence; a failed batch is skipped (its transactions stay unclassified for the caller to handle).
    """
    leaves = leaf_categories(blueprint)
    if not leaves or not transactions:
        return {}
    allowed = {leaf.key for leaf in leaves}
    schema = _answer_model([leaf.key for leaf in leaves])
    result: Dict[int, Classification] = {}
    for start in range(0, len(transactions), batch_size):
        batch = transactions[start:start + batch_size]
        listing = [
            f"{start + offset}) {_clean(str(t['description']))} | amount: {t['amount']}"
            for offset, t in enumerate(batch)
        ]
        try:
            answer = await llm.structured(_system_prompt(), _user_prompt(blueprint, leaves, listing), schema, temperature=0.0)
        except LLMError as e:
            logger.warning("Classification batch %d-%d failed: %s", start, start + len(batch) - 1, e)
            continue
        valid = range(start, start + len(batch))
        for item in answer.items:  # type: ignore[attr-defined]
            if item.index not in valid:
                continue
            confidence = max(0.0, min(1.0, float(item.confidence)))
            if item.category == UNKNOWN or item.category not in allowed:
                result[item.index] = Classification(None, min(confidence, 0.3))
            else:
                result[item.index] = Classification(item.category, confidence)
    return result
