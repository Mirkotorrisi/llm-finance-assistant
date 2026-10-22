"""The scoring used by eval/run_eval.py (the model itself is never called here)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

from scoring import score  # noqa: E402


def r(expected, predicted, confidence, description="x"):
    return {"description": description, "expected": expected, "predicted": predicted, "confidence": confidence}


def test_accuracy_confidence_and_errors():
    results = [
        r("a", "a", 0.95), r("b", "b", 0.9), r("c", "x", 0.9, "confident miss"), r("d", "d", 0.6),
        r("e", "x", 0.4), r("unknown", "unknown", 0.2), r("f", "unknown", 0.2),
    ]
    report = score(results)
    assert report["n"] == 7 and report["accuracy"] == round(4 / 7, 3)
    assert report["confident_share"] == round(3 / 7, 3) and report["confident_accuracy"] == round(2 / 3, 3)
    assert [e["description"] for e in report["confident_errors"]] == ["confident miss"]
    assert report["answered_share"] == round(5 / 6, 3)  # 6 rows should be answered; all but "f" got a category (right or wrong)
    assert {b["range"]: b["n"] for b in report["buckets"]}["0.80-0.95"] == 2


def test_empty_input_does_not_divide_by_zero():
    report = score([])
    assert report["n"] == 0 and report["accuracy"] == 0.0 and report["confident_accuracy"] is None
