"""Batched categorization: many items per LLM call instead of one call per item.

Batches run one after the other and share the categories discovered so far, so the
model reuses labels instead of inventing near-duplicates.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from src.llm.base import LLMClient, LLMError

logger = logging.getLogger(__name__)

# Descriptions come from untrusted files: cap them before they reach a prompt.
MAX_DESCRIPTION_LENGTH = 500
TRANSACTION_BATCH_SIZE = 40
ORDER_BATCH_SIZE = 20

_RULES = """Rules:
1. If an item clearly fits an existing category, use that category exactly as written.
2. Only create a NEW category if nothing existing fits.
3. Labels are short (1-2 words), lowercase and descriptive."""


class _IndexedCategory(BaseModel):
    index: int
    category: str


class _IndexedCategories(BaseModel):
    items: list[_IndexedCategory]


def _clean(text: str) -> str:
    text = text.strip()[:MAX_DESCRIPTION_LENGTH]
    return "".join(ch for ch in text if ch.isprintable() or ch.isspace())


def _categories_text(existing: list[str]) -> str:
    return ", ".join(existing) if existing else "None yet - create appropriate categories"


async def _categorize(
    llm: LLMClient, system: str, lines: list[str], existing_categories: list[str], batch_size: int
) -> dict[int, str]:
    """Core loop. ``lines[i]`` describes item ``i``; returns ``{i: category}`` for the items answered."""
    known = list(existing_categories)
    result: dict[int, str] = {}
    for start in range(0, len(lines), batch_size):
        batch = lines[start : start + batch_size]
        listing = "\n".join(f"{start + offset}) {line}" for offset, line in enumerate(batch))
        user = (
            f"Existing categories: {_categories_text(known)}\n\n"
            f"Items:\n{listing}\n\n"
            "Return one entry per item, using the item's number as `index`."
        )
        try:
            parsed = await llm.structured(system, user, _IndexedCategories, temperature=0.2)
        except LLMError as e:
            logger.warning("Categorization batch %d-%d failed: %s", start, start + len(batch) - 1, e)
            continue
        valid = range(start, start + len(batch))
        for item in parsed.items:
            category = item.category.lower().strip()
            if item.index in valid and category:
                result[item.index] = category
                if category not in known:
                    known.append(category)
    return result


async def categorize_transactions(
    llm: LLMClient, transactions: list[dict], existing_categories: list[str]
) -> dict[int, str]:
    """``transactions`` items need ``description`` and ``amount``. Returns ``{position: category}``."""
    system = (
        "You are a financial transaction categorization expert. Assign a category to each bank transaction "
        "from its description and amount (negative = expense, positive = income).\n" + _RULES
    )
    lines = [
        f"{_clean(str(t['description']))} | amount: {t['amount']} | {'income' if t['amount'] > 0 else 'expense'}"
        for t in transactions
    ]
    return await _categorize(llm, system, lines, existing_categories, TRANSACTION_BATCH_SIZE)


async def categorize_orders(
    llm: LLMClient, orders: list[list[str]], existing_categories: list[str]
) -> dict[int, str]:
    """``orders`` is one list of product names per Amazon order. Returns ``{position: category}``."""
    system = (
        "You are a financial transaction categorization expert. Assign a single category to each Amazon order "
        "from the products purchased.\n" + _RULES + "\n"
        "4. Prefer a specific category (groceries, baby, electronics, clothing, home, health, books, toys) "
        "over a generic 'shopping' whenever the products suggest one.\n"
        "5. If an order mixes unrelated products, choose the category of the most dominant/expensive item."
    )
    lines = ["; ".join(_clean(p) for p in products[:20]) for products in orders]
    return await _categorize(llm, system, lines, existing_categories, ORDER_BATCH_SIZE)
