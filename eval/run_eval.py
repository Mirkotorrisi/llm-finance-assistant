"""Measure the blueprint classifier against eval/gold.csv with the configured LLM provider.

    PYTHONPATH=. uv run python eval/run_eval.py                       # claude, default model (haiku)
    FINANCE_CLAUDE_MODEL=sonnet PYTHONPATH=. uv run python eval/run_eval.py
    FINANCE_LLM_PROVIDER=openai PYTHONPATH=. uv run python eval/run_eval.py

This calls the real model (your Claude login or your OpenAI key). Use it when changing the prompt, the model or the
batch size, to see whether accuracy moved.
"""

import asyncio
import csv
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from scoring import UNKNOWN, score  # noqa: E402
from src.llm import get_llm_client  # noqa: E402
from src.llm.classify import classify  # noqa: E402


async def main() -> None:
    blueprint = json.loads((HERE / "blueprint_personal_it.json").read_text())
    rows = [
        {"description": r["description"], "amount": float(r["amount"]), "expected": r["expected"]}
        for r in csv.DictReader((HERE / "gold.csv").open())
    ]
    llm = get_llm_client()
    started = time.time()
    answers = await classify(llm, rows, blueprint)
    elapsed = time.time() - started

    results = []
    for i, row in enumerate(rows):
        answer = answers.get(i)
        results.append({
            "description": row["description"],
            "expected": row["expected"],
            "predicted": (answer.category or UNKNOWN) if answer else "(no answer)",
            "confidence": answer.confidence if answer else 0.0,
        })
    report = score(results)

    print(f"provider: {llm.name}  model: {getattr(llm, 'model', '?')}  rows: {report['n']}  time: {elapsed:.1f}s")
    print(f"accuracy: {report['accuracy']:.1%}   answered (non-ambiguous rows): {report['answered_share']:.1%}")
    print(f"confident (>=0.8): {report['confident_share']:.1%} of rows, accuracy {report['confident_accuracy']}")
    print("by confidence:", *(f"{b['range']}: n={b['n']} acc={b['accuracy']}" for b in report["buckets"]), sep="\n  ")
    print(f"\nconfident errors ({len(report['confident_errors'])}):")
    for r in report["confident_errors"]:
        print(f"  {r['confidence']:.2f}  {r['description']}: expected {r['expected']}, got {r['predicted']}")
    other = [r for r in report["errors"] if r not in report["confident_errors"]]
    print(f"\nother errors ({len(other)}):")
    for r in other:
        print(f"  {r['confidence']:.2f}  {r['description']}: expected {r['expected']}, got {r['predicted']}")


if __name__ == "__main__":
    asyncio.run(main())
