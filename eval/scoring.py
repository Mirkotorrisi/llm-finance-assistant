"""Scoring of the classifier against hand-labelled transactions. Pure functions: no LLM, no I/O."""

from typing import Any, Dict, List

UNKNOWN = "unknown"
CONFIDENT = 0.8
BUCKETS = ((0.0, 0.5), (0.5, 0.8), (0.8, 0.95), (0.95, 1.01))


def score(results: List[Dict[str, Any]], confident_at: float = CONFIDENT) -> Dict[str, Any]:
    """``results``: dicts with ``description``, ``expected``, ``predicted`` ("unknown" when none) and ``confidence``."""
    n = len(results)
    correct = [r for r in results if r["predicted"] == r["expected"]]
    confident = [r for r in results if r["confidence"] >= confident_at]
    confident_ok = [r for r in confident if r["predicted"] == r["expected"]]

    buckets = []
    for low, high in BUCKETS:
        rows = [r for r in results if low <= r["confidence"] < high]
        buckets.append({
            "range": f"{low:.2f}-{min(high, 1.0):.2f}",
            "n": len(rows),
            "accuracy": round(sum(r["predicted"] == r["expected"] for r in rows) / len(rows), 3) if rows else None,
        })

    known = [r for r in results if r["expected"] != UNKNOWN]
    return {
        "n": n,
        "accuracy": round(len(correct) / n, 3) if n else 0.0,
        "answered_share": round(sum(r["predicted"] != UNKNOWN for r in known) / len(known), 3) if known else 0.0,
        "confident_share": round(len(confident) / n, 3) if n else 0.0,
        "confident_accuracy": round(len(confident_ok) / len(confident), 3) if confident else None,
        "confident_errors": [r for r in confident if r["predicted"] != r["expected"]],
        "errors": [r for r in results if r["predicted"] != r["expected"]],
        "buckets": buckets,
    }
